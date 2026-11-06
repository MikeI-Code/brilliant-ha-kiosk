"""MQTT side of ha-kiosk-bridge: publishes this panel to Home Assistant with MQTT discovery.

Enabled when MQTT_HOST is set in kiosk.env. One HA device per panel ("<room> Brilliant panel"):

  light   Wired load                on/off + brightness, controlled over the Brilliant bus
  sensor  Load power / temperature  per gangbox_peripheral_N (the dimmer)
  sensor  Dim levels, dimming edge, calibration   per load (diagnostic, disabled by default)
  sensor  Faceplate / CPU temp, firmware, uptime  diagnostics
  binary  HomeKit paired / enabled  diagnostics
  sensor  Shelly BLE sensors        count + decoded readings as attributes (Brilliant's own scan)
  binary  Motion                    on when Brilliant's motion logic turns its screen on
  sensor  Display state             awake / photos / dim / asleep, plus backlight and room light level
  switch  Screensaver, Night mode, Motion wake, Smart dimming   (runtime overrides)
  number  Day brightness            backlight level when awake (1-15)
  button  Wake screen
  event   Gesture                   every multi-finger gesture, for automations
  sensor  Privacy                   open / mic_locked / camera_locked / mic_and_camera_locked
                                    (ALLOW_MIC / ALLOW_CAMERA, plus PANEL_GROUP; diagnostic)

Runtime overrides are written to /var/lib/ha-kiosk/overrides.env (read after kiosk.env) and the
display service is restarted to apply them.

Everything read from Brilliant's bus here is read-only: no gangbox, dimming, HomeKit or BLE
variable is ever written.
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


CALIBRATION = {"0": "idle", "1": "running", "2": "success"}   # MinDimlevelCalibrationStatus


def gb_suffix(n):
    """Entity key suffix and name label for gangbox_peripheral_<n> (0 keeps the original ids)."""
    return ("", "") if n == 0 else (f"_{n}", f" {n + 1}")


def deciwatts_to_w(value):
    """gangbox `power` is deciwatts (0.1 W); publish watts. Pass blanks/garbage through."""
    try:
        return str(round(int(value) / 10.0, 1))
    except (TypeError, ValueError):
        return value


def decode_shelly(value):
    """ble_peripheral.shelly_sensor_readings (base64 Thrift) -> {mac: {...}}, or None if undecodable."""
    if not value:
        return {}
    try:
        import lib.serialization as S              # Brilliant's libs (path set up by brilliant_bus)
        from thrift_types.shelly import ttypes as SH
        r = S.deserialize(SH.ShellySensorReadings, value)
        out = {}
        for mac, s in (r.readings_by_mac or {}).items():
            out[mac] = {"motion": s.movement_detected, "contact_open": s.contact_open,
                        "battery": s.battery, "encrypted": s.is_encrypted, "rssi": s.rssi,
                        "last_heard": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s.last_heard_ms / 1000))
                                       if s.last_heard_ms else None)}
        return out
    except Exception as e:
        print(f"mqtt: shelly decode: {e}", flush=True)
        return None


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
        self.peripherals = set()
        self.gangboxes = [0]
        self.next_diag = 0.0
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
        try:
            self.peripherals = set(self.control.peripherals())
        except Exception as e:
            print(f"mqtt: peripheral list: {e}", flush=True)
        self.gangboxes = sorted((int(p.rsplit("_", 1)[1]) for p in self.peripherals
                                 if re.fullmatch(r"gangbox_peripheral_\d+", p)), key=int) or [0]
        sensors = []
        for n in self.gangboxes:
            sfx, lbl = gb_suffix(n)
            # `power` is reported in deciwatts (0.1 W): a 24 W (6x4 W LED) load read 134 at ~60%
            # dim and 172 higher, i.e. 13.4 W / 17.2 W. We divide by 10 and publish watts.
            # Brilliant only pushes changes above a threshold, so this updates coarsely.
            sensors += [(f"load_power{sfx}", f"Load{lbl} power", "W", "power", None),
                        (f"load_temperature{sfx}", f"Gangbox{lbl} temperature", "°C", "temperature",
                         "diagnostic")]
        sensors += [("faceplate_temperature", "Faceplate temperature", "°C", "temperature", "diagnostic"),
                    ("cpu_temperature", "CPU temperature", "°C", "temperature", "diagnostic")]
        for key, name, unit, dc, cat in sensors:
            extra = {"name": name, "state_topic": f"{b}/{key}", "unit_of_measurement": unit,
                     "device_class": dc, "state_class": "measurement"}
            if cat:
                extra["entity_category"] = cat
            self._cfg("sensor", key, extra)
        # Read-only diagnostics (polled every DIAG_SECS)
        self._cfg("sensor", "firmware_version", {"name": "Brilliant firmware", "icon": "mdi:chip",
                                                 "state_topic": f"{b}/firmware_version",
                                                 "entity_category": "diagnostic"})
        self._cfg("sensor", "boot_time", {"name": "Last boot", "device_class": "timestamp",
                                          "state_topic": f"{b}/boot_time", "entity_category": "diagnostic"})
        for n in self.gangboxes:   # dimming calibration, read-only, hidden until enabled in HA
            sfx, lbl = gb_suffix(n)
            for key, name, icon in ((f"min_dim_level{sfx}", f"Load{lbl} minimum dim level", "mdi:brightness-4"),
                                    (f"max_dim_level{sfx}", f"Load{lbl} maximum dim level", "mdi:brightness-7"),
                                    (f"dimming_edge{sfx}", f"Load{lbl} dimming edge", "mdi:sine-wave"),
                                    (f"dim_calibration{sfx}", f"Load{lbl} dim calibration", "mdi:tune")):
                self._cfg("sensor", key, {"name": name, "icon": icon, "state_topic": f"{b}/{key}",
                                          "entity_category": "diagnostic", "enabled_by_default": False})
        if "homekit_peripheral" in self.peripherals:
            for key, name in (("homekit_paired", "HomeKit paired"), ("homekit_enabled", "HomeKit enabled")):
                self._cfg("binary_sensor", key, {"name": name, "icon": "mdi:apple",
                                                 "state_topic": f"{b}/{key}", "entity_category": "diagnostic"})
        if "ble_peripheral" in self.peripherals:
            self._cfg("sensor", "shelly_ble", {"name": "Shelly BLE sensors", "icon": "mdi:bluetooth",
                                               "state_topic": f"{b}/shelly_ble", "state_class": "measurement",
                                               "json_attributes_topic": f"{b}/shelly_ble/attributes",
                                               "entity_category": "diagnostic"})
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
        self._cfg("sensor", "privacy", {"name": "Privacy", "icon": "mdi:shield-lock",
                                        "entity_category": "diagnostic", "device_class": "enum",
                                        "options": ["open", "mic_locked", "camera_locked",
                                                    "mic_and_camera_locked"],
                                        "state_topic": f"{b}/privacy",
                                        "json_attributes_topic": f"{b}/privacy/attributes"})
        self.last = {}   # force a full state publish
        self.next_diag = 0.0
        self.publish_privacy()

    def publish_privacy(self):
        """Mic / camera locks (ALLOW_MIC / ALLOW_CAMERA = 0, deny wins at install) and the group."""
        deny = ("0", "off", "false", "no")
        mic = str(self.env.get("ALLOW_MIC", "")).strip().lower() in deny
        cam = str(self.env.get("ALLOW_CAMERA", "")).strip().lower() in deny
        state = ("mic_and_camera_locked" if mic and cam else "mic_locked" if mic
                 else "camera_locked" if cam else "open")
        self._pub("privacy", state)
        self._pub("privacy/attributes", json.dumps({
            "mic": "locked" if mic else "allowed", "camera": "locked" if cam else "allowed",
            "group": str(self.env.get("PANEL_GROUP", "")).strip()}, sort_keys=True))

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

    # Brilliant's Ring integration (read-only), published by the panel that hosts it.
    def ring_alarm(self, state):
        if not getattr(self, "_ring_alarm_disc", False):
            self._ring_alarm_disc = True
            self._cfg("sensor", "ring_alarm", {
                "name": "Ring alarm", "state_topic": f"{self.base}/ring_alarm", "icon": "mdi:shield-home",
                "device_class": "enum",
                "options": ["unknown", "disarmed", "armed_home", "armed_away", "armed_night", "disabled",
                            "disarming", "disarmed_not_ready", "fire_alarm", "carbon_monoxide_alarm",
                            "medical_alarm", "intrusion_alarm", "armed_away_bypass", "armed_home_bypass",
                            "police_alarm", "disarmed_bypass"]})
        self._pub("ring_alarm", state)

    def brilliant_lock(self, lk):
        key = "lock_" + "".join(c if c.isalnum() else "_" for c in lk["name"].lower())
        if key not in getattr(self, "_lock_disc", set()):
            self._lock_disc = getattr(self, "_lock_disc", set()) | {key}
            self._cfg("sensor", key, {"name": f"{lk['name'].title()} lock", "icon": "mdi:lock",
                                      "state_topic": f"{self.base}/{key}", "device_class": "enum",
                                      "options": ["locked", "unlocked", "jammed"]})
        self._pub(key, lk["state"])

    def security_controls(self, st):
        """'ok' or 'limited', with each ability and the reason in the attributes (per panel)."""
        if not getattr(self, "_sec_disc", False):
            self._sec_disc = True
            self._cfg("sensor", "security_controls", {
                "name": "Security controls", "icon": "mdi:shield-check", "entity_category": "diagnostic",
                "state_topic": f"{self.base}/security_controls", "device_class": "enum", "options": ["ok", "limited"],
                "json_attributes_topic": f"{self.base}/security_controls/attributes"})
        self._pub("security_controls", st["status"])
        attrs = {"arm": st["caps"]["arm"], "disarm": st["caps"]["disarm"], "lock": st["caps"]["lock"],
                 "reasons": st["reasons"], "firmware": st["firmware"],
                 "disarm_verified_on_this_firmware": not st["disarm_unverified"],
                 "last_disarm": st["last_disarm"], "paused_until": st["lockout_until"]}
        self._pub("security_controls/attributes", json.dumps(attrs, sort_keys=True))

    def ring_doorbell(self, name):
        if not getattr(self, "_ring_bell_disc", False):
            self._ring_bell_disc = True
            self._cfg("event", "ring_doorbell", {"name": "Ring doorbell", "device_class": "doorbell",
                                                 "state_topic": f"{self.base}/ring_doorbell",
                                                 "event_types": ["press"]})
        self.c.publish(f"{self.base}/ring_doorbell", json.dumps({"event_type": "press", "doorbell": name}))

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

    def _pub_gangbox(self, n, v):
        sfx = gb_suffix(n)[0]
        self._pub(f"load_power{sfx}", deciwatts_to_w(v.get("power", "")))
        self._pub(f"load_temperature{sfx}", v.get("temperature", ""))
        self._pub(f"min_dim_level{sfx}", v.get("minimum_dim_level", ""))
        self._pub(f"max_dim_level{sfx}", v.get("maximum_dim_level", ""))
        # Raw flag: which of 0/1 is leading vs trailing edge is not documented.
        self._pub(f"dimming_edge{sfx}", v.get("dimming_edge", ""))
        cal = v.get("min_dim_level_calibration_status", "")
        self._pub(f"dim_calibration{sfx}", CALIBRATION.get(cal, cal))

    def _poll_diagnostics(self):
        for n in self.gangboxes:
            try:
                self._pub_gangbox(n, self.control.variables(f"gangbox_peripheral_{n}"))
            except Exception as e:
                print(f"mqtt: gangbox_peripheral_{n}: {e}", flush=True)
        hw = self.control.variables("hardware_peripheral")
        self._pub("cpu_temperature", hw.get("cpu_temperature", ""))
        self._pub("firmware_version", hw.get("current_release_tag", ""))
        fp = self.control.variables("faceplate_peripheral")
        self._pub("faceplate_temperature", fp.get("hottest_internal_temperature", ""))
        try:   # boot time, rounded to the minute so it doesn't jitter
            boot = int((time.time() - float(read("/proc/uptime", "0").split()[0])) // 60 * 60)
            self._pub("boot_time", time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(boot)))
        except (ValueError, IndexError):
            pass
        if "homekit_peripheral" in self.peripherals:
            hk = self.control.variables("homekit_peripheral")
            self._pub("homekit_paired", "ON" if hk.get("paired") == "1" else "OFF")
            self._pub("homekit_enabled", "ON" if hk.get("adk_enabled") == "1" else "OFF")
        if "ble_peripheral" in self.peripherals:
            readings = decode_shelly(self.control.variables("ble_peripheral").get("shelly_sensor_readings", ""))
            if readings is not None:
                self._pub("shelly_ble/attributes", json.dumps({"sensors": readings}, sort_keys=True))
                self._pub("shelly_ble", str(len(readings)))

    def _poll(self):
        if not self.c.is_connected():
            return
        v = self.control.variables()
        on = v["on"] == "1"
        bri = round(100 * int(v["intensity"]) / int(v.get("max_intensity_value") or 1000))
        self._pub("load/state", json.dumps({"state": "ON" if on else "OFF", "brightness": bri}))
        # The bridge's own load is read every second anyway, so its power shows up in HA at once;
        # the other gangboxes and the diagnostics are polled every DIAG_SECS (Brilliant's bus only
        # pushes power changes above ~40 W / 10 %, temperature is effectively poll-only).
        m = re.fullmatch(r"gangbox_peripheral_(\d+)", os.environ.get("BRIDGE_LOAD", "gangbox_peripheral_0"))
        if m:
            self._pub_gangbox(int(m.group(1)), v)
        if time.time() >= self.next_diag:
            self.next_diag = time.time() + int(self.env.get("DIAG_SECS", "60"))
            self._poll_diagnostics()

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
