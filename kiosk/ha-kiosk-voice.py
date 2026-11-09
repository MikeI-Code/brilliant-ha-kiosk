#!/usr/bin/env python3
"""Opt-in voice satellite and announcement speaker for the panel (KIOSK_VOICE=1 in kiosk.env).

Runs on the panel with Brilliant's Python (/data/switch-embedded/env/bin/python). No extra packages:
the Wyoming protocol is implemented here (JSON header line + optional JSON data + binary payload).

  Wyoming satellite  TCP VOICE_PORT (default 10700) on the panel's LAN address only, and only the
                     Home Assistant host (VOICE_ALLOW, default: the host in HA_URL) may connect.
                     Add it in HA: Settings > Devices & services > Add integration > Wyoming Protocol.
  Mic                ALSA VOICE_MIC (default "default": Brilliant's DC removal + gain), 16 kHz mono.
                     Opened only while audio is streamed to HA.
  Speaker            ALSA VOICE_SPEAKER (default "dmix_filtered": shared dmix through Brilliant's volume
                     control and a 600 Hz high-pass). Half duplex: the mic is not
                     streamed while the panel is speaking (there is no echo cancellation).
  Wake word          VOICE_WAKE=ha  streams the mic to HA continuously; HA's pipeline needs a wake word
                                    engine (e.g. the openWakeWord add-on).
                     VOICE_WAKE=off (default) push-to-talk only: the "Talk" button (MQTT) or HA's
                                    own controls start a pipeline at speech-to-text.
  MQTT (same device as the bridge, when MQTT_HOST is set):
    button Talk            start listening (push-to-talk)
    text   Announce        a sentence (spoken with VOICE_TTS_ENGINE) or an http(s):// or /api/... URL
    number Speaker volume  Brilliant's hardware_peripheral.output_volume (0-100)
    sensor Voice state     idle / listening / processing / speaking

Nothing is recorded to disk: mic audio only goes to Home Assistant over the socket.
"""
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import alsaaudio

sys.path.insert(0, "/etc/ha-kiosk")

ENV = os.environ
_TOPICS = None


def _topics():
    """The shared record of published discovery configs (ha-kiosk-common.py, next to this file)."""
    global _TOPICS
    if _TOPICS is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ha_kiosk_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ha-kiosk-common.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TOPICS = mod.DiscoveryTopics()
    return _TOPICS
RATE, WIDTH, CHANNELS = 16000, 2, 1
CHUNK = 1024                                   # frames per mic chunk (64 ms)
PTT_SECS = float(ENV.get("VOICE_LISTEN_SECS", "15"))
VERSION = "1.5.4"
DEBUG = ENV.get("VOICE_DEBUG", "0") == "1"
MAX_MEDIA = 8 * 1024 * 1024               # bytes; announcements are downloaded into memory


def log(msg):
    print(f"voice: {msg}", flush=True)


_bus_seq = [0]


def bus_name():
    """A fresh client name per bus connection: the bus refuses a name that is still being released."""
    _bus_seq[0] += 1
    return f"ha_kiosk_voice_{os.getpid()}_{_bus_seq[0]}"


def ha_base():
    m = re.match(r"(https?://[^/]+)", ENV.get("HA_URL", ""))
    return m.group(1) if m else ""


def same_origin(url, base):
    """True only if scheme, host and port all match (a prefix check would accept http://ha.evil)."""
    if not base:
        return False
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    dp = {"http": 80, "https": 443}
    try:
        return (a.scheme == b.scheme and a.hostname == b.hostname
                and (a.port or dp.get(a.scheme)) == (b.port or dp.get(b.scheme)))
    except ValueError:                      # malformed port
        return False


def ha_token():
    try:
        with open("/etc/ha-kiosk/token", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def lan_address(peer_host):
    """The panel address used to reach Home Assistant (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


# ---------------------------------------------------------------- Wyoming framing
def send_event(sock, etype, data=None, payload=None, lock=None):
    header = {"type": etype, "version": VERSION}
    body = json.dumps(data).encode() if data else b""
    if body:
        header["data_length"] = len(body)
    if payload:
        header["payload_length"] = len(payload)
    msg = json.dumps(header).encode() + b"\n" + body + (payload or b"")
    if lock:
        with lock:
            sock.sendall(msg)
    else:
        sock.sendall(msg)


def read_event(f):
    line = f.readline()
    if not line:
        return None
    header = json.loads(line)
    data = header.get("data") or {}
    n = header.get("data_length") or 0
    if n:
        data.update(json.loads(f.read(n)))
    n = header.get("payload_length") or 0
    payload = f.read(n) if n else b""
    return header.get("type"), data, payload


# ---------------------------------------------------------------- speaker
class Speaker:
    """Plays PCM or any URL ffmpeg can decode, one thing at a time, on the shared ALSA device."""

    def __init__(self, on_state):
        self.q = queue.Queue()
        self.on_state = on_state
        self.busy = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def play_url(self, url, done=None):
        self.q.put(("url", url, done))

    def play_pcm(self, fmt, chunks, done=None):
        self.q.put(("pcm", (fmt, chunks), done))

    def _open(self, rate, channels):
        return alsaaudio.PCM(alsaaudio.PCM_PLAYBACK, device=ENV.get("VOICE_SPEAKER", "dmix_filtered"),
                             rate=rate, channels=channels, format=alsaaudio.PCM_FORMAT_S16_LE,
                             periodsize=1024)

    def _loop(self):
        while True:
            kind, what, done = self.q.get()
            self.busy.set()
            self.on_state("speaking")
            try:
                if kind == "pcm":
                    (rate, channels), chunks = what
                    out = self._open(rate, channels)
                    for c in chunks:
                        out.write(c)
                    out.close()
                else:
                    self._play_url(what)
            except Exception as e:
                log(f"playback: {e}")
            time.sleep(0.3)                    # let the dmix buffer drain before the mic reopens
            self.busy.clear()
            self.on_state("idle")
            if done:
                done()

    def _play_url(self, url):
        """Download (this ffmpeg build has no network protocols) and decode with ffmpeg."""
        req = urllib.request.Request(url)
        if same_origin(url, ha_base()) and ha_token():   # never send the token to another host
            req.add_header("Authorization", f"Bearer {ha_token()}")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(MAX_MEDIA + 1)
        if len(data) > MAX_MEDIA:
            raise ValueError("media too large")
        p = subprocess.Popen(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
                              "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        threading.Thread(target=self._feed, args=(p, data), daemon=True).start()
        out = self._open(RATE, 1)
        try:
            while True:
                buf = p.stdout.read(4096)
                if not buf:
                    break
                out.write(buf)
        finally:
            out.close()
            p.stdout.close()
            p.wait(timeout=10)

    @staticmethod
    def _feed(p, data):
        try:
            p.stdin.write(data)
        except OSError:
            pass
        finally:
            try:
                p.stdin.close()
            except OSError:
                pass


# ---------------------------------------------------------------- satellite
class Satellite:
    def __init__(self):
        self.wake = ENV.get("VOICE_WAKE", "off").lower()
        self.name = ENV.get("VOICE_NAME") or "Brilliant panel"
        self.sock = None
        self.lock = threading.Lock()           # socket writes
        self.running = False                   # HA sent run-satellite
        self.streaming = threading.Event()     # mic -> HA
        self.stream_until = 0.0
        self.tts = None                        # (fmt, [chunks]) while HA sends TTS audio
        self.state_cb = lambda s: None
        self.state = "idle"
        self.speaker = Speaker(self.set_state)
        threading.Thread(target=self._mic_loop, daemon=True).start()

    def set_state(self, s):
        if s == "idle" and self.streaming.is_set():
            s = "listening"
        self.state = s
        self.state_cb(s)

    # --- outgoing
    def _send(self, etype, data=None, payload=None):
        s = self.sock
        if s is None:
            return False
        try:
            send_event(s, etype, data, payload, self.lock)
            return True
        except OSError:
            return False

    def info(self):
        return {"satellite": {"name": self.name, "description": "Brilliant panel (ha-kiosk)",
                              "attribution": {"name": "brilliant-ha-kiosk", "url": ""},
                              "installed": True, "version": "1.0", "area": None,
                              "has_vad": False, "active_wake_words": [], "max_active_wake_words": 0,
                              "supports_trigger": False,
                              "snd_format": {"rate": RATE, "width": WIDTH, "channels": CHANNELS}},
                "asr": [], "tts": [], "handle": [], "intent": [], "wake": [], "mic": [], "snd": []}

    def start_pipeline(self, start_stage):
        """Ask HA to run a pipeline and stream the mic to it."""
        if not self.running or self.sock is None:
            log("talk: Home Assistant is not connected")
            return False
        if self.speaker.busy.is_set():
            return False
        ok = self._send("run-pipeline", {"start_stage": start_stage, "end_stage": "tts",
                                         "restart_on_end": False,
                                         "snd_format": {"rate": RATE, "width": WIDTH, "channels": CHANNELS}})
        if ok:
            log(f"pipeline started at {start_stage}")
            self.stream_until = (time.time() + PTT_SECS) if start_stage != "wake" else 0
            self.streaming.set()
            self.set_state("listening")
        return ok

    def talk(self):
        self.start_pipeline("asr")

    def _stop_stream(self, state="processing"):
        if self.streaming.is_set():
            self.streaming.clear()
            self.set_state(state)

    def _resume(self):
        """After a pipeline: back to idle, or (wake word on HA) back to streaming for the next one."""
        self._stop_stream("idle")
        self.set_state("idle")
        if self.wake == "ha" and self.running:
            self.start_pipeline("wake")

    # --- mic
    def _mic_loop(self):
        while True:
            self.streaming.wait()
            pcm = None
            try:
                pcm = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, device=ENV.get("VOICE_MIC", "default"),
                                    rate=RATE, channels=CHANNELS, format=alsaaudio.PCM_FORMAT_S16_LE,
                                    periodsize=CHUNK)
                fmt = {"rate": RATE, "width": WIDTH, "channels": CHANNELS}
                self._send("audio-start", {**fmt, "timestamp": 0})
                t0 = time.monotonic()
                while self.streaming.is_set() and not self.speaker.busy.is_set():
                    n, buf = pcm.read()
                    if n <= 0:
                        continue
                    ts = int((time.monotonic() - t0) * 1000)
                    if not self._send("audio-chunk", {**fmt, "timestamp": ts}, buf):
                        break
                    if self.stream_until and time.time() > self.stream_until:
                        log("listen timeout")
                        self._stop_stream()
                self._send("audio-stop", {"timestamp": int((time.monotonic() - t0) * 1000)})
            except Exception as e:
                log(f"mic: {e}")
                self.streaming.clear()
                time.sleep(2)
            finally:
                if pcm is not None:
                    pcm.close()
            while self.speaker.busy.is_set():    # half duplex: wait until the panel stops talking
                time.sleep(0.1)

    # --- incoming
    def handle(self, conn, etype, data, payload):
        if DEBUG and etype != "audio-chunk":
            log(f"<- {etype} {json.dumps(data)[:200]} ({len(payload)} B)")
        if etype == "describe":
            send_event(conn, "info", self.info(), lock=self.lock)
            return
        if etype == "ping":
            send_event(conn, "pong", {"text": data.get("text")}, lock=self.lock)
            return
        if etype == "run-satellite":
            self.sock = conn
            self.running = True
            log("Home Assistant started the satellite")
            if self.wake == "ha":
                self.start_pipeline("wake")
            return
        if conn is not self.sock:
            return                             # a probe connection (e.g. HA checking the info)
        if etype == "pause-satellite":
            self.running = False
            self._stop_stream("idle")
        elif etype == "detection":
            log(f"wake word: {data.get('name')}")
            self.stream_until = time.time() + PTT_SECS
        elif etype == "voice-stopped":
            self._stop_stream()
        elif etype == "transcript":
            log(f"transcript: {data.get('text')!r}")
            self._stop_stream()
        elif etype == "synthesize":
            log(f"response: {data.get('text')!r}")
        elif etype == "audio-start":
            self._stop_stream()
            self.tts = ((int(data.get("rate", RATE)), int(data.get("channels", 1))), [])
            if int(data.get("width", 2)) != 2:
                log("unsupported TTS sample width")
                self.tts = None
        elif etype == "audio-chunk":
            if self.tts is not None:
                self.tts[1].append(payload)
        elif etype == "audio-stop":
            if self.tts is not None:
                fmt, chunks = self.tts
                self.tts = None
                if DEBUG:
                    log(f"TTS audio: {len(chunks)} chunks, {sum(map(len, chunks))} B at {fmt}")
                self.speaker.play_pcm(fmt, chunks, done=self._played)
        elif etype == "error":
            log(f"pipeline error: {data.get('text')} ({data.get('code')})")
            self._resume()
        elif etype in ("run-pipeline", "transcribe", "voice-started"):
            pass

    def _played(self):
        self._send("played")
        self._resume()

    # --- server
    def serve(self, bind, port, allow):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((bind, port))
        srv.listen(4)
        log(f"Wyoming satellite on {bind}:{port}, accepting {', '.join(sorted(allow)) or 'anyone'}")
        while True:
            conn, addr = srv.accept()
            if allow and addr[0] not in allow:
                log(f"refused connection from {addr[0]}")
                conn.close()
                continue
            threading.Thread(target=self._client, args=(conn, addr), daemon=True).start()

    def _client(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        f = conn.makefile("rb")
        try:
            while True:
                ev = read_event(f)
                if ev is None:
                    break
                self.handle(conn, *ev)
        except (OSError, ValueError) as e:
            log(f"connection: {e}")
        finally:
            if self.sock is conn:
                self.sock = None
                self.running = False
                self._stop_stream("idle")
                log(f"Home Assistant disconnected ({addr[0]})")
            try:
                conn.close()
            except OSError:
                pass


# ---------------------------------------------------------------- announcements
def tts_url(message):
    req = urllib.request.Request(
        ha_base() + "/api/tts_get_url",
        data=json.dumps({"engine_id": ENV.get("VOICE_TTS_ENGINE", "tts.google_translate_en_com"),
                         "message": message}).encode(),
        headers={"Authorization": f"Bearer {ha_token()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["url"]


def announce(speaker, text):
    text = text.strip()
    if not text:
        return
    try:
        if text.startswith("/"):
            url = ha_base() + text
        elif re.match(r"https?://", text):
            url = text
        else:
            url = tts_url(text)
        log(f"announce: {urllib.parse.urlsplit(url).path[:60]}")
        speaker.play_url(url)
    except Exception as e:
        log(f"announce: {e}")


# ---------------------------------------------------------------- MQTT
class VoiceMqtt:
    def __init__(self, sat):
        import paho.mqtt.client as mqtt
        from brilliant_bus import Bus
        self.sat, self.Bus = sat, Bus
        b = Bus(name=bus_name())
        try:
            dev = b.device_id
            room = (b.peripheral("art_config_peripheral").variables or {}).get("display_name")
            room = (room.value or "").strip() if room else ""
        finally:
            b.close()
        self.room = room or "Brilliant"
        self.node = "brilliant_" + dev[:12]
        self.base = f"{ENV.get('MQTT_BASE', 'brilliant_kiosk')}/{self.node}"
        self.disc = ENV.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self.node}_voice")
        if ENV.get("MQTT_USER"):
            c.username_pw_set(ENV["MQTT_USER"], ENV.get("MQTT_PASSWORD", ""))
        c.will_set(f"{self.base}/voice/availability", "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.reconnect_delay_set(2, 60)
        self.c = c
        sat.state_cb = lambda s: self.c.publish(f"{self.base}/voice/state", s, retain=True)

    def start(self):
        self.c.connect_async(ENV["MQTT_HOST"], int(ENV.get("MQTT_PORT", "1883")), keepalive=30)
        self.c.loop_start()

    def _cfg(self, component, key, extra):
        payload = {"unique_id": f"{self.node}_{key}", "object_id": f"{self.node}_{key}",
                   "availability_topic": f"{self.base}/voice/availability",
                   "device": {"identifiers": [self.node], "name": f"{self.room} Brilliant panel",
                              "manufacturer": "Brilliant", "model": "Brilliant Control (HA kiosk)"}}
        payload.update(extra)
        topic = f"{self.disc}/{component}/{self.node}/{key}/config"
        _topics().add(topic)            # recorded first: ha-kiosk-setup withdraws it on uninstall
        self.c.publish(topic, json.dumps(payload), retain=True)

    def _on_connect(self, client, userdata, flags, reason, props=None):
        if reason.is_failure:
            log(f"mqtt connect failed: {reason}")
            return
        b = f"{self.base}/voice"
        self._cfg("button", "voice_talk", {"name": "Talk", "icon": "mdi:microphone",
                                           "command_topic": f"{b}/talk"})
        self._cfg("text", "voice_announce", {"name": "Announce", "icon": "mdi:bullhorn", "max": 255,
                                             "command_topic": f"{b}/announce",
                                             "state_topic": f"{b}/announce/state"})
        self._cfg("number", "speaker_volume", {"name": "Speaker volume", "icon": "mdi:volume-high",
                                               "min": 0, "max": 100, "step": 1, "mode": "slider",
                                               "state_topic": f"{b}/volume",
                                               "command_topic": f"{b}/volume/set",
                                               "entity_category": "config"})
        self._cfg("sensor", "voice_state", {"name": "Voice state", "icon": "mdi:account-voice",
                                            "state_topic": f"{b}/state"})
        client.subscribe([(f"{b}/talk", 0), (f"{b}/announce", 0), (f"{b}/volume/set", 0),
                          (f"{self.disc}/status", 0)])
        client.publish(f"{b}/availability", "online", retain=True)
        client.publish(f"{b}/state", self.sat.state, retain=True)
        self.publish_volume()
        log(f"mqtt connected as {self.node}")

    def publish_volume(self):
        try:
            b = self.Bus(name=bus_name())
            try:
                v = b.peripheral("hardware_peripheral").variables["output_volume"].value
            finally:
                b.close()
            self.c.publish(f"{self.base}/voice/volume", v, retain=True)
        except Exception as e:
            log(f"volume read: {e}")

    def _on_message(self, client, userdata, msg):
        t, p = msg.topic, msg.payload.decode(errors="replace")
        b = f"{self.base}/voice"
        try:
            if t == f"{b}/talk":
                threading.Thread(target=self.sat.talk, daemon=True).start()
            elif t == f"{b}/announce":
                self.c.publish(f"{b}/announce/state", p[:255], retain=False)
                threading.Thread(target=announce, args=(self.sat.speaker, p), daemon=True).start()
            elif t == f"{b}/volume/set":
                v = str(max(0, min(100, int(float(p)))))
                bus = self.Bus(name=bus_name())
                try:
                    bus.set("hardware_peripheral", {"output_volume": v})
                finally:
                    bus.close()
                log(f"output_volume={v}")
                self.publish_volume()
            elif t == f"{self.disc}/status" and p == "online":
                self._on_connect(client, None, None, type("R", (), {"is_failure": False})())
        except Exception as e:
            log(f"{t}: {e}")


FW_TAG = "ha-kiosk-voice"


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
    """Brilliant's firewall drops unknown TCP ports: accept VOICE_PORT from the allowed hosts only."""
    firewall_close()
    src = ["ip", "saddr", "{ " + ", ".join(sorted(allow)) + " }"] if allow else []
    r = subprocess.run(["nft", "insert", "rule", "inet", "firewall", "filter-input", *src,
                        "tcp", "dport", str(port), "accept", "comment", f'"{FW_TAG}"'],
                       capture_output=True, text=True, timeout=10)
    if r.returncode:
        log(f"firewall: {r.stderr.strip() or r.returncode} (port {port} may be unreachable)")


def main():
    if "--close-firewall" in sys.argv:
        firewall_close()
        return 0
    if ENV.get("KIOSK_VOICE", "0") != "1":
        log("KIOSK_VOICE is not 1; nothing to do")
        return 0
    if ENV.get("ALLOW_MIC", "").strip().lower() in ("0", "off", "false", "no"):
        log("microphone locked by ALLOW_MIC=0")
        return 0
    ha_host = urllib.parse.urlsplit(ENV.get("HA_URL", "")).hostname or ""
    allow = {a.strip() for a in ENV.get("VOICE_ALLOW", "").split(",") if a.strip()}
    if not allow and ha_host:
        allow = {socket.gethostbyname(ha_host)}
    bind = ENV.get("VOICE_BIND") or (lan_address(ha_host) if ha_host else "")
    if not bind or bind.startswith("127."):
        log("could not work out the LAN address; set VOICE_BIND")
        return 1
    sat = Satellite()
    if ENV.get("MQTT_HOST") and ENV.get("KIOSK_MQTT", "1") != "0":
        try:
            VoiceMqtt(sat).start()
        except Exception as e:
            log(f"mqtt disabled: {e}")
    port = int(ENV.get("VOICE_PORT", "10700"))
    firewall_open(port, allow)
    sat.serve(bind, port, allow)
    return 0


if __name__ == "__main__":
    sys.exit(main())
