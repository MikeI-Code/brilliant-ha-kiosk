#!/usr/bin/env python3
"""Opt-in voice / video capture with a published API (KIOSK_CAPTURE=1 in kiosk.env).

Runs on the panel with Brilliant's Python (/data/switch-embedded/env/bin/python). No extra packages.

Nothing listens all the time. The mic (and, with CAPTURE_VIDEO=1, the camera) is opened only for an
explicit recording, started on the panel's Capture screen or by a Home Assistant command, and closed
right after. Each clip is published so anyone can build their own automation on it; this project
ships no Home Assistant logic.

  Clips        /run/ha-kiosk-capture (tmpfs), deleted after CAPTURE_TTL seconds, 16 MB in total.
  Clip server  http://<panel-lan-ip>:CAPTURE_PORT/clips/<token>.<ext>, GET only, and only for the
               hosts in CAPTURE_ALLOW (default: the host in HA_URL). A runtime nftables rule opens the
               port for those hosts; it is removed when the service stops.
  MQTT         event   Capture        <base>/capture/event   audio_clip | snapshot | video_clip | capture_error
               sensor  Capture state  <base>/capture/state   idle | recording | processing | error
               command <base>/capture/command  {"action":"record","kind":"audio|snapshot|video","secs":N,
                                                "request_id":"..."} | {"action":"stop"} | {"action":"cancel"}
               reply   <base>/capture/reply    {"text":"...","clear_after":30}  (shown on the screen)
  Local UI     127.0.0.1:CAPTURE_PORT+1  GET /status /start?kind=audio|video /stop /snapshot /cancel
"""
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

sys.path.insert(0, "/etc/ha-kiosk")

ENV = os.environ
RATE, CHUNK = 16000, 1024                      # 16 kHz mono S16_LE, 64 ms chunks
PORT = int(ENV.get("CAPTURE_PORT", "10720"))
_DENY = ("0", "off", "false", "no")
MIC_DENIED = ENV.get("ALLOW_MIC", "").strip().lower() in _DENY        # privacy lock (deny wins)
CAM_DENIED = ENV.get("ALLOW_CAMERA", "").strip().lower() in _DENY
AUDIO = not MIC_DENIED
VIDEO = ENV.get("CAPTURE_VIDEO", "0") == "1" and not CAM_DENIED
MAX_SECS = max(1.0, float(ENV.get("CAPTURE_MAX_SECS", "30")))
VIDEO_MAX = 10.0
TTL = max(10, int(ENV.get("CAPTURE_TTL", "300")))
MIC = ENV.get("CAPTURE_MIC") or "default"
CLIP_DIR = "/run/ha-kiosk-capture"
CAP_BYTES = 16 * 1024 * 1024
HA_INTERVAL = 10.0                             # at most one HA-started recording per 10 s
CAM = "/dev/video0"
MIME = {"wav": "audio/wav", "jpg": "image/jpeg", "mp4": "video/mp4"}
EVENT = {"audio": "audio_clip", "snapshot": "snapshot", "video": "video_clip"}
FW_TAG = "ha-kiosk-capture"


def log(msg):
    print(f"capture: {msg}", flush=True)


# Optional push into Home Assistant's media library (Media > My media), using the panel's HA token.
# HA's upload endpoint is admin-only and only accepts image/, video/ and audio/ files; the target
# folder must already exist under /media (default: the top level).
UPLOAD = ENV.get("CAPTURE_UPLOAD", "0") == "1"
UPLOAD_DIR = ENV.get("CAPTURE_UPLOAD_DIR", "").strip("/")


def upload_to_ha(path, kind, ext, clip_id):
    """Upload one clip; returns its media-source id, or None (logged) on failure."""
    import urllib.parse
    import urllib.request
    u = urllib.parse.urlsplit(ENV.get("HA_URL", ""))      # HA_URL may be a full dashboard URL
    base = f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""
    try:
        token = open("/etc/ha-kiosk/token").read().strip()
    except OSError:
        token = ""
    if not base or not token:
        log("upload skipped: HA_URL or token missing")
        return None
    target = "media-source://media_source/local/" + (UPLOAD_DIR or ".")
    fname = f"{NODE_NAME}-{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{clip_id[:6]}.{ext}"
    boundary = "----hakiosk" + clip_id.replace("-", "")
    with open(path, "rb") as f:
        data = f.read()
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"media_content_id\"\r\n\r\n{target}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
        f"Content-Type: {MIME[ext]}\r\n\r\n".encode(), data, f"\r\n--{boundary}--\r\n".encode()])
    req = urllib.request.Request(base + "/api/media_source/local_source/upload", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            media_id = json.load(r).get("media_content_id")
        log(f"uploaded to HA media: {fname}")
        return media_id
    except Exception as e:  # HTTPError carries HA's reason in its body
        detail = e.read().decode(errors="replace")[:200] if hasattr(e, "read") else str(e)
        log(f"upload failed: {detail}")
        return None


NODE_NAME = "panel"   # set in main() to a filename-safe room name


_bus_seq = [0]


def bus_name():
    _bus_seq[0] += 1
    return f"ha_kiosk_capture_{os.getpid()}_{_bus_seq[0]}"


def lan_address(peer_host):
    """The panel address used to reach Home Assistant (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


# ---------------------------------------------------------------- clip store
class Clips:
    """Short-lived files under CLIP_DIR, addressed by an unguessable token."""

    def __init__(self):
        shutil.rmtree(CLIP_DIR, ignore_errors=True)
        os.makedirs(CLIP_DIR, mode=0o700, exist_ok=True)
        self.lock = threading.Lock()
        self.items = {}                        # name -> {"path", "expires", "bytes", "mime"}
        threading.Thread(target=self._sweep_loop, daemon=True).start()

    def tmp_path(self, ext):
        return os.path.join(CLIP_DIR, f".tmp-{uuid.uuid4().hex}.{ext}")

    def add(self, tmp, ext):
        name = f"{secrets.token_urlsafe(24)}.{ext}"
        path = os.path.join(CLIP_DIR, name)
        os.rename(tmp, path)
        size = os.path.getsize(path)
        expires = int(time.time()) + TTL
        with self.lock:
            self.items[name] = {"path": path, "expires": expires, "bytes": size, "mime": MIME[ext]}
            self._enforce_cap()
        return name, size, expires

    def get(self, name):
        with self.lock:
            it = self.items.get(name)
            if it and it["expires"] > time.time() and os.path.exists(it["path"]):
                return it
        return None

    def _remove(self, name):
        it = self.items.pop(name, None)
        if it:
            try:
                os.unlink(it["path"])
            except OSError:
                pass

    def _enforce_cap(self):
        while self.items and sum(i["bytes"] for i in self.items.values()) > CAP_BYTES:
            self._remove(min(self.items, key=lambda n: self.items[n]["expires"]))   # oldest first

    def _sweep_loop(self):
        while True:
            time.sleep(5)
            now = time.time()
            with self.lock:
                for n in [n for n, i in self.items.items() if i["expires"] <= now]:
                    self._remove(n)
            for f in os.listdir(CLIP_DIR):     # stray temp files (crash mid-recording)
                p = os.path.join(CLIP_DIR, f)
                try:
                    if f.startswith(".tmp-") and now - os.path.getmtime(p) > 120:
                        os.unlink(p)
                except OSError:
                    pass


# ---------------------------------------------------------------- camera
GAIN_DEFAULT, GAIN_MIN, GAIN_MAX = 32, 16, 248
TARGET_LUMA = 105                              # mean raw level to aim for (0-255)


def _v4l2_restore():
    subprocess.run(["v4l2-ctl", "-d", CAM, "--set-input=0",
                    "--set-fmt-video=width=1280,height=720,pixelformat=BA81",
                    "--set-ctrl=frame_rate=30", f"--set-ctrl=gain={GAIN_DEFAULT}"],
                   capture_output=True, timeout=15)


def _auto_gain(w, h, probe):
    """Simple auto-exposure. Brilliant's own UI adjusts the sensor live; recording to a file skips
    that, so frames come out very dark at the default gain. Meter a frame, scale the sensor gain
    toward TARGET_LUMA, repeat up to 3 times. Returns the gain to record with."""
    gain = GAIN_DEFAULT
    for _ in range(3):
        subprocess.run(["v4l2-ctl", "-d", CAM, "--set-input=1",
                        f"--set-fmt-video=width={w},height={h},pixelformat=BA81", "--set-ctrl=frame_rate=30",
                        f"--set-ctrl=gain={gain}", "--stream-mmap", "--stream-skip=2", "--stream-count=1",
                        f"--stream-to={probe}"], capture_output=True, timeout=15)
        try:
            with open(probe, "rb") as f:
                d = f.read(w * h)
        except OSError:
            break
        if len(d) < w * h:
            break
        mean = max(1.0, sum(d[::101]) / len(d[::101]))
        new = int(min(GAIN_MAX, max(GAIN_MIN, gain * TARGET_LUMA / mean)))
        if abs(new - gain) <= max(2, gain // 8):
            break
        gain = new
    try:
        os.remove(probe)
    except OSError:
        pass
    return gain


def _capture_raw(w, h, fps, count, skip, raw, gain=GAIN_DEFAULT):
    """Raw BGGR frames from the sensor into a file in tmpfs. (The mxc capture driver rejects
    v4l2src's TRY_FMT, so v4l2-ctl is the source. Frames go to a file rather than a pipe: reads
    from a pipe arrive in 64 KB pieces, and the encoder would treat each piece as a frame.)"""
    return subprocess.Popen(
        ["v4l2-ctl", "-d", CAM, "--set-input=1",
         f"--set-fmt-video=width={w},height={h},pixelformat=BA81", f"--set-ctrl=frame_rate={fps}",
         f"--set-ctrl=gain={gain}",
         "--stream-mmap", f"--stream-skip={skip}", f"--stream-count={count}", f"--stream-to={raw}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _whole_frames(raw, frame):
    """Drop a partial last frame (left when a recording is stopped early). Returns the frame count."""
    size = os.path.getsize(raw) if os.path.exists(raw) else 0
    n = size // frame
    if size != n * frame:
        with open(raw, "r+b") as f:
            f.truncate(n * frame)
    return n


def _encode(w, h, fps, raw, encoder, out, downsample=False):
    """Debayer (Brilliant's bayer2yuv) and encode whole frames read from the raw file."""
    return subprocess.Popen(
        ["gst-launch-1.0", "-q", "filesrc", f"location={raw}", f"blocksize={w * h}", "!",
         f"video/x-bayer,format=bggr,width={w},height={h},framerate={fps}/1", "!",
         "bayer2yuv", *(["downsample=1"] if downsample else []), "!", *encoder, "!", "filesink", f"location={out}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# ---------------------------------------------------------------- recorder
class Recorder:
    def __init__(self, clips, publish):
        self.clips, self.publish = clips, publish
        self.lock = threading.Lock()
        self.state, self.kind, self.source, self.request_id = "idle", None, None, None
        self.t0, self.max, self.level = 0.0, 0.0, 0.0
        self.error, self.last, self.reply = None, None, None
        self.stop_ev, self.cancel_ev = threading.Event(), threading.Event()
        self.procs = []
        self.last_ha = 0.0
        self.state_cb = lambda s: None

    def _set(self, state):
        self.state = state
        self.state_cb(state)

    def status(self):
        r = self.reply
        if r and r["until"] and time.time() > r["until"]:
            self.reply = r = None
        busy = self.state in ("recording", "processing")
        return {"enabled": True, "audio": AUDIO, "video": VIDEO, "state": self.state,
                "kind": self.kind if busy else None, "source": self.source if busy else None,
                "elapsed": round(time.monotonic() - self.t0, 1) if self.state == "recording" else 0,
                "max": self.max if busy else MAX_SECS,
                "level": round(self.level, 3) if self.state == "recording" else 0,
                "last": self.last, "reply": {"text": r["text"], "ts": r["ts"], "clear_after": r["clear_after"]} if r else None,
                "error": self.error}

    def set_reply(self, text, clear_after):
        text = str(text or "").strip()[:500]
        if not text:
            self.reply = None
            return
        try:
            ca = float(clear_after)
        except (TypeError, ValueError):
            ca = 30.0
        self.reply = {"text": text, "ts": int(time.time()), "clear_after": ca,
                      "until": time.time() + ca if ca > 0 else 0}

    def start(self, kind, source="panel", secs=None, request_id=None):
        """Returns (ok, reason)."""
        if kind not in EVENT:
            return False, "unknown kind"
        if kind == "audio" and not AUDIO:
            return False, "microphone locked"
        if kind in ("video", "snapshot") and not VIDEO:
            return False, "camera locked" if CAM_DENIED else "video capture is off (CAPTURE_VIDEO=0)"
        with self.lock:
            if self.state in ("recording", "processing"):
                return False, "busy"
            if source == "ha":
                if time.monotonic() - self.last_ha < HA_INTERVAL:
                    return False, "rate limited"
                self.last_ha = time.monotonic()
            cap = VIDEO_MAX if kind == "video" else (0 if kind == "snapshot" else MAX_SECS)
            try:
                want = float(secs) if secs is not None else cap
            except (TypeError, ValueError):
                want = cap
            self.kind, self.source, self.request_id = kind, source, request_id
            self.max = max(1.0, min(want, cap)) if cap else 0
            self.error, self.level = None, 0.0
            self.stop_ev.clear()
            self.cancel_ev.clear()
            self.t0 = time.monotonic()
            self._set("processing" if kind == "snapshot" else "recording")
        target = {"audio": self._audio, "video": self._video, "snapshot": self._snapshot}[kind]
        threading.Thread(target=self._run, args=(target,), daemon=True).start()
        log(f"{kind} started ({source}{', request ' + str(request_id) if request_id else ''})")
        return True, ""

    def stop(self):
        if self.state != "recording":
            return False
        self.stop_ev.set()
        self._end_procs()
        return True

    def cancel(self):
        if self.state not in ("recording", "processing"):
            return False
        self.cancel_ev.set()
        self.stop_ev.set()
        self._end_procs(kill=True)
        return True

    def _end_procs(self, kill=False):
        """Stopping the camera source makes gst see EOS, so mp4mux still writes a valid file."""
        for p in list(self.procs[:1] if not kill else self.procs):
            try:
                p.kill() if kill else p.terminate()
            except OSError:
                pass

    def _run(self, target):  # noqa: C901 (record -> store -> optional HA upload -> event)
        kind, source, rid = self.kind, self.source, self.request_id
        tmp = None
        try:
            tmp, ext, duration = target()
            if self.cancel_ev.is_set():
                log(f"{kind} cancelled")
                self._set("idle")
                return
            if not tmp or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                raise RuntimeError("nothing was recorded")
            name, size, expires = self.clips.add(tmp, ext)
            tmp = None
            clip_id = str(uuid.uuid4())
            media_id = upload_to_ha(os.path.join(CLIP_DIR, name), kind, ext, clip_id) if UPLOAD else None
            self.last = {"clip_id": clip_id, "kind": kind, "duration": duration, "bytes": size,
                         "expires": expires}
            self._set("idle")
            log(f"{kind} published: {size} B, {duration} s")
            self.publish(EVENT[kind], {"clip_id": clip_id, "name": name, "mime": MIME[ext],
                                       "duration": duration, "bytes": size, "expires": expires,
                                       "source": source, "request_id": rid, "error": None,
                                       "media_id": media_id})
        except Exception as e:
            self.error = str(e)[:200]
            log(f"{kind} failed: {self.error}")
            self._set("error")
            self.publish("capture_error", {"clip_id": str(uuid.uuid4()), "name": None, "mime": None,
                                           "duration": 0, "bytes": 0, "expires": None,
                                           "source": source, "request_id": rid, "error": self.error})
            time.sleep(3)
            if self.state == "error":
                self._set("idle")
        finally:
            self.level = 0.0
            self.procs = []
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # --- audio
    def _audio(self):
        import alsaaudio
        tmp = self.clips.tmp_path("wav")
        pcm = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, device=MIC, rate=RATE, channels=1,
                            format=alsaaudio.PCM_FORMAT_S16_LE, periodsize=CHUNK)
        frames = 0
        try:
            with wave.open(tmp, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                limit = int(self.max * RATE)
                while not self.stop_ev.is_set() and frames < limit:
                    n, buf = pcm.read()
                    if n <= 0:
                        continue
                    buf = buf[:min(n, limit - frames) * 2]
                    w.writeframes(buf)
                    frames += len(buf) // 2
                    cnt = len(buf) // 2
                    if cnt:
                        s = struct.unpack(f"<{cnt}h", buf)
                        rms = math.sqrt(sum(x * x for x in s[::4]) / len(s[::4]))
                        self.level = min(1.0, rms / 6000.0)
        finally:
            pcm.close()                        # the mic is closed as soon as the clip ends
        self.level = 0.0
        self._set("processing")
        return tmp, "wav", round(frames / RATE, 1)

    # --- camera
    def _wait_gst(self, gst, timeout):
        try:
            _, err = gst.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            gst.kill()
            _, err = gst.communicate()
            raise RuntimeError("encoder timed out")
        if gst.returncode and not self.cancel_ev.is_set():
            raise RuntimeError((err or b"").decode(errors="replace").strip()[-200:] or "encoder failed")

    def _snapshot(self):
        tmp, raw = self.clips.tmp_path("jpg"), self.clips.tmp_path("raw")
        w, h = 1280, 720
        try:
            gain = _auto_gain(w, h, raw)
            v4l2 = _capture_raw(w, h, 30, 1, 3, raw, gain)
            self.procs = [v4l2]
            try:
                v4l2.wait(timeout=15)
            except subprocess.TimeoutExpired:
                v4l2.kill()
            _v4l2_restore()                             # camera released (LED off) before encoding
            if _whole_frames(raw, w * h) < 1:
                raise RuntimeError("the camera returned no frame")
            gst = _encode(w, h, 30, raw, ["imxvpuenc_jpeg"], tmp)
            self.procs = [gst]
            self._wait_gst(gst, 30)
        finally:
            self.procs = []
            _v4l2_restore()
            try:
                os.remove(raw)
            except OSError:
                pass
        return tmp, "jpg", 0

    def _video(self):
        tmp, raw = self.clips.tmp_path("mp4"), self.clips.tmp_path("raw")
        # Capture at the sensor's native 1280x720: at 640x360 the driver still hands out full-size
        # buffers and ignores the frame-rate control. bayer2yuv downsample=1 halves it to 640x360.
        w, h, fps, ow, oh = 1280, 720, 5, 640, 360
        try:
            gain = _auto_gain(w, h, raw)
            v4l2 = _capture_raw(w, h, fps, int(self.max * fps), 1, raw, gain)
            self.procs = [v4l2]
            while v4l2.poll() is None and not self.stop_ev.is_set():
                if time.monotonic() - self.t0 > self.max + 5:
                    break
                time.sleep(0.1)
            if v4l2.poll() is None:
                v4l2.terminate()
            try:
                v4l2.wait(timeout=5)
            except subprocess.TimeoutExpired:
                v4l2.kill()
            _v4l2_restore()                             # camera released (LED off) before encoding
            self._set("processing")
            frames = _whole_frames(raw, w * h)
            if frames < 1 or self.cancel_ev.is_set():
                raise RuntimeError("nothing was recorded")
            secs = round(frames / fps, 1)
            # bayer2yuv drops timestamps and mp4mux needs them: copy to system memory (videoconvert)
            # and let rawvideoparse stamp each frame from the frame rate.
            gst = _encode(w, h, fps, raw,
                          ["videoconvert", "!", "video/x-raw,format=NV12", "!", "queue", "!",   # queue: keep rawvideoparse in push mode
                           "rawvideoparse", "use-sink-caps=false", "format=nv12", f"width={ow}", f"height={oh}",
                           f"framerate={fps}/1", "!",
                           # the VPU encoder works in 16-line blocks: 360 lines gave a green bar, so crop to 352
                           "videocrop", "bottom=8", "!", "videoconvert", "!", "video/x-raw,format=NV12,width=640,height=352", "!",
                           "imxvpuenc_h264", "!", "h264parse", "!", "mp4mux"], tmp,
                          downsample=True)
            self.procs = [gst]
            self._wait_gst(gst, 60)
        finally:
            self.procs = []
            _v4l2_restore()
            try:
                os.remove(raw)
            except OSError:
                pass
        return tmp, "mp4", secs


# ---------------------------------------------------------------- MQTT
class CaptureMqtt:
    def __init__(self, rec, node, room, url_base):
        import paho.mqtt.client as mqtt
        self.rec, self.node, self.room, self.url_base = rec, node, room, url_base
        self.base = f"{ENV.get('MQTT_BASE', 'brilliant_kiosk')}/{node}"
        self.disc = ENV.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{node}_capture")
        if ENV.get("MQTT_USER"):
            c.username_pw_set(ENV["MQTT_USER"], ENV.get("MQTT_PASSWORD", ""))
        c.will_set(f"{self.base}/capture/availability", "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.reconnect_delay_set(2, 60)
        self.c = c

    def start(self):
        self.c.connect_async(ENV["MQTT_HOST"], int(ENV.get("MQTT_PORT", "1883")), keepalive=30)
        self.c.loop_start()

    def stop(self):
        self.c.publish(f"{self.base}/capture/availability", "offline", retain=True).wait_for_publish(3)
        self.c.disconnect()

    def _cfg(self, component, key, extra):
        payload = {"unique_id": f"{self.node}_{key}", "object_id": f"{self.node}_{key}",
                   "availability_topic": f"{self.base}/capture/availability",
                   "device": {"identifiers": [self.node], "name": f"{self.room} Brilliant panel",
                              "manufacturer": "Brilliant", "model": "Brilliant Control (HA kiosk)"}}
        payload.update(extra)
        self.c.publish(f"{self.disc}/{component}/{self.node}/{key}/config", json.dumps(payload), retain=True)

    def _on_connect(self, client, userdata, flags, reason, props=None):
        if reason.is_failure:
            log(f"mqtt connect failed: {reason}")
            return
        b = f"{self.base}/capture"
        self._cfg("event", "capture_event", {"name": "Capture", "icon": "mdi:record-rec",
                                             "state_topic": f"{b}/event",
                                             "event_types": list(EVENT.values()) + ["capture_error"]})
        self._cfg("sensor", "capture_state", {"name": "Capture state", "icon": "mdi:record-circle",
                                              "state_topic": f"{b}/state"})
        client.subscribe([(f"{b}/command", 0), (f"{b}/reply", 0), (f"{self.disc}/status", 0)])
        client.publish(f"{b}/availability", "online", retain=True)
        client.publish(f"{b}/state", self.rec.state, retain=True)
        log(f"mqtt connected as {self.node}")

    def publish_state(self, s):
        self.c.publish(f"{self.base}/capture/state", s, retain=True)

    def publish_event(self, event_type, info):
        name = info.pop("name")
        payload = {"event_type": event_type, **info,
                   "url": f"{self.url_base}/clips/{name}" if name else None,
                   "panel": self.node, "room": self.room}
        self.c.publish(f"{self.base}/capture/event", json.dumps(payload), qos=1, retain=False)

    def _on_message(self, client, userdata, msg):
        t, p = msg.topic, msg.payload.decode(errors="replace")
        b = f"{self.base}/capture"
        try:
            if t == f"{b}/command":
                cmd = json.loads(p) if p.strip().startswith("{") else {"action": p.strip()}
                action = str(cmd.get("action", "")).lower()
                if action == "record":
                    rid = cmd.get("request_id")
                    rid = str(rid)[:100] if rid is not None else None
                    ok, why = self.rec.start(str(cmd.get("kind", "audio")), "ha", cmd.get("secs"), rid)
                    if not ok:
                        log(f"HA record refused: {why}")
                        self.publish_event("capture_error", {
                            "clip_id": str(uuid.uuid4()), "name": None, "mime": None, "duration": 0,
                            "bytes": 0, "expires": None, "source": "ha", "request_id": rid, "error": why})
                elif action == "stop":
                    self.rec.stop()
                elif action == "cancel":
                    self.rec.cancel()
            elif t == f"{b}/reply":
                r = json.loads(p) if p.strip().startswith("{") else {"text": p}
                self.rec.set_reply(r.get("text"), r.get("clear_after", 30))
            elif t == f"{self.disc}/status" and p == "online":
                self._on_connect(client, None, None, type("R", (), {"is_failure": False})())
        except Exception as e:
            log(f"{t}: {e}")


# ---------------------------------------------------------------- HTTP
class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _json(h, obj, code=200):
    body = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Cache-Control", "no-store")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def control_server(rec):
    """127.0.0.1 only: the panel UI."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            ok, why = True, ""
            if u.path == "/start":
                ok, why = rec.start(q.get("kind", "audio") if q.get("kind") in ("audio", "video") else "",
                                    "panel")
            elif u.path == "/snapshot":
                ok, why = rec.start("snapshot", "panel")
            elif u.path == "/stop":
                ok = rec.stop()
            elif u.path == "/cancel":
                ok = rec.cancel()
            elif u.path != "/status":
                self.send_error(404)
                return
            st = rec.status()
            if u.path != "/status":
                st.update({"ok": bool(ok), "reason": why or None})
            _json(self, st)

    srv = _Server(("127.0.0.1", PORT + 1), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def clip_server(clips, bind, allow):
    """LAN address only; GET /clips/<token>.<ext> for the allowed hosts, nothing else."""
    name_re = re.compile(r"^/clips/([A-Za-z0-9_-]{32}\.(?:wav|jpg|mp4))$")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _deny(self, code):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if self.client_address[0] not in allow:
                return self._deny(403)
            m = name_re.match(urllib.parse.urlsplit(self.path).path)
            it = clips.get(m.group(1)) if m else None
            if not it:
                return self._deny(404)
            try:
                with open(it["path"], "rb") as f:
                    data = f.read()
            except OSError:
                return self._deny(404)
            self.send_response(200)
            self.send_header("Content-Type", it["mime"])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _other(self):
            self._deny(403 if self.client_address[0] not in allow else 405)

        do_POST = do_PUT = do_DELETE = do_HEAD = _other

    srv = _Server((bind, PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"clip server on {bind}:{PORT}, allowing {', '.join(sorted(allow))}")


# ---------------------------------------------------------------- firewall
def firewall_close():
    """Remove our rule(s) from Brilliant's nftables input chain (runtime only, never persisted)."""
    try:
        out = subprocess.run(["nft", "-a", "list", "chain", "inet", "firewall", "filter-input"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return
    for h in re.findall(rf'comment "{FW_TAG}" # handle (\d+)', out):
        subprocess.run(["nft", "delete", "rule", "inet", "firewall", "filter-input", "handle", h],
                       check=False, timeout=10)


def firewall_open(port, allow):
    firewall_close()
    r = subprocess.run(["nft", "insert", "rule", "inet", "firewall", "filter-input",
                        "ip", "saddr", "{ " + ", ".join(sorted(allow)) + " }",
                        "tcp", "dport", str(port), "accept", "comment", f'"{FW_TAG}"'],
                       capture_output=True, text=True, timeout=10)
    if r.returncode:
        log(f"firewall: {r.stderr.strip() or r.returncode} (port {port} may be unreachable)")


# ---------------------------------------------------------------- main
def main():
    if "--close-firewall" in sys.argv:
        firewall_close()
        return 0
    if ENV.get("KIOSK_CAPTURE", "0") != "1":
        log("KIOSK_CAPTURE is not 1; nothing to do")
        return 0
    if not AUDIO and not VIDEO:
        log("microphone locked by ALLOW_MIC=0 and no camera capture"
            + (" (camera locked by ALLOW_CAMERA=0)" if CAM_DENIED else "") + "; nothing to do")
        return 0
    if MIC_DENIED:
        log("microphone locked by ALLOW_MIC=0: audio capture refused")
    if CAM_DENIED and ENV.get("CAPTURE_VIDEO", "0") == "1":
        log("camera locked by ALLOW_CAMERA=0: video and snapshots refused")
    ha_host = urllib.parse.urlsplit(ENV.get("HA_URL", "")).hostname or ""
    allow = {a.strip() for a in ENV.get("CAPTURE_ALLOW", "").split(",") if a.strip()}
    if not allow and ha_host:
        allow = {socket.gethostbyname(ha_host)}
    if not allow:
        log("no host may download clips: set HA_URL or CAPTURE_ALLOW")
        return 1
    bind = ENV.get("CAPTURE_BIND") or (lan_address(ha_host or sorted(allow)[0]))
    if not bind or bind.startswith("127."):
        log("could not work out the LAN address; set CAPTURE_BIND")
        return 1

    node, room = None, ""
    try:
        from brilliant_bus import Bus
        b = Bus(name=bus_name())
        try:
            node = "brilliant_" + b.device_id[:12]
            v = (b.peripheral("art_config_peripheral").variables or {}).get("display_name")
            room = (v.value or "").strip() if v else ""
        finally:
            b.close()
    except Exception as e:
        log(f"device id: {e}")
    node = node or "brilliant_" + socket.gethostname()[:12]
    room = room or "Brilliant"
    global NODE_NAME
    NODE_NAME = re.sub(r"[^a-z0-9]+", "-", room.lower()).strip("-") or "panel"

    clips = Clips()
    mq = [None]

    def publish(event_type, info):
        if mq[0]:
            mq[0].publish_event(event_type, info)

    rec = Recorder(clips, publish)
    rec.state_cb = lambda s: mq[0] and mq[0].publish_state(s)
    if ENV.get("MQTT_HOST") and ENV.get("KIOSK_MQTT", "1") != "0":
        try:
            mq[0] = CaptureMqtt(rec, node, room, f"http://{bind}:{PORT}")
            mq[0].start()
        except Exception as e:
            log(f"mqtt disabled: {e}")
            mq[0] = None
    else:
        log("MQTT is not set up: clips are recorded but not announced")

    firewall_open(PORT, allow)
    clip_server(clips, bind, allow)
    control_server(rec)
    log(f"ready ({', '.join(k for k, on in (('audio', AUDIO), ('video', VIDEO)) if on)}; max {MAX_SECS:g} s, clips kept {TTL} s)")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    while not stop.wait(1):
        pass
    was_camera = rec.kind in ("video", "snapshot") and rec.state in ("recording", "processing")
    rec.cancel()
    for _ in range(50):                        # let the worker close the mic / camera
        if rec.state not in ("recording", "processing"):
            break
        time.sleep(0.1)
    if was_camera:
        _v4l2_restore()                        # camera back to input 0 even if the worker didn't finish
    firewall_close()
    if mq[0]:
        try:
            mq[0].stop()
        except Exception:
            pass
    shutil.rmtree(CLIP_DIR, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
