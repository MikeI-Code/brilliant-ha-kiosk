"""Physical sliders -> Home Assistant lights (loaded by ha-kiosk-bridge). EXPERIMENTAL (nightly).

Brilliant Controls with 2+ switches have a touch slider per switch. To drive a smart bulb (Hue
etc.) from a slider without ever cutting its power:

  1. In Brilliant's own app, set that switch's load to always-on (smart bulb). Brilliant's
     firmware then holds the circuit at full power (gangbox_always_on_peripheral).
  2. In kiosk.env, point the slider at the HA light:  SLIDER_TARGET_1=light.kitchen_hue
     (1 = first switch / gangbox 0, 2 = second, ...).

This module never writes to the Brilliant bus. It watches the gang's on/intensity variables and
forwards slider moves to HA (light.turn_on brightness_pct / light.turn_off) with the panel token.

How the slider shows up on the bus in always-on mode is inferred from Brilliant's libraries, not
yet verified on hardware. With SLIDER_DEBUG=1 (default on while any SLIDER_TARGET_* is set) the
bridge log gets a one-time dump of every gangbox/switch peripheral and every variable change on
them, which is what we need to confirm or fix the mapping:
  journalctl -u ha-kiosk-bridge | grep slider:
"""
import json
import re
import threading
import time
import urllib.request

POLL = 0.2            # seconds between bus reads
SEND_GAP = 0.3        # at most one HA call per gang per SEND_GAP (latest value wins)
RESET_WINDOW = 2.0    # a snap back to the resting value this soon after a move = Brilliant's reset
NOISY = {"power", "temperature", "timestamp"}   # vars that change on their own; never logged
WATCH = re.compile(r"gangbox|switch|cap_?touch|slider", re.I)
GANG = re.compile(r"gangbox\w*?_(\d+)$")


def targets_from(env):
    """{gang index (0-based): entity_id} from SLIDER_TARGET_<n> (1-based)."""
    out = {}
    for k, v in env.items():
        m = re.fullmatch(r"SLIDER_TARGET_([1-9])", k)
        v = (v or "").strip()
        if m and re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", v):
            out[int(m.group(1)) - 1] = v
    return out


def gang_peripherals(names):
    """{gang index: peripheral name}; the always-on peripheral wins over the dimmer one."""
    out = {}
    for name in sorted(names):
        m = GANG.search(name)
        if not m:
            continue
        n = int(m.group(1))
        if n not in out or "always_on" in name:
            out[n] = name
    return out


def level(v):
    """(on, percent) from a gang's variables; None if it has neither."""
    if "on" not in v and "intensity" not in v:
        return None
    on = v.get("on", "1") == "1"
    try:
        top = int(v.get("max_intensity_value") or 1000) or 1000
        pct = max(0, min(100, round(100 * int(v.get("intensity") or 0) / top)))
    except ValueError:
        pct = 100
    return (on and pct > 0, pct)


class Gang:
    """One slider: turns a stream of (on, pct) readings into HA commands."""

    def __init__(self, n, entity):
        self.n, self.entity = n, entity
        self.rest = None          # resting value (unchanged for RESET_WINDOW)
        self.last = None          # last reading
        self.moved_at = 0.0       # time of the last reading that differed from rest
        self.since = 0.0          # when `last` was first seen
        self.pending = None
        self.sent_at = 0.0

    def reading(self, val, now):
        """Feed a reading; returns a command to send now or None."""
        if val != self.last:
            self.last, self.since = val, now
            if self.rest is None:
                self.rest = val                     # first sight: nothing to forward
            elif val == self.rest and now - self.moved_at < RESET_WINDOW:
                self.pending = None                 # Brilliant snapping back (always-on reset)
            else:
                if val != self.rest:
                    self.moved_at = now
                self.pending = val
        elif self.rest != val and now - self.since >= RESET_WINDOW:
            self.rest = val                         # settled: new resting value
        if self.pending is not None and now - self.sent_at >= SEND_GAP:
            cmd, self.pending, self.sent_at = self.pending, None, now
            return cmd
        return None


class SliderLink:
    def __init__(self, env, control, log=print):
        self.env, self.control, self.log = env, control, log
        self.targets = targets_from(env)
        dbg = str(env.get("SLIDER_DEBUG", "")).strip().lower()
        self.debug = dbg in ("1", "on", "true", "yes") or (dbg == "" and bool(self.targets))
        m = re.match(r"(https?://[^/\s]+)", env.get("HA_URL", ""))
        self.base = m.group(1) if m else ""
        try:
            self.token = open("/etc/ha-kiosk/token", encoding="utf-8").read().strip()
        except OSError:
            self.token = ""
        self.gangs = {n: Gang(n, e) for n, e in self.targets.items()}
        self.seen = {}

    def start(self):
        if not self.targets and not self.debug:
            return False
        threading.Thread(target=self._run, daemon=True).start()
        return True

    # ---------- HA ----------
    def _ha(self, entity, val):
        on, pct = val
        domain = "light" if entity.startswith("light.") else "homeassistant"
        svc = "turn_on" if on else "turn_off"
        data = {"entity_id": entity}
        if domain == "light" and on:
            data["brightness_pct"] = pct
        req = urllib.request.Request(f"{self.base}/api/services/{domain}/{svc}", method="POST",
                                     data=json.dumps(data).encode(),
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).read()
            self.log(f"slider: gang {self_n(entity, self.targets)} -> {entity} "
                     f"{'on ' + str(pct) + '%' if on else 'off'}")
        except Exception as e:
            self.log(f"slider: {entity}: HA call failed: {e}")

    # ---------- bus ----------
    def _dump(self, names):
        self.log("slider: peripherals: " + ", ".join(sorted(names)))
        for name in sorted(names):
            if WATCH.search(name):
                try:
                    v = self.control.variables(name)
                except Exception as e:
                    self.log(f"slider: {name}: {e}")
                    continue
                self.log(f"slider: {name} = " + json.dumps({k: str(x)[:60] for k, x in sorted(v.items())}))

    def _run(self):
        time.sleep(5)
        names, gangs = [], {}
        while not names:
            try:
                names = self.control.peripherals()
            except Exception as e:
                self.log(f"slider: waiting for bus ({e})")
                time.sleep(10)
        gangs = gang_peripherals(names)
        self.log("slider: gangs " + json.dumps({n + 1: p for n, p in gangs.items()})
                 + " targets " + json.dumps({n + 1: e for n, e in self.targets.items()}))
        for n in self.targets:
            if n not in gangs:
                self.log(f"slider: SLIDER_TARGET_{n + 1}: this panel has no gang {n + 1}")
            elif "always_on" not in gangs[n]:
                self.log(f"slider: gang {n + 1} is not always-on ({gangs[n]}): the slider also dims "
                         "the wired circuit. Set it to always-on / smart bulb in Brilliant's app.")
        if self.debug:
            self._dump(names)
        watch = [p for p in names if WATCH.search(p)] if self.debug else []
        last_dump = time.time()
        while True:
            now = time.time()
            for n, gang in self.gangs.items():
                p = gangs.get(n)
                if not p:
                    continue
                try:
                    v = self.control.variables(p)
                except Exception as e:
                    self.log(f"slider: {p}: {e}")
                    continue
                val = level(v)
                if val is None:
                    continue
                cmd = gang.reading(val, now)
                if cmd is not None:
                    threading.Thread(target=self._ha, args=(gang.entity, cmd), daemon=True).start()
            for p in watch:
                self._diff(p)
            if self.debug and now - last_dump > 6 * 3600:   # names can change after a Brilliant update
                last_dump = now
                try:
                    self._dump(self.control.peripherals())
                except Exception:
                    pass
            time.sleep(POLL)

    def _diff(self, p):
        try:
            v = {k: str(x) for k, x in self.control.variables(p).items() if k not in NOISY}
        except Exception:
            return
        old = self.seen.get(p)
        self.seen[p] = v
        if old is None:
            return
        ch = {k: f"{old.get(k)!s:.40} -> {v.get(k)!s:.40}" for k in set(old) | set(v) if old.get(k) != v.get(k)}
        if ch:
            self.log(f"slider: {p} changed " + json.dumps(ch, sort_keys=True))


def self_n(entity, targets):
    return next((n + 1 for n, e in targets.items() if e == entity), "?")
