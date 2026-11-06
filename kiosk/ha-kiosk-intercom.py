#!/usr/bin/env python3
"""Opt-in push-to-talk intercom between panels (KIOSK_INTERCOM=1 in kiosk.env). Audio only.

Runs on the panel with Brilliant's Python (/data/switch-embedded/env/bin/python). No extra packages.

  Receive   KIOSK_INTERCOM=1: listen on UDP INTERCOM_PORT (default 10710) and play what another
            panel sends on ALSA INTERCOM_SPEAKER (default dmix_filtered, follows Brilliant's volume).
            Only panels found through MQTT discovery are played; anything else is dropped.
  Send      INTERCOM_MIC=1 as well: the mic is streamed while
            "talk" is held, at most INTERCOM_MAX_SECS; ALSA INTERCOM_MIC_DEVICE (default "default").
            Without INTERCOM_MIC=1 the mic is never opened.
  Wire      RTP over UDP, payload type 96, 16 kHz mono signed 16-bit little-endian PCM, 20 ms packets
            (256 kbit/s; plain PCM costs far less CPU on this board than Opus).
  Discovery every intercom panel publishes a retained {MQTT_BASE}/intercom/peers/<node> with its
            room name and address (cleared by its MQTT last will).
  MQTT entities on the panel's device:
    sensor Intercom state              idle / talking to <room> / listening to <room>
    select Intercom target             (mic panels) a room or "All panels"
    switch Intercom talk               (mic panels) on = stream the mic to the target
    button Intercom test tone          (mic panels) a 1 s tone to the target, no mic
  Local control for the panel UI (127.0.0.1 only, port INTERCOM_PORT+1):
    GET /status   GET /talk?on=1   GET /talk?on=0   GET /target?name=<room>   GET /tone

Nothing is recorded: audio only exists in memory on its way to the other panel's speaker.
"""
import json
import math
import os
import queue
import random
import re
import socket
import struct
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import alsaaudio

sys.path.insert(0, "/etc/ha-kiosk")

ENV = os.environ
RATE = 16000
FRAME = 320                       # samples per packet (20 ms)
PKT = FRAME * 2                   # bytes of PCM per packet
PT = 96
ALL = "All panels"
PORT = int(ENV.get("INTERCOM_PORT", "10710"))
MAX_SECS = float(ENV.get("INTERCOM_MAX_SECS", "30"))
MIC_WANTED = ENV.get("INTERCOM_MIC", "0") == "1"
MIC_DENIED = ENV.get("ALLOW_MIC", "").strip().lower() in ("0", "off", "false", "no")
MIC_ON = MIC_WANTED and not MIC_DENIED        # ALLOW_MIC=0 forces receive-only
GROUP = ENV.get("PANEL_GROUP", "").strip()    # only peers in the same group are listed/accepted
HANGOVER = 0.6                    # s without packets before the speaker is released


def log(msg):
    print(f"intercom: {msg}", flush=True)


_bus_seq = [0]


def bus_name():
    _bus_seq[0] += 1
    return f"ha_kiosk_intercom_{os.getpid()}_{_bus_seq[0]}"


def lan_address(peer_host):
    """The panel address used to reach the MQTT broker (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer_host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


class Intercom:
    def __init__(self, node, room, addr):
        self.node, self.room, self.addr = node, room, addr
        self.peers = {}                          # node -> {"name", "host", "port", "mic"}
        self.target = ALL
        self.state = "idle"
        self.state_cb = lambda s: None
        self.talking = threading.Event()
        self.talk_lock = threading.Lock()
        self.rx = queue.Queue(maxsize=100)       # (from_name, pcm)
        self.rx_from = ""
        self.mqtt = None
        self.sock =socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((addr, PORT))
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    # --- state
    def set_state(self, s):
        if s != self.state:
            self.state = s
            log(s)
            self.state_cb(s)

    def _idle_state(self):
        if self.talking.is_set():
            return f"talking to {self.target}"
        if self.rx_from:
            return f"listening to {self.rx_from}"
        return "idle"

    def targets(self):
        """Peers that can play audio, other than this panel."""
        return {n: p for n, p in self.peers.items() if n != self.node}

    def target_options(self):
        return [ALL] + sorted({p["name"] for p in self.targets().values()})

    def destinations(self):
        t = self.targets()
        if self.target != ALL:
            t = {n: p for n, p in t.items() if p["name"] == self.target}
        return [(p["host"], int(p.get("port", PORT))) for p in t.values()]

    # --- receive
    def _recv_loop(self):
        last_seq = {}
        while True:
            try:
                data, (host, _) = self.sock.recvfrom(2048)
            except OSError as e:
                log(f"recv: {e}")
                time.sleep(1)
                continue
            src = next((p for p in self.targets().values() if p["host"] == host), None)
            if src is None or len(data) < 12 or data[0] >> 6 != 2 or data[1] & 0x7F != PT:
                continue                         # not a known panel, or not our RTP
            seq = struct.unpack("!H", data[2:4])[0]
            prev = last_seq.get(host)
            if prev is not None and data[1] & 0x80 == 0 and 0 < (prev - seq) & 0xFFFF < 1000:
                continue                         # late / duplicate packet
            last_seq[host] = seq
            try:
                self.rx.put_nowait((src["name"], data[12:]))
            except queue.Full:
                pass

    def _play_loop(self):
        while True:
            name, pcm = self.rx.get()
            self.rx_from = name
            self.set_state(self._idle_state())
            out = None
            try:
                out = alsaaudio.PCM(alsaaudio.PCM_PLAYBACK,
                                    device=ENV.get("INTERCOM_SPEAKER", "dmix_filtered"),
                                    rate=RATE, channels=1, format=alsaaudio.PCM_FORMAT_S16_LE,
                                    periodsize=FRAME)
                time.sleep(0.06)                 # small jitter buffer: let 2-3 packets arrive
                out.write(pcm)
                while True:
                    try:
                        _, pcm = self.rx.get(timeout=HANGOVER)
                    except queue.Empty:
                        break
                    out.write(pcm)
            except Exception as e:
                log(f"playback: {e}")
                time.sleep(1)
            finally:
                if out is not None:
                    out.close()
            self.rx_from = ""
            self.set_state(self._idle_state())

    # --- send
    def talk(self, on, secs=None, tone=False):
        if not on:
            self.talking.clear()
            return True
        if not (MIC_ON or tone):
            log("talk ignored: " + ("microphone locked by ALLOW_MIC=0" if MIC_DENIED else "INTERCOM_MIC is not 1 on this panel"))
            return False
        dest = self.destinations()
        if not dest:
            log(f"talk: no intercom panel for target {self.target!r}")
            return False
        if not self.talk_lock.acquire(blocking=False):
            return False                         # already talking
        self.talking.set()
        threading.Thread(target=self._send, args=(dest, min(secs or MAX_SECS, MAX_SECS), tone),
                         daemon=True).start()
        return True

    def _frames_tone(self):
        amp = 6000
        n = 0
        while True:
            yield b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * 880 * (n + i) / RATE)))
                           for i in range(FRAME))
            n += FRAME
            time.sleep(FRAME / RATE)             # pace like a real mic

    def _frames_mic(self):
        pcm = alsaaudio.PCM(alsaaudio.PCM_CAPTURE, device=ENV.get("INTERCOM_MIC_DEVICE") or "default",
                            rate=RATE, channels=1, format=alsaaudio.PCM_FORMAT_S16_LE,
                            periodsize=FRAME)
        buf = b""
        try:
            while True:
                n, data = pcm.read()
                if n > 0:
                    buf += data
                while len(buf) >= PKT:
                    yield buf[:PKT]
                    buf = buf[PKT:]
        finally:
            pcm.close()

    def _send(self, dest, secs, tone):
        log(f"talk to {self.target} ({len(dest)} panel(s)){' [tone]' if tone else ''}")
        self.set_state(self._idle_state())
        seq, ts, ssrc = random.randrange(65536), random.randrange(1 << 32), random.randrange(1 << 32)
        first = True
        end = time.monotonic() + secs
        frames = self._frames_tone() if tone else self._frames_mic()
        sent = 0
        try:
            for pcm in frames:
                if not self.talking.is_set() or time.monotonic() > end:
                    break
                hdr = struct.pack("!BBHII", 0x80, PT | (0x80 if first else 0), seq, ts, ssrc)
                for d in dest:
                    try:
                        self.sock.sendto(hdr + pcm, d)
                    except OSError:
                        pass
                first = False
                seq = (seq + 1) & 0xFFFF
                ts = (ts + FRAME) & 0xFFFFFFFF
                sent += 1
        except Exception as e:
            log(f"send: {e}")
        finally:
            frames.close()
            self.talking.clear()
            self.talk_lock.release()
            log(f"talk ended ({sent * FRAME / RATE:.1f} s)")
            self.set_state(self._idle_state())


# ---------------------------------------------------------------- MQTT
class IntercomMqtt:
    def __init__(self, ic, base, disc):
        import paho.mqtt.client as mqtt
        self.ic, self.root, self.disc = ic, base, disc
        self.base = f"{base}/{ic.node}"
        self.peer_topic = f"{base}/intercom/peers"
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{ic.node}_intercom")
        if ENV.get("MQTT_USER"):
            c.username_pw_set(ENV["MQTT_USER"], ENV.get("MQTT_PASSWORD", ""))
        c.will_set(f"{self.peer_topic}/{ic.node}", "", retain=True)     # removes this panel when gone
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.reconnect_delay_set(2, 60)
        self.c = c
        ic.state_cb = lambda s: self.c.publish(f"{self.base}/intercom/state", s, retain=True)

    def start(self):
        self.c.connect_async(ENV["MQTT_HOST"], int(ENV.get("MQTT_PORT", "1883")), keepalive=30)
        self.c.loop_start()

    def stop(self):
        self.c.publish(f"{self.peer_topic}/{self.ic.node}", "", retain=True).wait_for_publish(3)
        self.c.disconnect()

    def _cfg(self, component, key, extra):
        n = self.ic.node
        payload = {"unique_id": f"{n}_{key}", "object_id": f"{n}_{key}",
                   "availability_topic": f"{self.base}/intercom/availability",
                   "device": {"identifiers": [n], "name": f"{self.ic.room} Brilliant panel",
                              "manufacturer": "Brilliant", "model": "Brilliant Control (HA kiosk)"}}
        payload.update(extra or {})
        self.c.publish(f"{self.disc}/{component}/{n}/{key}/config",
                       json.dumps(payload) if extra is not None else "", retain=True)

    def publish_entities(self):
        b = f"{self.base}/intercom"
        self._cfg("sensor", "intercom_state", {"name": "Intercom state", "icon": "mdi:phone-in-talk",
                                               "state_topic": f"{b}/state"})
        if MIC_ON:
            self._cfg("select", "intercom_target", {"name": "Intercom target", "icon": "mdi:account-voice",
                                                    "options": self.ic.target_options(),
                                                    "state_topic": f"{b}/target",
                                                    "command_topic": f"{b}/target/set"})
            self._cfg("switch", "intercom_talk", {"name": "Intercom talk", "icon": "mdi:microphone-message",
                                                  "state_topic": f"{b}/talk",
                                                  "command_topic": f"{b}/talk/set"})
            self._cfg("button", "intercom_tone", {"name": "Intercom test tone", "icon": "mdi:bell-ring",
                                                  "command_topic": f"{b}/tone",
                                                  "entity_category": "diagnostic"})
            self.c.publish(f"{b}/target", self.ic.target, retain=True)
        else:                                    # receive-only: make sure no talk controls linger
            for comp, key in (("select", "intercom_target"), ("switch", "intercom_talk"),
                              ("button", "intercom_tone")):
                self._cfg(comp, key, None)

    def _on_connect(self, client, userdata, flags, reason, props=None):
        if reason.is_failure:
            log(f"mqtt connect failed: {reason}")
            return
        b = f"{self.base}/intercom"
        client.publish(f"{self.peer_topic}/{self.ic.node}",
                       json.dumps({"name": self.ic.room, "host": self.ic.addr, "port": PORT,
                                   "mic": MIC_ON, "group": GROUP}), retain=True)
        client.subscribe([(f"{self.peer_topic}/+", 0), (f"{b}/target/set", 0), (f"{b}/talk/set", 0),
                          (f"{b}/tone", 0), (f"{self.disc}/status", 0)])
        self.publish_entities()
        client.publish(f"{b}/availability", "online", retain=True)
        client.publish(f"{b}/state", self.ic.state, retain=True)
        client.publish(f"{b}/talk", "OFF", retain=True)
        log(f"mqtt connected as {self.ic.node} ({self.ic.room}, mic {'on' if MIC_ON else 'off'})")

    def talk_state(self, on):
        self.c.publish(f"{self.base}/intercom/talk", "ON" if on else "OFF", retain=True)

    def _on_message(self, client, userdata, msg):
        t, p = msg.topic, msg.payload.decode(errors="replace")
        b = f"{self.base}/intercom"
        try:
            if t.startswith(self.peer_topic + "/"):
                node = t.rsplit("/", 1)[1]
                before = self.ic.target_options()
                if p:
                    info = json.loads(p)
                    if (re.match(r"^[0-9.]+$", str(info.get("host", "")))
                            and str(info.get("group") or "") == GROUP):
                        self.ic.peers[node] = info
                    else:
                        self.ic.peers.pop(node, None)    # other group (or bad address): ignore
                else:
                    self.ic.peers.pop(node, None)
                if node != self.ic.node:
                    log(f"peers: {', '.join(sorted(v['name'] for k, v in self.ic.targets().items())) or 'none'}")
                if MIC_ON and self.ic.target_options() != before:
                    self.publish_entities()
            elif t == f"{b}/target/set":
                if p in self.ic.target_options():
                    self.ic.target = p
                    self.c.publish(f"{b}/target", p, retain=True)
            elif t == f"{b}/talk/set":
                self.ic.talk(p.upper() == "ON")
            elif t == f"{b}/tone":
                self.ic.talk(True, secs=1.0, tone=True)
            elif t == f"{self.disc}/status" and p == "online":
                self.publish_entities()
        except Exception as e:
            log(f"{t}: {e}")


# ---------------------------------------------------------------- local HTTP (panel UI)
class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def http_server(ic):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            ok = True
            if u.path == "/talk":
                ok = ic.talk(q.get("on") == "1")
            elif u.path == "/tone":
                ok = ic.talk(True, secs=1.0, tone=True)
            elif u.path == "/target":
                ok = q.get("name") in ic.target_options()
                if ok:
                    ic.target = q["name"]
                    if ic.mqtt:
                        ic.mqtt.c.publish(f"{ic.mqtt.base}/intercom/target", ic.target, retain=True)
            elif u.path != "/status":
                self.send_error(404)
                return
            body = json.dumps({"ok": bool(ok), "mic": MIC_ON, "state": ic.state,
                               "talking": ic.talking.is_set(), "target": ic.target,
                               "targets": ic.target_options() if ic.targets() else []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = _Server(("127.0.0.1", PORT + 1), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def main():
    if ENV.get("KIOSK_INTERCOM", "0") != "1":
        log("KIOSK_INTERCOM is not 1; nothing to do")
        return 0
    if not ENV.get("MQTT_HOST") or ENV.get("KIOSK_MQTT", "1") == "0":
        log("the intercom finds other panels over MQTT: set MQTT_HOST (and KIOSK_MQTT=1)")
        return 1
    from brilliant_bus import Bus
    b = Bus(name=bus_name())
    try:
        dev = b.device_id
        room = (b.peripheral("art_config_peripheral").variables or {}).get("display_name")
        room = (room.value or "").strip() if room else ""
    finally:
        b.close()
    node = "brilliant_" + dev[:12]
    if MIC_WANTED and MIC_DENIED:
        log("microphone locked by ALLOW_MIC=0: receive only")
    log(f"group: {GROUP or '(none)'}")
    addr = ENV.get("INTERCOM_BIND") or lan_address(socket.gethostbyname(ENV["MQTT_HOST"]))
    ic = Intercom(node, room or node, addr)
    ic.mqtt = IntercomMqtt(ic, ENV.get("MQTT_BASE", "brilliant_kiosk"),
                           ENV.get("MQTT_DISCOVERY_PREFIX", "homeassistant"))
    orig_cb = ic.state_cb

    def state_cb(s):
        orig_cb(s)
        ic.mqtt.talk_state(ic.talking.is_set())
    ic.state_cb = state_cb
    ic.mqtt.start()
    http_server(ic)
    log(f"UDP {addr}:{PORT}, mic {'on' if MIC_ON else 'off (receive only)'}")
    import signal
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    while not stop.wait(1):
        pass
    ic.talking.clear()
    try:
        ic.mqtt.stop()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
