#!/usr/bin/env python3
"""Local bridge between the kiosk UI and the Brilliant message bus (runs on the panel with
Brilliant's Python: /data/switch-embedded/env/bin/python).

Lets the screen control this panel's own wired load directly, without Home Assistant in the path.
HTTP on 127.0.0.1 only (BRIDGE_PORT, default 8765):

  GET  /load                      -> {"on": true, "brightness": 70, "power": 189, "name": "Kitchen Table"}
  POST /gesture {"name": "3_tap"}    (forwarded to Home Assistant as an MQTT event)
  POST /load {"on": false}        |  {"toggle": true}  |  {"brightness": 40}  |  {"step": -10}
       brightness/step are percent (1-100); brightness also turns the load on

The bus protocol is described in docs/hardware-and-services.md (section 6).
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/etc/ha-kiosk")
from brilliant_bus import Bus  # noqa: E402

LOAD = os.environ.get("BRIDGE_LOAD", "gangbox_peripheral_0")
PORT = int(os.environ.get("BRIDGE_PORT", "8765"))


class LoadControl:
    """Thread-safe access to the load over one persistent bus connection (reconnects on error)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.bus = None

    def _with_bus(self, fn):
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.bus is None:
                        self.bus = Bus(name="ha_kiosk_bridge")
                    return fn(self.bus)
                except Exception:
                    try:
                        self.bus and self.bus.close()
                    except Exception:
                        pass
                    self.bus = None
                    if attempt == 2:
                        raise

    def variables(self, peripheral=LOAD):
        """All variables of a peripheral as {name: value string}."""
        v = self._with_bus(lambda b: b.peripheral(peripheral).variables) or {}
        return {k: x.value for k, x in v.items()}

    def info(self):
        def get(b):
            room = (b.peripheral("art_config_peripheral").variables or {}).get("display_name")
            return {"device_id": b.device_id, "room": (room.value or "").strip() if room else ""}
        return self._with_bus(get)

    def state(self):
        v = self._with_bus(lambda b: b.peripheral(LOAD).variables)
        top = int(v["max_intensity_value"].value or 1000) if "max_intensity_value" in v else 1000
        return {"on": v["on"].value == "1",
                "brightness": round(100 * int(v["intensity"].value) / top),
                "dimmable": v.get("dimmable") is not None and v["dimmable"].value == "1",
                "power": int(v["power"].value) if "power" in v else None,
                "name": (v["display_name"].value or "").strip() if "display_name" in v else ""}

    def apply(self, req):
        cur = self.state()
        values = {}
        if "toggle" in req:
            values["on"] = "0" if cur["on"] else "1"
        if "on" in req:
            values["on"] = "1" if req["on"] else "0"
        pct = None
        if "brightness" in req:
            pct = float(req["brightness"])
        elif "step" in req:
            pct = (cur["brightness"] if cur["on"] else 0) + float(req["step"])
        if pct is not None:
            if pct < 1:
                values["on"] = "0"
            else:
                values["intensity"] = str(int(round(min(100.0, pct) * 10)))
                values["on"] = "1"
        if values:
            self._with_bus(lambda b: b.set(LOAD, values))
        return self.state()


control = LoadControl()
mqtt_link = None


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/load":
            return self._reply(404, {"error": "not found"})
        try:
            self._reply(200, control.state())
        except Exception as e:
            self._reply(503, {"error": str(e)})

    def do_POST(self):
        if self.path == "/gesture":          # the UI reports every multi-finger gesture
            name = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}").get("name", "")
            if mqtt_link:
                mqtt_link.gesture(name)
            return self._reply(200, {"ok": True})
        if self.path != "/load":
            return self._reply(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            self._reply(200, control.apply(req))
        except Exception as e:
            self._reply(503, {"error": str(e)})

    def log_message(self, fmt, *args):   # quiet; errors are in the replies
        pass


def start_mqtt():
    """Publish this panel to Home Assistant over MQTT if MQTT_HOST is configured."""
    global mqtt_link
    import importlib.util
    spec = importlib.util.spec_from_file_location("ha_kiosk_mqtt", "/etc/ha-kiosk/ha-kiosk-mqtt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = {**mod.read_env("/etc/ha-kiosk/kiosk.env"), **mod.read_env(mod.OVERRIDES)}
    if not env.get("MQTT_HOST"):
        print("bridge: MQTT off (no MQTT_HOST)", flush=True)
        return
    mqtt_link = mod.KioskMqtt(env, control, control.info())
    mqtt_link.start()


def start_photo_sync():
    """Mirror screensaver photos from Home Assistant's media library (PHOTO_SYNC_MINS, default on)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ha_kiosk_photos", "/etc/ha-kiosk/ha-kiosk-photos.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = {}
    for path in ("/etc/ha-kiosk/kiosk.env", "/var/lib/ha-kiosk/overrides.env"):
        try:
            for line in open(path, encoding="utf-8"):
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"')
        except OSError:
            pass
    if env.get("PHOTO_SYNC_MINS", "30") in ("0", "off"):
        print("bridge: HA photo sync off", flush=True)
        return
    mod.PhotoSync(env).start()


def main():
    for i in range(30):                  # the bus may still be starting at boot
        try:
            print("bridge: load", json.dumps(control.state()), flush=True)
            break
        except Exception as e:
            print(f"bridge: waiting for message bus ({e})", flush=True)
            time.sleep(5)
    try:
        start_mqtt()
    except Exception as e:
        print(f"bridge: MQTT not started: {e}", flush=True)
    try:
        start_photo_sync()
    except Exception as e:
        print(f"bridge: photo sync not started: {e}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
