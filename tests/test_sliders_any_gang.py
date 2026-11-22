import io, json, os, sys, threading, time, unittest, urllib.error
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kiosk"))
from slider_output import GestureOutput, LiveOutput
from slider_worker import MockLoads, Worker, build_strips, parse_env, strip_count
from strips import FULL, HaLight, HaLightStrip, Strips, WiredStrip


def frame(worker, now, *pairs):
    for code, value in pairs:
        worker.feed(now, 3, code, value)
    worker.feed(now, 0, 0, 0)


class FakeBus:
    device_id = "test"

    def __init__(self, gangs, stale_once=False):
        self.gangs, self.calls, self.reads, self.stale_once = gangs, [], 0, stale_once

    def peripheral(self, name):
        self.reads += 1
        values = self.gangs[name]
        return SimpleNamespace(variables={k: SimpleNamespace(value=v, timestamp=self.reads, externally_settable=k != "max_intensity_value")
                                          for k, v in values.items()})

    def call(self, method, **kw):
        self.calls.append((kw["peripheral_name"], dict(kw["variables"])))
        if self.stale_once and len(self.calls) == 1:
            raise RuntimeError("Check-and-set timestamp stale for 'intensity' (code 2)")
        self.gangs[kw["peripheral_name"]].update(kw["variables"])


def office_bus(ceiling_on="1", fan_on="0"):
    return FakeBus({
        "gangbox_peripheral_0": {"display_name": "Ceiling", "dimmable": "1", "on": ceiling_on, "intensity": "400", "max_intensity_value": "1000"},
        "gangbox_peripheral_1": {"display_name": "Fan light", "on": fan_on},
    })


class FakeHA:
    """Stands in for urlopen."""
    def __init__(self, state="on", brightness=128, fail=False):
        self.state, self.brightness, self.fail, self.posts = state, brightness, fail, []

    def __call__(self, req, timeout=None):
        if self.fail:
            raise urllib.error.URLError("unreachable")
        if req.get_method() == "POST":
            body = json.loads(req.data.decode()); self.posts.append((req.full_url.rsplit("/", 1)[1], body))
            if req.full_url.endswith("turn_off"):
                self.state = "off"
            else:
                self.state = "on"
                if "brightness_pct" in body:
                    self.brightness = round(body["brightness_pct"] * 255 / 100)
            payload = []
        else:
            payload = {"state": self.state, "attributes": {"brightness": self.brightness if self.state == "on" else None}}
        return io.BytesIO(json.dumps(payload).encode())


def fan_strip(bus, ha, log=None):
    light = HaLight("http://ha:8123", "tok", "light.fan", opener=lambda req, timeout=None: _ctx(ha(req, timeout)))
    return HaLightStrip(WiredStrip(bus, 1, "Fan light"), light, (log.append if log is not None else print))


class _ctx:
    def __init__(self, f): self.f = f
    def __enter__(self): return self.f
    def __exit__(self, *a): return False


class WorkerTests(unittest.TestCase):
    def test_pr6_three_strip_behaviour_unchanged(self):
        loads = MockLoads([500] * 3); worker = Worker(loads)
        for slot in (1, 2, 3):
            frame(worker, 1, (47, slot), (57, 10), (54, 300)); frame(worker, 1.1, (54, 500)); frame(worker, 1.2, (57, -1))
            frame(worker, 2, (57, 11), (54, 400)); frame(worker, 2.1, (57, -1))
            self.assertEqual(loads.history[-2:], [(slot, "level", 800), (slot, "toggle", 0)])

    def test_two_strips_with_remapped_slots(self):
        loads = MockLoads([500, 500]); worker = Worker(loads, slot_map={0: 1, 1: 2})
        frame(worker, 1, (47, 1), (57, 5), (54, 400)); frame(worker, 1.1, (57, -1))          # tap strip 2
        frame(worker, 2, (47, 0), (57, 6), (54, 600)); frame(worker, 2.1, (54, 400)); frame(worker, 2.2, (57, -1))  # slide strip 1 down
        frame(worker, 3, (47, 2), (57, 7), (54, 400)); frame(worker, 3.1, (57, -1))          # unmapped slot: ignored
        self.assertEqual(loads.history, [(2, "toggle", 0), (1, "level", 200)])

    def test_two_fingers_at_once_do_nothing(self):
        loads = MockLoads([500, 500]); worker = Worker(loads, slot_map={1: 1, 2: 2})
        frame(worker, 1, (47, 1), (57, 5), (54, 400)); frame(worker, 1.05, (47, 2), (57, 6), (54, 400))
        frame(worker, 1.2, (47, 1), (57, -1)); frame(worker, 1.3, (47, 2), (57, -1))
        self.assertEqual(loads.history, [])

    def test_real_tap_with_lift_off_drift_counts_as_tap(self):
        loads = MockLoads([500] * 3); worker = Worker(loads)
        frame(worker, 1, (47, 1), (57, 0), (54, 515)); frame(worker, 1.2, (54, 489)); frame(worker, 1.28, (54, 462)); frame(worker, 1.29, (57, -1))
        self.assertEqual(loads.history, [(1, "toggle", 0)])

    def test_parse_env(self):
        self.assertEqual(parse_env('# c\nSLIDER_LOAD_1="Ceiling"\nSLIDER_HA_2=light.x\n'), {"SLIDER_LOAD_1": "Ceiling", "SLIDER_HA_2": "light.x"})


class ConfigTests(unittest.TestCase):
    FAMILY = {"SLIDER_HA_1": "light.lamp", "SLIDER_HA_2": "light.main", "SLIDER_LOAD_3": "Hallway", "SLIDER_GANG_3": "1"}

    def family_bus(self):
        return FakeBus({"gangbox_peripheral_0": {"display_name": "Hallway", "dimmable": "1", "on": "1", "intensity": "600", "max_intensity_value": "1000"},
                        "gangbox_peripheral_1": {"display_name": "Main"}, "gangbox_peripheral_2": {"display_name": "Floor"}})

    def test_family_room_two_ha_strips_and_hallway_dimmer_on_strip_three(self):
        bus, made = self.family_bus(), []
        built = build_strips(self.FAMILY, bus, print, lambda entity: made.append(entity) or ("light", entity))
        self.assertEqual(made, ["light.lamp", "light.main"]); self.assertEqual(strip_count(self.FAMILY), 3)
        self.assertIsNone(built[1].circuit); self.assertIsNone(built[2].circuit)
        out = GestureOutput(Strips({3: built[3]})); out.apply((3, 1, -200))
        self.assertEqual(bus.calls, [("gangbox_peripheral_0", {"intensity": "400"})])

    def test_always_on_gang_is_accepted_for_ha_strip_but_not_as_wired_strip(self):
        build_strips({"SLIDER_LOAD_1": "Hallway", "SLIDER_LOAD_2": "Main", "SLIDER_HA_2": "light.main"}, self.family_bus(), print, lambda e: e)
        with self.assertRaises(KeyError):
            build_strips({"SLIDER_LOAD_1": "Hallway", "SLIDER_LOAD_2": "Main"}, self.family_bus(), print, lambda e: e)

    def test_gaps_and_wrong_names_are_refused(self):
        with self.assertRaises(ValueError):
            strip_count({"SLIDER_HA_1": "light.a", "SLIDER_HA_3": "light.c"})
        with self.assertRaises(ValueError):
            build_strips({"SLIDER_LOAD_1": "Kitchen"}, self.family_bus(), print, lambda e: e)


class WiredTests(unittest.TestCase):
    def test_dimmer_level_toggle_and_stale_retry(self):
        bus = office_bus(); s = WiredStrip(bus, 0, "Ceiling")
        self.assertEqual(s.level(), (400, 1000))
        s.toggle(); self.assertEqual(bus.calls[-1], ("gangbox_peripheral_0", {"on": "0"}))
        self.assertEqual(s.level(), (0, 1000))
        s.set_level(700); self.assertEqual(bus.calls[-1], ("gangbox_peripheral_0", {"on": "1", "intensity": "700"}))
        bus = office_bus(); bus.stale_once = True
        WiredStrip(bus, 0, "Ceiling").set_level(450); self.assertEqual(len(bus.calls), 2)
        bus = office_bus(); bus.stale_once = True
        with self.assertRaises(RuntimeError):
            WiredStrip(bus, 0, "Ceiling").toggle()
        self.assertEqual(len(bus.calls), 1)                       # a toggle is never resent

    def test_renamed_circuit_is_refused(self):
        with self.assertRaises(ValueError):
            WiredStrip(office_bus(), 0, "Kitchen").toggle()

    def test_on_off_only_load(self):
        bus = office_bus(fan_on="0"); s = WiredStrip(bus, 1, "Fan light")
        self.assertEqual(s.level(), (0, 1000)); s.toggle(); self.assertEqual(s.level(), (1000, 1000))
        s.set_level(300); self.assertEqual(bus.gangs["gangbox_peripheral_1"], {"display_name": "Fan light", "on": "1"})


class HaOnlyStripTests(unittest.TestCase):
    def strip(self, ha):
        light = HaLight("http://ha:8123", "tok", "light.fan", opener=lambda req, timeout=None: _ctx(ha(req, timeout)))
        return HaLightStrip(None, light)

    def test_tap_and_slide_never_touch_the_wired_load(self):
        bus, ha = office_bus(fan_on="0"), FakeHA(state="off")
        out = GestureOutput(Strips({1: WiredStrip(bus, 0, "Ceiling"), 2: self.strip(ha)}))
        out.apply((2, 1, None)); out.apply((2, 2, None)); out.apply((2, 3, 600))
        self.assertEqual([p[0] for p in ha.posts], ["turn_on", "turn_off", "turn_on"])
        self.assertEqual(ha.posts[-1][1]["brightness_pct"], 60); self.assertEqual(bus.calls, [])

    def test_ha_down_or_bulbs_offline_is_an_error_not_a_circuit_cut(self):
        with self.assertRaises(OSError):
            self.strip(FakeHA(fail=True)).toggle()
        with self.assertRaises(ValueError):
            self.strip(FakeHA(state="unavailable")).toggle()


class HaStripTests(unittest.TestCase):
    def test_tap_with_circuit_off_powers_circuit_only(self):
        bus, ha = office_bus(fan_on="0"), FakeHA()
        fan_strip(bus, ha).toggle()
        self.assertEqual(bus.calls, [("gangbox_peripheral_1", {"on": "1"})]); self.assertEqual(ha.posts, [])

    def test_tap_toggles_bulbs_and_keeps_circuit_on(self):
        bus, ha = office_bus(fan_on="1"), FakeHA(state="on")
        s = fan_strip(bus, ha); s.toggle(); s.toggle()
        self.assertEqual([p[0] for p in ha.posts], ["turn_off", "turn_on"]); self.assertEqual(bus.calls, [])

    def test_tap_falls_back_to_circuit_when_ha_down_or_bulbs_offline(self):
        for ha in (FakeHA(fail=True), FakeHA(state="unavailable")):
            bus, log = office_bus(fan_on="1"), []
            fan_strip(bus, ha, log).toggle()
            self.assertEqual(bus.calls, [("gangbox_peripheral_1", {"on": "0"})]); self.assertEqual(len(log), 1)

    def test_slide_dims_bulbs_relative_to_current_brightness(self):
        bus, ha = office_bus(fan_on="1"), FakeHA(state="on", brightness=128)      # ~50%
        out = GestureOutput(Strips({1: WiredStrip(bus, 0, "Ceiling"), 2: fan_strip(bus, ha)}))
        out.apply((2, 9, 300)); out.apply((2, 9, -2000))
        self.assertEqual([p[1].get("brightness_pct") for p in ha.posts], [80, 1]); self.assertEqual(bus.calls, [])
        out.apply((1, 10, 100)); self.assertEqual(bus.calls[-1], ("gangbox_peripheral_0", {"intensity": "500"}))

    def test_slide_from_dark_circuit_powers_it_first(self):
        bus, ha = office_bus(fan_on="0"), FakeHA(state="off")
        GestureOutput(Strips({2: fan_strip(bus, ha)})).apply((2, 1, 400))
        self.assertEqual(bus.calls, [("gangbox_peripheral_1", {"on": "1"})]); self.assertEqual(ha.posts[0][1]["brightness_pct"], 40)

    def test_ha_down_during_slide_is_reported_not_fatal(self):
        bus, log = office_bus(fan_on="1"), []
        sink = LiveOutput(Strips({2: fan_strip(bus, FakeHA(fail=True))}), log.append)
        try:
            sink.submit((2, 1, 100)); deadline = time.monotonic() + 1
            while not log and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(log and "unavailable" in log[0]); self.assertTrue(sink.thread.is_alive())
        finally:
            sink.close()


if __name__ == "__main__":
    unittest.main()
