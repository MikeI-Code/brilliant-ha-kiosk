"""Read a three-gang Brilliant panel’s physical dimmer strips. Default to dry run."""

import argparse
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from slider_output import GestureOutput, LiveOutput

from wired_loads import WiredLoads

STRIP_SLOTS = (1, 2, 3)

INPUT_EVENT = struct.Struct("<IIHHi")  # ARM32 input_event
TAP_SPAN, SLIDE_SPAN = 40, 65
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
        self, loads, slot=None, observer=None, live_output=None, snapshot=None
    ):
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
                slot in STRIP_SLOTS and touch.first is not None and not touch.blocked
            )
            kind = "ignored"
            if usable:
                span = touch.high - touch.low
                if span >= SLIDE_SPAN:
                    touch.sliding = True
                    if end or now - touch.last_write >= UPDATE_INTERVAL:
                        delta = GAIN * (touch.y - touch.first)
                        if delta != touch.last_value:
                            self.submit((slot, touch.id, delta))
                            touch.last_value, touch.last_write = delta, now
                    kind = "slide"
                elif end and span <= TAP_SPAN and 0 < now - touch.start <= 0.95:
                    self.submit((slot, touch.id, None))
                    kind = "tap"
            if end:
                if self.observer:
                    self.observer(touch.id, slot, kind)
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
        elif self.slot not in STRIP_SLOTS:
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


def snapshot_slots(stream):
    import fcntl

    # EVIOCGABS(ABS_MT_SLOT) gives the selected slot and upper bound.
    absolute = bytearray(24)
    fcntl.ioctl(stream.fileno(), 0x8018456F, absolute)
    slot, _, maximum_slot = struct.unpack_from("<3i", absolute)
    if not 3 <= maximum_slot <= 31:
        raise ValueError("Unexpected multi-touch slot count")
    # EVIOCGMTSLOTS(ABS_MT_TRACKING_ID) returns the IDs still in contact.
    tracking = bytearray(4 * (maximum_slot + 2))
    struct.pack_into("<I", tracking, 0, 57)
    fcntl.ioctl(stream.fileno(), 0x8000450A | (len(tracking) << 16), tracking)
    inherited = {
        n: t[0]
        for n, t in enumerate(struct.iter_unpack("<i", tracking[4:]))
        if n in STRIP_SLOTS and t[0] >= 0
    }
    return slot, inherited


def open_bus(factory):
    # The socket can appear after the systemd bus unit becomes active.
    while True:
        try:
            return factory(name="ha_kiosk_three_sliders")
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
    live_output = bus = None
    if apply:
        sys.path.insert(0, "/etc/ha-kiosk")
        from brilliant_bus import Bus

        bus = open_bus(Bus)
        labels = [
            os.environ.get("SLIDER_LOAD_{}".format(i), "").strip() for i in STRIP_SLOTS
        ]
        loads = WiredLoads(bus, labels)
        for strip in STRIP_SLOTS:
            loads.level(
                strip
            )  # Validate all three assignments before accepting any gestures.
        live_output = LiveOutput(loads, lambda text: print(text, flush=True))
    else:
        loads = MockLoads([0, 0, 0])
    with path.open("rb", buffering=0) as stream:
        snapshot = lambda: snapshot_slots(stream)
        slot, inherited = snapshot()
        seen = lambda tid, strip, kind: print(tid, strip, kind, flush=True)
        worker = Worker(loads, slot, seen, live_output, snapshot)
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
