"""Read a Brilliant panel's physical capacitive strips (any gang count). Default to dry run.

Adapted from brilliant-ha-kiosk PR #6. Settings come from sliders.env next to this file:
  SLIDER_SLOTS     input slot of each strip, in strip order (default "1,2,3", cut to the strip count)
Each strip n (1 = leftmost) needs at least one of:
  SLIDER_LOAD_<n>  a wired load of this panel, named exactly as Brilliant shows it
  SLIDER_HA_<n>    a Home Assistant light the strip controls instead (tap = on/off, slide = dim)
and may add:
  SLIDER_GANG_<n>  which gang SLIDER_LOAD_<n> is on (default n), to put a load on another strip
  SLIDER_POWER_<n> with both of the above: "manage" = the bulbs are fed by that load, switch it on
                   when needed. Default "leave": never touch the wired load.
"""

import argparse
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from slider_output import GestureOutput, LiveOutput

DEFAULT_SLOTS = {1: 1, 2: 2, 3: 3}   # input slot -> strip number

INPUT_EVENT = struct.Struct("<IIHHi")  # ARM32 input_event
# Recorded on a real panel 2026-11-21: taps drift 17-53 units (lift-off), slides span 650+.
TAP_SPAN, SLIDE_SPAN = 60, 65
UPDATE_INTERVAL = 0.065
GAIN = 1.5  # Provisional relative mapping, intensity units per unit of finger travel.


class MockLoads:
    def __init__(self, levels):
        self.levels = dict(enumerate(levels, 1))
        self.history = []

    def level(self, slot):
        return self.levels[slot], 1000

    def toggle(self, slot):
        self.levels[slot] = 0 if self.levels[slot] else 1000
        self.history.append((slot, "toggle", self.levels[slot]))

    def set_level(self, slot, value):
        self.levels[slot] = value
        self.history.append((slot, "level", value))


class Contact:
    def __init__(self, now, tracking_id):
        self.start = now
        self.id = tracking_id
        self.first = self.y = self.low = self.high = None
        self.last_value = None
        self.last_write = float("-inf")
        self.sliding = self.blocked = False

    def move(self, y):
        if not 0 <= y <= 1000:
            self.blocked = True
            return
        if self.first is None:
            self.first = self.low = self.high = y
        self.y = y
        self.low = min(self.low, y)
        self.high = max(self.high, y)


class Worker:
    def __init__(
        self, loads, slot=None, observer=None, live_output=None, snapshot=None, slot_map=None
    ):
        self.slot_map = dict(slot_map or DEFAULT_SLOTS)
        self.live_output = live_output
        self.submit = (
            live_output.submit
            if live_output is not None
            else GestureOutput(loads).apply
        )
        self.snapshot = snapshot
        self.discarding = False
        self.inherited = {}
        self.slot = slot
        self.active = {}
        self.released = set()
        self.observer = observer

    def frame(self, now):
        for slot, touch in list(self.active.items()):
            end = slot in self.released
            usable = (
                slot in self.slot_map and touch.first is not None and not touch.blocked
            )
            kind = "ignored"
            if usable:
                span = touch.high - touch.low
                if span >= SLIDE_SPAN:
                    touch.sliding = True
                    if end or now - touch.last_write >= UPDATE_INTERVAL:
                        delta = GAIN * (touch.y - touch.first)
                        if delta != touch.last_value:
                            self.submit((self.slot_map[slot], touch.id, delta))
                            touch.last_value, touch.last_write = delta, now
                    kind = "slide"
                elif end and span <= TAP_SPAN and 0 < now - touch.start <= 0.95:
                    self.submit((self.slot_map[slot], touch.id, None))
                    kind = "tap"
            if end:
                if self.observer:
                    self.observer(touch.id, self.slot_map.get(slot, slot), kind)
                del self.active[slot]
        self.released.clear()

    def feed(self, now, typ, code, value):
        # SYN_DROPPED invalidates every in-flight gesture. Resume only after
        # SYN_REPORT and ignore contacts already down in the kernel snapshot.
        if (typ, code) == (0, 3):
            self.active.clear()
            self.released.clear()
            self.inherited.clear()
            self.slot = None
            self.discarding = True
            if self.live_output is not None:
                self.live_output.cancel()
            return
        if self.discarding:
            if (typ, code) == (0, 0):
                if self.snapshot is not None:
                    self.slot, self.inherited = self.snapshot()
                self.discarding = False
            return
        if typ == 0:
            if code == 0:
                self.frame(now)
            return
        if typ != 3:
            return
        if code == 47:
            self.slot = value
        elif self.slot not in self.slot_map:
            return
        elif code == 57:
            if self.inherited.get(self.slot) == value:
                return
            self.inherited.pop(self.slot, None)
            if value < 0:
                if self.slot in self.active:
                    self.released.add(self.slot)
            else:
                if self.slot in self.active:
                    # A malformed slot stream cannot complete an ambiguous gesture.
                    self.active.pop(self.slot)
                    self.released.discard(self.slot)
                    self.inherited[self.slot] = value
                    return
                touch = Contact(now, value)
                if self.active:
                    touch.blocked = True
                    for previous in self.active.values():
                        previous.blocked = True
                self.active[self.slot] = touch
        elif code == 54 and self.slot in self.active:
            self.active[self.slot].move(value)

    def finish(self):
        if self.active:
            raise ValueError(
                "Incomplete trace; in-progress gesture has no final command"
            )


def feed_bytes(worker, data):
    for sec, micros, typ, code, val in INPUT_EVENT.iter_unpack(data):
        worker.feed(sec + micros / 1000000.0, typ, code, val)


def active_state(service):
    result = subprocess.run(
        ["systemctl", "show", "-p", "ActiveState", service],
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def snapshot_slots(stream, slots=DEFAULT_SLOTS):
    import fcntl

    # EVIOCGABS(ABS_MT_SLOT) gives the selected slot and upper bound.
    absolute = bytearray(24)
    fcntl.ioctl(stream.fileno(), 0x8018456F, absolute)
    slot, _, maximum_slot = struct.unpack_from("<3i", absolute)
    if not max(slots) <= maximum_slot <= 31:
        raise ValueError("Unexpected multi-touch slot count")
    # EVIOCGMTSLOTS(ABS_MT_TRACKING_ID) returns the IDs still in contact.
    tracking = bytearray(4 * (maximum_slot + 2))
    struct.pack_into("<I", tracking, 0, 57)
    fcntl.ioctl(stream.fileno(), 0x8000450A | (len(tracking) << 16), tracking)
    inherited = {
        n: t[0]
        for n, t in enumerate(struct.iter_unpack("<i", tracking[4:]))
        if n in slots and t[0] >= 0
    }
    return slot, inherited


HERE = os.path.dirname(os.path.abspath(__file__))


def parse_env(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def read_settings(path=None):
    return parse_env(Path(path or os.path.join(HERE, "sliders.env")).read_text())


def ha_base():
    import re
    url = parse_env(Path("/etc/ha-kiosk/kiosk.env").read_text()).get("HA_URL", "")
    match = re.match(r"(https?://[^/\s]+)", url)
    if not match:
        raise ValueError("No HA_URL in kiosk.env")
    return match.group(1)


def ha_token():
    return Path("/etc/ha-kiosk/token").read_text().strip()


def strip_count(settings):
    numbers = [int(k.rsplit("_", 1)[1]) for k in settings
               if k.startswith(("SLIDER_LOAD_", "SLIDER_HA_")) and k.rsplit("_", 1)[1].isdigit() and settings[k]]
    count = max(numbers or [0])
    if not count or set(numbers) != set(range(1, count + 1)):
        raise ValueError("Give every strip from 1 up a SLIDER_LOAD_<n> or a SLIDER_HA_<n> (no gaps)")
    return count


def build_strips(settings, bus, say, make_light):
    """{strip number: strip object}. Every assignment is validated before any gesture is accepted."""
    from strips import HaLightStrip, WiredStrip

    built = {}
    for strip in range(1, strip_count(settings) + 1):
        get = lambda key: settings.get("SLIDER_{}_{}".format(key, strip), "").strip()
        label, entity = get("LOAD"), get("HA")
        gang = int(get("GANG") or strip)
        wired = WiredStrip(bus, gang - 1, label) if label else None
        if not entity:
            wired.level()                     # a wired strip must be a readable load
            built[strip] = wired
            continue
        manage = get("POWER").lower() == "manage"
        if manage and wired is None:
            raise ValueError("SLIDER_POWER_{}=manage needs SLIDER_LOAD_{}".format(strip, strip))
        if wired is not None:
            # A gang in Brilliant's always-on mode has no on/intensity, so only its name is checked.
            wired.level() if manage else wired._target()
        built[strip] = HaLightStrip(wired if manage else None, make_light(entity), say)
    return built


def open_bus(factory):
    # The socket can appear after the systemd bus unit becomes active.
    while True:
        try:
            return factory(name="ha_kiosk_sliders")
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(1)


def device_run(apply):
    matches = [
        p
        for p in Path("/sys/class/input").glob("event*/device/name")
        if p.read_text().strip() == "brilliant_captouch"
    ]
    if len(matches) != 1:
        raise ValueError("Expected one brilliant_captouch input device")
    path = Path("/dev/input") / matches[0].parents[1].name
    if apply:
        if active_state("switch_ui_app.service") != b"ActiveState=inactive":
            raise ValueError("Stock input handler must be inactive during a live pilot")
        if active_state("ha-kiosk.service") != b"ActiveState=active":
            raise ValueError("Custom kiosk must be active during a live pilot")
    settings = read_settings()
    count = strip_count(settings)
    order = [int(x) for x in settings.get("SLIDER_SLOTS", "1,2,3").split(",")][:count]
    if len(order) != count or len(set(order)) != count:
        raise ValueError("SLIDER_SLOTS must name one distinct input slot per strip")
    slot_map = {slot: strip for strip, slot in enumerate(order, 1)}
    live_output = bus = None
    if apply:
        sys.path.insert(0, "/etc/ha-kiosk")
        from brilliant_bus import Bus
        from strips import HaLight, Strips

        say = lambda text: print(text, flush=True)
        bus = open_bus(Bus)
        loads = Strips(build_strips(settings, bus, say, lambda entity: HaLight(ha_base(), ha_token(), entity)))
        live_output = LiveOutput(loads, say)
    else:
        loads = MockLoads([0] * count)
    with path.open("rb", buffering=0) as stream:
        snapshot = lambda: snapshot_slots(stream, slot_map)
        slot, inherited = snapshot()
        seen = lambda tid, strip, kind: print("strip {} {}".format(strip, kind), flush=True)
        worker = Worker(loads, slot, seen, live_output, snapshot, slot_map)
        worker.inherited = inherited
        try:
            while True:
                data = stream.read(INPUT_EVENT.size * 32)
                if not data:
                    raise IOError("Slider input disconnected")
                feed_bytes(worker, data)
                if not apply:
                    loads.history.clear()
        finally:
            if live_output is not None:
                live_output.close()
            if bus is not None:
                bus.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        action="store_true",
        required=True,
        help="Read the panel capacitive strips",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Control the configured local dimmers (default: dry run)",
    )
    device_run(parser.parse_args().apply)


if __name__ == "__main__":
    main()
