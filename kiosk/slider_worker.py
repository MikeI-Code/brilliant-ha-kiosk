"""Read a Brilliant panel's physical capacitive strips (any gang count). Default to dry run.

Settings come from kiosk.env and overrides.env, read like every other kiosk service.
Strip 1 is the leftmost.
  SLIDER_TARGET_<n> what strip n controls: a Home Assistant light (light.*, tap = on/off,
                    slide = brightness) or a wired load of this panel, named exactly as Brilliant
                    shows it (tap = toggle, slide = dim when it is a dimmer)
  SLIDER_GANG_<n>   which gang that wired load is on (default n)
  SLIDER_FEED_<n>   for a light: the wired load that powers its bulbs, switched on when needed.
                    Unset: the wired load is never touched.
  SLIDER_SLOTS      input slot of each strip, in strip order (default "1,2,3")
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
        self.last_value = 0.0
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
                        # Measure from where the slide began, not the first touch, so the
                        # level does not jump by the dead zone when the slide starts.
                        travel = touch.y - touch.first
                        travel = max(0, abs(travel) - SLIDE_SPAN) * (1 if travel > 0 else -1)
                        delta = GAIN * travel
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


SETUP_ERROR = 3  # exit status for a configuration problem: systemd does not restart (see the unit)


class SetupError(ValueError):
    """The panel or kiosk.env does not match the slider configuration. Restarting will not help."""


def read_settings():
    """kiosk.env with overrides.env on top, read by the shared kiosk reader."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ha_kiosk_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ha-kiosk-common.py"))
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    return common.read_settings()


def ha_base(settings):
    import re
    match = re.match(r"(https?://[^/\s]+)", settings.get("HA_URL", ""))
    if not match:
        raise SetupError("No HA_URL in kiosk.env")
    return match.group(1)


def ha_token():
    return Path("/etc/ha-kiosk/token").read_text().strip()


def configured_strips(settings):
    """Strip numbers with a SLIDER_TARGET_<n>, in order. Strips without one are ignored."""
    numbers = sorted(int(k.rsplit("_", 1)[1]) for k in settings
                     if k.startswith("SLIDER_TARGET_") and k.rsplit("_", 1)[1].isdigit() and settings[k].strip())
    if not numbers:
        raise SetupError("No SLIDER_TARGET_<n> is set")
    return numbers


def _checked(wired, strip):
    try:
        wired.level()
    except KeyError:
        raise SetupError("Strip {}: {} has no wired load to drive (is it always-on? "
                         "Use a light.* target instead)".format(strip, wired.label))
    except ValueError as err:
        raise SetupError("Strip {} ({}): {}".format(strip, wired.label, err))
    return wired


def build_strips(settings, bus, say, make_light):
    """{strip number: strip object}. Every assignment is validated before any gesture is accepted.

    SLIDER_TARGET_<n> is a Home Assistant light (light.*) or a wired load named as Brilliant shows it.
    SLIDER_GANG_<n> is the gang of that wired load (default n). For a light, SLIDER_FEED_<n> names the
    wired load that powers the bulbs: it is switched on when needed. Without it the wired load is
    never touched.
    """
    from strips import HaLightStrip, WiredStrip

    built = {}
    for strip in configured_strips(settings):
        get = lambda key: settings.get("SLIDER_{}_{}".format(key, strip), "").strip()
        target, feed = get("TARGET"), get("FEED")
        try:
            gang = int(get("GANG") or strip)
        except ValueError:
            raise SetupError("SLIDER_GANG_{} must be a number".format(strip))
        if not target.startswith("light."):
            if feed:
                raise SetupError("SLIDER_FEED_{} only applies to a light target".format(strip))
            built[strip] = _checked(WiredStrip(bus, gang - 1, target), strip)
            continue
        circuit = None
        if feed:
            circuit = _checked(WiredStrip(bus, gang - 1, feed), strip)
        built[strip] = HaLightStrip(circuit, make_light(target), say)
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
        raise SetupError("Expected one brilliant_captouch input device")
    path = Path("/dev/input") / matches[0].parents[1].name
    if apply:
        # Transient at boot or during an install: exit 1 and let systemd retry.
        if active_state("switch_ui_app.service") != b"ActiveState=inactive":
            raise RuntimeError("Stock input handler must be inactive")
        if active_state("ha-kiosk.service") != b"ActiveState=active":
            raise RuntimeError("Custom kiosk is not active yet")
    settings = read_settings()
    strips = configured_strips(settings)
    count = max(strips)
    try:
        order = [int(x) for x in settings.get("SLIDER_SLOTS", "1,2,3").split(",")]
    except ValueError:
        order = []
    if len(order) < count or len(set(order)) != len(order):
        raise SetupError("SLIDER_SLOTS must name one distinct input slot per strip")
    slot_map = {order[strip - 1]: strip for strip in strips}
    live_output = bus = None
    if apply:
        sys.path.insert(0, "/etc/ha-kiosk")
        from brilliant_bus import Bus
        from strips import HaLight, Strips

        say = lambda text: print(text, flush=True)
        bus = open_bus(Bus)
        base = ha_base(settings) if any(
            settings.get("SLIDER_TARGET_{}".format(n), "").strip().startswith("light.")
            for n in strips) else None
        loads = Strips(build_strips(settings, bus, say, lambda entity: HaLight(base, ha_token(), entity)))
        live_output = LiveOutput(loads, say, fatal=lambda: os._exit(1))
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
    try:
        device_run(parser.parse_args().apply)
    except SetupError as err:
        print("Slider setup: " + str(err), flush=True)
        sys.exit(SETUP_ERROR)


if __name__ == "__main__":
    main()
