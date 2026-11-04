"""MQTT side of ha-kiosk-bridge: publishes this panel to Home Assistant with MQTT discovery.

Enabled when MQTT_HOST is set in kiosk.env. One HA device per panel ("<room> Brilliant panel"):

  light   Wired load                on/off + brightness, controlled over the Brilliant bus
  sensor  Load power / temperature  from the dimmer
  sensor  Faceplate / CPU temp      diagnostics
  binary  Motion                    on when Brilliant's motion logic turns its screen on
  sensor  Display state             awake / photos / dim / asleep, plus backlight and room light level
  switch  Screensaver, Night mode, Motion wake, Smart dimming   (runtime overrides)
  number  Day brightness            backlight level when awake (1-15)
  button  Wake screen
  event   Gesture                   every multi-finger gesture, for automations

Runtime overrides are written to /var/lib/ha-kiosk/overrides.env (read after kiosk.env) and the
display service is restarted to apply them.
"""
import json
import os
import re
import struct
import subprocess
import threading
import time

import paho.mqtt.client as mqtt

STATE_DIR = "/run/ha-kiosk-display"
SHADOW = "/run/ha-kiosk-shadow"
OVERRIDES = "/var/lib/ha-kiosk/overrides.env"
BL = "/sys/class/backlight/backlight_lcd"

GESTURES = ["2_tap", "3_tap"] + [f"{n}_swipe_{d}" for n in (2, 3) for d in ("left", "right", "up", "down")]


def read(path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


def read_env(path):
    out = {}
    for line in read(path).splitlines():
        m = re.match(r'\s*([A-Z_][A-Z0-9_]*)\s*=\s*"?([^"\n#]*)"?', line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


class KioskMqtt:
    def __init__(self, env, control, bus_info):
        self.env, self.control = env, control
        self.dev_id = bus_info["device_id"]
        self.room = bus_info.get("room") or "Brilliant"
        self.node = "brilliant_" + self.dev_id[:12]
        self.base = f"{env.get('MQTT_BASE', 'brilliant_kiosk')}/{self.node}"
        self.disc = env.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
        self.last = {}
        self.motion_until = 0.0
        self.brilliant_on = None
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self.node}_kiosk")
        if env.get("MQTT_USER"):
            c.username_pw_set(env["MQTT_USER"], env.get("MQTT_PASSWORD", ""))
        c.will_set(f"{self.base}/availability", "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.reconnect_delay_set(2, 60)
        self.c = c

    # ---------- discovery ----------
    def _device(self):
        return {"identifiers": [self.node], "name": f"{self.room} Brilliant panel",
                "manufacturer": "Brilliant", "model": "Brilliant Control (HA kiosk)"}

    def _cfg(self, component, key, extra):
        payload = {"unique_id": f"{self.node}_{key}", "object_id": f"{self.node}_{key}",
                   "availability_topic": f"{self.base}/availability", "device": self._device()}
        payload.update(extra)
        self.c.publish(f"{self.disc}/{component}/{self.node}/{key}/config", json.dumps(payload), retain=True)

    def publish_discovery(self):
        b = self.base
        self._cfg("light", "load", {"name": "Wired light", "schema": "json", "brightness": True,
                                    "brightness_scale": 100, "state_topic": f"{b}/load/state",
                                    "command_topic": f"{b}/load/set"})
        for key, name, unit, dc, cat in (
                ("load_power", "Load power", "W", "power", None),
                ("load_temperature", "Dimmer temperature", "°C", "temperature", "diagnostic"),
                ("faceplate_temperature", "Faceplate temperature", "°C", "temperature", "diagnostic"),
                ("cpu_temperature", "CPU temperature", "°C", "temperature", "diagnostic")):
            extra = {"name": name, "state_topic": f"{b}/{key}", "unit_of_measurement": unit,
                     "device_class": dc, "state_class": "measurement"}
            if cat:
                extra["entity_category"] = cat
            self._cfg("sensor", key, extra)
        self._cfg("binary_sensor", "motion", {"name": "Motion", "device_class": "motion",
                                              "state_topic": f"{b}/motion"})
        self._cfg("sensor", "display_state", {"name": "Display", "state_topic": f"{b}/display_state",
                                              "icon": "mdi:tablet-dashboard"})
        self._cfg("sensor", "backlight", {"name": "Backlight", "state_topic": f"{b}/backlight",
                                          "icon": "mdi:brightness-6", "entity_category": "diagnostic"})
        self._cfg("sensor", "room_light", {"name": "Room light level", "state_topic": f"{b}/room_light",
                                           "unit_of_measurement": "%", "icon": "mdi:theme-light-dark"})
        for key, name, icon in (("screensaver", "Screensaver", "mdi:image-multiple"),
                                ("night_mode", "Night mode", "mdi:weather-night"),
                                ("motion_wake", "Motion wake", "mdi:motion-sensor"),
                                ("smart_dim", "Smart dimming", "mdi:brightness-auto")):
            self._cfg("switch", key, {"name": name, "icon": icon, "state_topic": f"{b}/{key}",
                                      "command_topic": f"{b}/{key}/set", "entity_category": "config"})
        self._cfg("number", "day_level", {"name": "Day brightness", "icon": "mdi:brightness-7",
                                          "min": 1, "max": 15, "step": 1, "mode": "slider",
                                          "state_topic": f"{b}/day_level",
                                          "command_topic": f"{b}/day_level/set", "entity_category": "config"})
        self._cfg("button", "wake", {"name": "Wake screen", "icon": "mdi:gesture-tap",
                                     "command_topic": f"{b}/wake/press"})
        self._cfg("event", "gesture", {"name": "Gesture", "state_topic": f"{b}/gesture",
                                       "event_types": GESTURES, "icon": "mdi:gesture"})
        self.last = {}   # force a full state publish

    # ---------- connection ----------
    def start(self):
        self.c.connect_async(self.env["MQTT_HOST"], int(self.env.get("MQTT_PORT", "1883")), keepalive=30)
        self.c.loop_start()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _on_connect(self, client, userdata, flags, reason, props=None):
        if reason.is_failure:
            print(f"mqtt: connect failed: {reason}", flush=True)
            return
        print(f"mqtt: connected to {self.env['MQTT_HOST']} as {self.node} ({self.room})", flush=True)
        client.subscribe(f"{self.base}/+/set")
        client.subscribe(f"{self.base}/wake/press")
        client.subscribe(f"{self.disc}/status")         # HA restarted -> resend discovery
        self.publish_discovery()
        client.publish(f"{self.base}/availability", "online", retain=True)

    def _on_message(self, client, userdata, msg):
        topic, payload = msg.topic, msg.payload.decode(errors="replace").strip()
        try:
            if topic == f"{self.disc}/status":
                if payload == "online":
                    self.publish_discovery()
            elif topic == f"{self.base}/load/set":
                cmd = json.loads(payload)
                req = {}
                if "brightness" in cmd:
                    req["brightness"] = cmd["brightness"]
                elif cmd.get("state") in ("ON", "OFF"):
                    req["on"] = cmd["state"] == "ON"
                self.control.apply(req)
                self.last.pop("load", None)
            elif topic == f"{self.base}/wake/press":
                wake_screen()
            elif topic == f"{self.base}/day_level/set":
                set_override("DAY_LEVEL", str(max(1, min(15, int(float(payload))))))
            else:
                key = topic[len(self.base) + 1:-len("/set")]
                var = {"screensaver": "SCREENSAVER", "night_mode": "NIGHT_MODE",
                       "motion_wake": "MOTION_WAKE", "smart_dim": "SMART_DIM"}.get(key)
                if var:
                    on = payload.upper() == "ON"
                    value = ("day" if on else "off") if var == "MOTION_WAKE" else ("1" if on else "0")
                    set_override(var, value)
        except Exception as e:
            print(f"mqtt: {topic}: {e}", flush=True)

    # ---------- state ----------
    def _pub(self, key, value):
        if self.last.get(key) != value:
            self.last[key] = value
            self.c.publish(f"{self.base}/{key}", value, retain=True)

    def gesture(self, name):
        if name in GESTURES:
            self.c.publish(f"{self.base}/gesture", json.dumps({"event_type": name}))

    def _poll_loop(self):
        while True:
            try:
                self._poll()
            except Exception as e:
                print(f"mqtt: poll: {e}", flush=True)
            time.sleep(1)

    def _poll(self):
        if not self.c.is_connected():
            return
        v = self.control.variables()
        on = v["on"] == "1"
        bri = round(100 * int(v["intensity"]) / int(v.get("max_intensity_value") or 1000))
        self._pub("load/state", json.dumps({"state": "ON" if on else "OFF", "brightness": bri}))
        self._pub("load_power", v.get("power", ""))
        self._pub("load_temperature", v.get("temperature", ""))
        hw = self.control.variables("hardware_peripheral")
        self._pub("cpu_temperature", hw.get("cpu_temperature", ""))
        fp = self.control.variables("faceplate_peripheral")
        self._pub("faceplate_temperature", fp.get("hottest_internal_temperature", ""))

        # Motion: Brilliant's motion logic turning *its* (shadow) screen on = someone walked up.
        b_on = read(f"{SHADOW}/blank") != "1" and read(f"{SHADOW}/brightness", "0") not in ("", "0")
        if self.brilliant_on is False and b_on:
            self.motion_until = time.time() + int(self.env.get("MOTION_CLEAR_SECS", "60"))
        self.brilliant_on = b_on
        self._pub("motion", "ON" if time.time() < self.motion_until else "OFF")

        cfg = {**read_env("/etc/ha-kiosk/kiosk.env"), **read_env(OVERRIDES)}
        self._pub("display_state", read(f"{STATE_DIR}/state", "unknown"))
        self._pub("backlight", read(f"{BL}/brightness"))
        levels = read(f"{STATE_DIR}/levels").split()
        if levels:
            lo, hi = int(cfg.get("MIN_LEVEL", 5)), int(cfg.get("DAY_LEVEL", 15))
            self._pub("room_light", str(round(100 * (int(levels[0]) - lo) / max(1, hi - lo))))
        self._pub("screensaver", "ON" if cfg.get("SCREENSAVER", "1") == "1" else "OFF")
        self._pub("night_mode", "ON" if cfg.get("NIGHT_MODE", "1") == "1" else "OFF")
        self._pub("motion_wake", "OFF" if cfg.get("MOTION_WAKE", "day") in ("off", "0") else "ON")
        self._pub("smart_dim", "ON" if cfg.get("SMART_DIM", "1") == "1" else "OFF")
        self._pub("day_level", cfg.get("DAY_LEVEL", "15"))


def set_override(var, value):
    """Persist a runtime setting and restart the display service so it takes effect."""
    cur = read_env(OVERRIDES)
    if cur.get(var) == value:
        return
    cur[var] = value
    os.makedirs(os.path.dirname(OVERRIDES), exist_ok=True)
    with open(OVERRIDES + ".tmp", "w", encoding="utf-8") as f:
        f.write("# Written by ha-kiosk-bridge (Home Assistant / MQTT). Overrides kiosk.env.\n")
        for k, v in sorted(cur.items()):
            f.write(f"{k}={v}\n")
    os.replace(OVERRIDES + ".tmp", OVERRIDES)
    print(f"mqtt: {var}={value}", flush=True)
    subprocess.run(["systemctl", "restart", "ha-kiosk-display"], check=False)


def wake_screen():
    """Wake the display the same way a touch does: one harmless sync event on the touchscreen."""
    with open("/dev/input/event0", "wb", buffering=0) as f:
        f.write(struct.pack("llHHi", 0, 0, 0, 0, 0))
