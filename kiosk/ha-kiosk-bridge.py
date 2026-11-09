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
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/etc/ha-kiosk")
from brilliant_bus import Bus  # noqa: E402


def _load_common():
    """ha-kiosk-common.py (shared env parsing), installed flat next to this file."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ha_kiosk_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ha-kiosk-common.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


common = _load_common()

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

    def device(self, device_id):
        """Another bus device (e.g. ring_virtual_device), read-only; None if it doesn't exist."""
        return self._with_bus(lambda b: b.call("get_device", device_id=device_id))

    def my_id(self):
        return self._with_bus(lambda b: b.device_id)

    def set_on(self, device_id, peripheral, values):
        """Check-and-set write to a peripheral of another bus device (as Brilliant's own UI does).
        Every variable must exist and be externally settable; one request = one atomic write."""
        def go(b):
            dev = b.call("get_device", device_id=device_id)
            p = (dev.peripherals or {}).get(peripheral) if dev else None
            if not p:
                raise ValueError(f"no {device_id}/{peripheral}")
            cur = p.variables or {}
            bad = [k for k in values if k not in cur or not cur[k].externally_settable]
            if bad:
                raise ValueError("not settable: " + ", ".join(bad))
            return b.call("set_variables_request", device_id=device_id, peripheral_name=peripheral,
                          variables=values, last_set_timestamps={k: cur[k].timestamp for k in values})
        return self._with_bus(go)

    def var(self, device_id, peripheral, name):
        dev = self.device(device_id)
        p = (dev.peripherals or {}).get(peripheral) if dev else None
        v = (p.variables or {}).get(name) if p else None
        return v.value if v else None

    def peripherals(self):
        """Names of all peripherals on this panel (read-only)."""
        return sorted(self._with_bus(lambda b: b.device().peripherals) or {})

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
        if self.path == "/ring":              # doorbell presses and alarm mode (see RingWatch)
            return self._reply(200, ring.status())
        if self.path != "/load":
            return self._reply(404, {"error": "not found"})
        try:
            self._reply(200, control.state())
        except Exception as e:
            self._reply(503, {"error": str(e)})

    def do_POST(self):
        if self.path in ("/lock", "/alarm"):   # door lock / Ring Alarm control from the panel UI
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
                return self._reply(200, lock_command(req) if self.path == "/lock" else alarm_command(req))
            except Exception as e:
                return self._reply(200, {"ok": False, "error": str(e)[:160]})
        if self.path == "/wake":             # the UI wakes the screen (doorbell pop-up)
            try:                             # same as a touch: one harmless sync event on the touchscreen
                with open("/dev/input/event0", "wb", buffering=0) as f:
                    f.write(struct.pack("llHHi", 0, 0, 0, 0, 0))
                return self._reply(200, {"ok": True})
            except OSError as e:
                return self._reply(503, {"error": str(e)})
        if self.path == "/heartbeat":        # the UI is up; ha-kiosk-setup checks this after an install
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
                os.makedirs("/run/ha-kiosk", exist_ok=True)
                with open("/run/ha-kiosk/heartbeat.json.tmp", "w", encoding="utf-8") as f:
                    json.dump({"time": time.time(), "ha": str(req.get("ha", ""))[:20]}, f)
                os.replace("/run/ha-kiosk/heartbeat.json.tmp", "/run/ha-kiosk/heartbeat.json")
            except Exception:
                pass
            return self._reply(200, {"ok": True})
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


# Ring Alarm modes (Brilliant's security_system.SystemMode) -> HA-style state names.
ALARM_MODES = {0: "unknown", 1: "disarmed", 2: "armed_home", 3: "armed_away", 4: "armed_night",
               5: "disabled", 6: "disarming", 7: "disarmed_not_ready", 8: "fire_alarm",
               9: "carbon_monoxide_alarm", 10: "medical_alarm", 11: "intrusion_alarm",
               12: "armed_away_bypass", 13: "armed_home_bypass", 14: "police_alarm",
               15: "disarmed_bypass"}


def read_kiosk_env():
    """This panel's settings: kiosk.env, then overrides.env, then the process environment
    (each file read in order, so the last assignment wins, like systemd)."""
    env = common.read_settings()
    for k in ("SECURITY_ACCESS", "SECURITY_DEVICES"):
        if k not in env and k in os.environ:
            env[k] = os.environ[k]
    return env


class SecurityAccess:
    """SECURITY_ACCESS=control|view|off (default control) and SECURITY_DEVICES (comma list of
    names, case-insensitive; empty = all). Filters what GET /ring shows and what this panel may
    control. The owner panel's MQTT publishing to Home Assistant is not filtered."""

    def __init__(self, env=None):
        env = read_kiosk_env() if env is None else env
        mode = (env.get("SECURITY_ACCESS") or "control").strip().lower()
        self.mode = mode if mode in ("control", "view", "off") else "control"
        self.devices = {d.strip().lower() for d in (env.get("SECURITY_DEVICES") or "").split(",") if d.strip()}

    def device_ok(self, name):
        if self.mode == "off":
            return False
        return not self.devices or str(name or "").strip().lower() in self.devices

    def alarm_ok(self, name):
        if self.mode == "off":
            return False
        return not self.devices or "alarm" in self.devices or str(name or "").strip().lower() in self.devices

    def check_control(self, allowed):
        if self.mode != "control":
            raise ValueError("view only on this panel")
        if not allowed:
            raise ValueError("not allowed on this panel")


access = SecurityAccess({})            # replaced with the real settings in main()


def wake_screen():
    """Same as a touch: one harmless sync event on the touchscreen (the display manager wakes)."""
    with open("/dev/input/event0", "wb", buffering=0) as f:
        f.write(struct.pack("llHHi", 0, 0, 0, 0, 0))


class RingWatch:
    """Read-only view of Brilliant's own Ring integration (linked in the Brilliant app).

    Polls the bus device "ring_virtual_device" once a second: a doorbell's last_ding changes on
    every press (we wake the screen; the UI opens the camera pop-up via GET /ring), and the alarm
    base station reports current_system_mode. Nothing is ever written to the Ring devices."""

    def __init__(self):
        self.lock = threading.Lock()
        self.seq, self.doorbell, self.alarm = 0, None, None
        self.locks = []                      # Brilliant's Schlage integration (read-only)
        self.devices = {}                    # Ring doorbells/cameras: id -> {name, kind, last_press}
        self.lock_owner = False
        self.dings = {}                      # peripheral id -> last_ding seen
        self.owner = False                   # this panel hosts the Ring integration (publishes to HA)

    def status(self):
        """What this panel may see (SECURITY_ACCESS / SECURITY_DEVICES)."""
        with self.lock:
            alarm = self.alarm if self.alarm and access.alarm_ok(self.alarm.get("name")) else None
            locks = [lk for lk in self.locks if access.device_ok(lk.get("name"))]
            devices = [d for d in self.devices.values() if access.device_ok(d.get("name"))]
            doorbell = self.doorbell if self.doorbell and access.device_ok(self.doorbell.get("name")) else None
            sec = dict(health.status(), access=access.mode)
            return {"seq": self.seq, "doorbell": doorbell, "alarm": alarm, "locks": locks, "security": sec,
                    "devices": sorted(devices, key=lambda d: (d["kind"] != "doorbell", d["name"]))}

    def raw_alarm(self):
        with self.lock:
            return self.alarm

    def raw_lock(self, pid):
        with self.lock:
            return next((lk for lk in self.locks if lk["id"] == pid), None)

    def _check_owner(self):
        try:
            cfg = control.device("configuration_virtual_device")
            me = control.my_id()

            def owns(name):
                p = (cfg.peripherals or {}).get(name) if cfg else None
                o = p and (p.variables or {}).get("owner")
                return bool(o) and o.value == me
            self.owner, self.lock_owner = owns("ring_configuration"), owns("schlage_configuration")
        except Exception:
            self.owner = self.lock_owner = False

    def run(self):
        next_owner = next_lock = 0
        while True:
            try:
                if time.time() > next_owner:
                    self._check_owner()
                    next_owner = time.time() + 600
                if time.time() > next_lock:
                    next_lock = time.time() + 5
                    self._scan_locks()
                dev = control.device("ring_virtual_device")
                if not dev or not dev.peripherals:
                    time.sleep(30)           # no Ring linked in the Brilliant app
                    continue
                self._scan(dev.peripherals)
                time.sleep(1)
            except Exception as e:
                print(f"ring: {e}", flush=True)
                time.sleep(15)

    def _scan_locks(self):
        try:
            dev = control.device("schlage")
        except Exception:
            dev = None
        locks = []
        for pid, p in ((dev.peripherals or {}).items() if dev else []):
            if p.peripheral_type != 1:                                 # LOCK
                continue
            v = {k: (x.value or "") for k, x in (p.variables or {}).items()}
            status = {"0": "unknown", "1": "normal", "2": "jammed", "3": "deauthorized"}.get(v.get("lock_status", "0"), "unknown")
            state = "jammed" if status == "jammed" else ("locked" if v.get("locked") == "1" else "unlocked")
            locks.append({"id": pid, "name": (v.get("thirdparty_display_name") or v.get("display_name") or "Lock").strip(),
                          "state": state, "status": status, "battery_low": v.get("battery_status") not in ("", "1")})
        changed = locks != self.locks
        with self.lock:
            self.locks = locks
        for lk in locks:
            if changed:
                print(f"lock: {lk['name']} {lk['state']}", flush=True)
            if mqtt_link and self.lock_owner:
                mqtt_link.brilliant_lock(lk)

    def _scan(self, peripherals):
        for pid, p in peripherals.items():
            v = {k: (x.value or "") for k, x in (p.variables or {}).items()}
            name = (v.get("thirdparty_display_name") or v.get("display_name") or "Doorbell").strip()
            if p.peripheral_type in (2, 59):                          # DOORBELL / CAMERA: for the Security tab
                with self.lock:
                    d = self.devices.setdefault(pid, {"name": name, "kind": "doorbell" if p.peripheral_type == 2 else "camera",
                                                      "last_press": None})
                    d["name"] = name
            if p.peripheral_type == 2 and "last_ding" in v:            # DOORBELL
                prev = self.dings.get(pid)
                self.dings[pid] = v["last_ding"]
                if prev is not None and v["last_ding"] != prev:
                    print(f"ring: doorbell pressed: {name}", flush=True)
                    ts = int(time.time())
                    with self.lock:
                        if pid in self.devices:
                            self.devices[pid]["last_press"] = ts
                    if access.device_ok(name):         # only doorbells this panel may see wake it
                        try:
                            wake_screen()
                        except OSError:
                            pass
                        with self.lock:
                            self.seq += 1
                            self.doorbell = {"name": name, "ts": ts}
                    if mqtt_link and self.owner:
                        mqtt_link.ring_doorbell(name)
            elif p.peripheral_type == 89 and "current_system_mode" in v:  # SECURITY_SYSTEM
                try:
                    mode = int(v["current_system_mode"])
                except ValueError:
                    mode = 0
                state = ALARM_MODES.get(mode, "unknown")
                changed = not self.alarm or self.alarm["state"] != state
                with self.lock:
                    self.alarm = {"id": pid, "name": name or "Alarm", "mode": mode, "state": state}
                if changed:
                    print(f"ring: alarm {state}", flush=True)
                if mqtt_link and self.owner:
                    mqtt_link.ring_alarm(state)


ring = RingWatch()


# ------------------------------------------------------------------ security controls health
# These controls use Brilliant's internal, undocumented interfaces, which a firmware update may
# change. Each ability (arm, disarm, lock) is checked on its own, without touching the devices,
# at start, every 10 minutes and whenever the firmware version changes. If a check fails, only that
# ability is switched off, the panel says why, and Home Assistant gets a "Security controls" sensor.
SEC_STATE = "/var/lib/ha-kiosk/security.json"     # firmware on which a panel disarm last worked
MAX_FAILS, LOCKOUT_SECS, VERIFY_SECS = 3, 300, 20


class SecurityHealth:
    def __init__(self):
        self.lock = threading.Lock()
        self.caps = {"arm": False, "disarm": False, "lock": False}
        self.reasons = {}
        self.firmware = ""
        self.fails, self.lockout_until, self.last_disarm = 0, 0.0, None
        try:
            self.verified_fw = json.load(open(SEC_STATE)).get("disarm_verified_firmware", "")
        except (OSError, ValueError):
            self.verified_fw = ""

    def status(self):
        with self.lock:
            limited = [k for k, v in self.caps.items() if not v and k in self.reasons]
            locked_out = time.time() < self.lockout_until
            return {"status": "limited" if limited or locked_out else "ok", "caps": dict(self.caps),
                    "reasons": dict(self.reasons), "firmware": self.firmware,
                    "disarm_verified_firmware": self.verified_fw,
                    "disarm_unverified": bool(self.caps["disarm"] and self.verified_fw != self.firmware),
                    "lockout_until": int(self.lockout_until) if locked_out else None,
                    "last_disarm": self.last_disarm}

    def check(self):
        """Read-only compatibility check. Nothing is sent to the alarm or the locks."""
        caps, why = {"arm": False, "disarm": False, "lock": False}, {}
        try:
            self.firmware = (control.variables("hardware_peripheral").get("current_release_tag") or "").strip()
        except Exception:
            pass
        try:
            dev = control.device("ring_virtual_device")
            alarm = [p for p in ((dev.peripherals or {}).values() if dev else []) if p.peripheral_type == 89]
            if alarm:
                v = alarm[0].variables or {}
                mode = v.get("current_system_mode")
                if mode is not None and mode.externally_settable:
                    caps["arm"] = True
                else:
                    why["arm"] = "the alarm's mode can no longer be set"
                code, key = v.get("encrypted_user_code"), v.get("ephemeral_public_key")
                if not (caps["arm"] and code is not None and code.externally_settable and key is not None):
                    why["disarm"] = "the alarm no longer offers coded disarm"
                else:
                    import lib.serialization as S
                    from thrift_types.crypto import ttypes as C
                    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey  # noqa: F401
                    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
                    from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: F401
                    pub = S.deserialize(C.PublicKey, key.value)
                    fields = {f[2] for f in C.EncryptedMessage.thrift_spec if f}
                    if pub.key_type != 1 or len(pub.public_bytes or b"") != 32:
                        why["disarm"] = "the alarm's key format changed"
                    elif not {"algorithm", "public_key", "peer_key_id", "ciphertext", "nonce"} <= fields:
                        why["disarm"] = "the encrypted code format changed"
                    else:
                        caps["disarm"] = True
        except Exception as e:
            why.setdefault("disarm", f"compatibility check failed ({type(e).__name__})")
        try:
            dev = control.device("schlage")
            locks = [p for p in ((dev.peripherals or {}).values() if dev else []) if p.peripheral_type == 1]
            if locks:
                if all((p.variables or {}).get("locked") is not None and p.variables["locked"].externally_settable for p in locks):
                    caps["lock"] = True
                else:
                    why["lock"] = "the locks can no longer be set"
        except Exception as e:
            why["lock"] = f"compatibility check failed ({type(e).__name__})"
        with self.lock:
            changed = caps != self.caps or why != self.reasons
            self.caps, self.reasons = caps, why
        if changed:
            print(f"security: controls {caps} {why or ''} (firmware {self.firmware or '?'})", flush=True)
        self.publish()

    def publish(self):
        if mqtt_link:
            mqtt_link.security_controls(self.status())

    def run(self):
        last_fw = None
        while True:
            try:
                self.check()
                if last_fw is not None and self.firmware != last_fw:
                    print(f"security: firmware changed {last_fw} -> {self.firmware}; disarm unverified until it works", flush=True)
                last_fw = self.firmware
            except Exception as e:
                print(f"security: check: {e}", flush=True)
            time.sleep(600)

    # --- disarm attempts
    def before_disarm(self):
        with self.lock:
            if time.time() < self.lockout_until:
                raise ValueError(f"too many failed attempts; panel disarm paused for {int(self.lockout_until - time.time()) // 60 + 1} min")
            if not self.caps["disarm"]:
                raise ValueError("disarm from the panel is unavailable: " + self.reasons.get("disarm", "not supported") +
                                 ". Use the Ring app or keypad.")

    def watch_disarm(self):
        """After a disarm is sent: accepted if the alarm reports disarmed within VERIFY_SECS."""
        def go():
            t0 = time.time()
            while time.time() - t0 < VERIFY_SECS:
                time.sleep(1)
                a = ring.raw_alarm() or {}
                if a.get("state") in ("disarmed", "disarmed_bypass", "disarmed_not_ready"):
                    with self.lock:
                        self.fails, self.last_disarm, self.verified_fw = 0, "accepted", self.firmware
                    try:
                        json.dump({"disarm_verified_firmware": self.firmware}, open(SEC_STATE, "w"))
                    except OSError:
                        pass
                    print("security: disarm accepted", flush=True)
                    self.publish()
                    return
            with self.lock:
                self.fails += 1
                self.last_disarm = "not_accepted"
                if self.fails >= MAX_FAILS:
                    self.lockout_until = time.time() + LOCKOUT_SECS
            print(f"security: disarm not accepted ({self.fails} in a row)", flush=True)
            self.publish()
        threading.Thread(target=go, daemon=True).start()


health = SecurityHealth()


def lock_command(req):
    """{"id": <schlage peripheral id>, "locked": true|false} -> Brilliant's Schlage integration."""
    pid = str(req.get("id", ""))
    lk = ring.raw_lock(pid)
    access.check_control(lk is not None and access.device_ok(lk.get("name")))
    if not health.caps["lock"]:
        raise ValueError("lock control is unavailable: " + health.reasons.get("lock", "no lock") + ". Use the lock's own app.")
    if not pid or not isinstance(req.get("locked"), bool):
        raise ValueError("need id and locked")
    control.set_on("schlage", pid, {"locked": "1" if req["locked"] else "0"})
    print(f"lock: {'lock' if req['locked'] else 'unlock'} requested from the panel", flush=True)
    return {"ok": True}


def _encrypt_pin(pin, public_key_value):
    """Encrypt the alarm PIN for the alarm's current one-time key, in Brilliant's EncryptedMessage
    format: X25519 key agreement, HKDF-SHA256 (32 bytes, no salt/info), AES-GCM, 12-byte nonce.
    (Verified against Brilliant's own EphemeralAsymmetricKeyCipher.decrypt.)"""
    import lib.serialization as S
    from thrift_types.crypto import ttypes as C
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    peer = S.deserialize(C.PublicKey, public_key_value)
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(peer.public_bytes))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"").derive(shared)
    nonce = os.urandom(12)
    mine = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    msg = C.EncryptedMessage(algorithm=C.EncryptionAlgorithm.AES_GCM if hasattr(C, "EncryptionAlgorithm") else 1,
                             public_key=C.PublicKey(key_id=None, key_type=1, public_bytes=mine),
                             peer_key_id=peer.key_id, ciphertext=AESGCM(key).encrypt(nonce, pin.encode(), None),
                             nonce=nonce)
    return S.serialize(msg)


def alarm_command(req):
    """{"mode": "armed_home"|"armed_away"} or {"mode": "disarmed", "pin": "1234"} -> Ring Alarm via
    Brilliant. The PIN is used for this one request only (never logged or kept)."""
    a = ring.raw_alarm()
    access.check_control(bool(a) and access.alarm_ok(a.get("name")))
    if not a:
        raise ValueError("no Ring alarm on this panel")
    dev, pid = "ring_virtual_device", a.get("id")
    mode = req.get("mode")
    if mode in ("armed_home", "armed_away"):
        if not health.caps["arm"]:
            raise ValueError("arming from the panel is unavailable: " + health.reasons.get("arm", "not supported") + ". Use the Ring app.")
        if a["state"] not in ("disarmed", "disarmed_bypass"):
            raise ValueError("disarm first")
        control.set_on(dev, pid, {"current_system_mode": "2" if mode == "armed_home" else "3"})
        print(f"alarm: {mode} requested from the panel", flush=True)
        return {"ok": True}
    if mode == "disarmed":
        pin = str(req.get("pin", ""))
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise ValueError("enter the alarm code")
        health.before_disarm()
        pub = control.var(dev, pid, "ephemeral_public_key")
        if not pub:
            raise ValueError("alarm key not available yet")
        control.set_on(dev, pid, {"current_system_mode": "1", "encrypted_user_code": _encrypt_pin(pin, pub)})
        del pin
        print("alarm: disarm requested from the panel", flush=True)
        health.watch_disarm()
        return {"ok": True}
    raise ValueError("unknown mode")


def start_mqtt():
    """Publish this panel to Home Assistant over MQTT if MQTT_HOST is configured."""
    global mqtt_link
    import importlib.util
    spec = importlib.util.spec_from_file_location("ha_kiosk_mqtt", "/etc/ha-kiosk/ha-kiosk-mqtt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = {**common.read_env("/etc/ha-kiosk/kiosk.env"), **common.read_env(mod.OVERRIDES)}
    if env.get("KIOSK_MQTT", "1").strip().lower() in ("0", "off", "false", "no"):
        print("bridge: MQTT off (KIOSK_MQTT=0; another tool publishes this panel)", flush=True)
        return
    if env.get("KIOSK_MQTT", "1").strip().lower() == "kiosk":
        print("bridge: MQTT kiosk mode (kiosk entities only"
              + (f", joining device {env['MQTT_DEVICE_ID']}" if env.get("MQTT_DEVICE_ID") else "") + ")", flush=True)
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
    env = common.read_settings()
    if env.get("PHOTO_SYNC_MINS", "30") in ("0", "off"):
        print("bridge: HA photo sync off", flush=True)
        return
    mod.PhotoSync(env).start()


def main():
    global access
    access = SecurityAccess()
    print(f"bridge: security access {access.mode}"
          + (f" (devices: {', '.join(sorted(access.devices))})" if access.devices else ""), flush=True)
    for i in range(30):                  # the bus may still be starting at boot
        try:
            print("bridge: load", json.dumps(control.state()), flush=True)
            break
        except Exception as e:
            print(f"bridge: waiting for message bus ({e})", flush=True)
            time.sleep(5)
    try:
        start_mqtt()
        threading.Thread(target=ring.run, daemon=True).start()
        threading.Thread(target=health.run, daemon=True).start()
    except Exception as e:
        print(f"bridge: MQTT not started: {e}", flush=True)
    try:
        start_photo_sync()
    except Exception as e:
        print(f"bridge: photo sync not started: {e}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
