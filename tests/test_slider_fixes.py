"""Review fixes for PR #6: tap drift, swipe-down on an off light, bus loss, setup errors, slide feel."""

import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kiosk"))
import slider_worker
from slider_output import GestureOutput, LiveOutput
from slider_worker import SETUP_ERROR, MockLoads, SetupError, Worker
from strips import BusLost, Strips, WiredStrip


def frame(worker, now, *pairs):
    for code, value in pairs:
        worker.feed(now, 3, code, value)
    worker.feed(now, 0, 0, 0)


class TapDrift(unittest.TestCase):
    def test_tap_that_drifts_55_units_toggles(self):
        loads = MockLoads([500] * 3)
        worker = Worker(loads)
        frame(worker, 1, (47, 2), (57, 1), (54, 400))
        frame(worker, 1.2, (54, 455))
        frame(worker, 1.25, (57, -1))
        self.assertEqual(loads.history, [(2, "toggle", 0)])


class SwipeDownWhenOff(unittest.TestCase):
    def test_swipe_down_on_an_off_light_does_nothing(self):
        loads = MockLoads([0, 0, 0])
        out = GestureOutput(loads)
        out.apply((1, 7, -150))
        out.apply((1, 7, -400))
        self.assertEqual(loads.history, [])

    def test_swipe_up_on_an_off_light_turns_it_on(self):
        loads = MockLoads([0, 0, 0])
        GestureOutput(loads).apply((1, 7, 300))
        self.assertEqual(loads.history, [(1, "level", 300)])


class SlideFeel(unittest.TestCase):
    def test_no_jump_when_the_slide_starts(self):
        loads = MockLoads([500] * 3)
        worker = Worker(loads)
        frame(worker, 1, (47, 1), (57, 3), (54, 300))
        frame(worker, 1.1, (54, 366))  # just past the dead zone: 1 unit of travel
        self.assertEqual(loads.history, [(1, "level", 502)])

    def test_late_final_slide_position_still_lands(self):
        loads = MockLoads([500] * 3)
        sink = LiveOutput(loads)
        try:
            with mock.patch("slider_output.monotonic", side_effect=[0.0, 10.0] + [10.0] * 20):
                sink.submit((1, 4, 200))
                deadline = time.time() + 1
                while not loads.history and time.time() < deadline:
                    time.sleep(0.01)
            self.assertEqual(loads.history, [(1, "level", 700)])
        finally:
            sink.close()

    def test_late_tap_is_never_applied(self):
        loads = MockLoads([500] * 3)
        log = []
        sink = LiveOutput(loads, log.append)
        try:
            with mock.patch("slider_output.monotonic", side_effect=[0.0, 10.0] + [10.0] * 20):
                sink.submit((1, 4, None))
                deadline = time.time() + 1
                while not log and time.time() < deadline:
                    time.sleep(0.01)
            self.assertEqual(loads.history, [])
            self.assertIn("expired", log[0])
        finally:
            sink.close()


class BusLoss(unittest.TestCase):
    class DeadBus:
        device_id = "test"

        def __init__(self, error):
            self.error = error

        def peripheral(self, name):
            raise self.error

    def test_bus_errors_become_bus_lost(self):
        for error in (EOFError("closed"), OSError("timed out")):
            with self.assertRaises(BusLost):
                WiredStrip(self.DeadBus(error), 0, "Hall").level()

    def test_closed_bus_makes_the_service_exit(self):
        died = threading.Event()
        log = []
        sink = LiveOutput(Strips({1: WiredStrip(self.DeadBus(EOFError("closed")), 0, "Hall")}),
                          log.append, fatal=died.set)
        sink.submit((1, 1, None))
        self.assertTrue(died.wait(1))
        self.assertIn("bus lost", log[-1])
        sink.close()

    def test_unexpected_error_makes_the_service_exit(self):
        class Broken(MockLoads):
            def toggle(self, slot):
                raise TypeError("bug")

        died = threading.Event()
        sink = LiveOutput(Broken([500] * 3), lambda text: None, fatal=died.set)
        sink.submit((1, 1, None))
        self.assertTrue(died.wait(1))
        sink.close()


class SetupErrors(unittest.TestCase):
    def test_setup_error_exits_with_the_no_restart_status(self):
        with mock.patch.object(slider_worker, "device_run", side_effect=SetupError("bad name")), \
                mock.patch.object(sys, "argv", ["slider_worker.py", "--device"]):
            with self.assertRaises(SystemExit) as caught:
                slider_worker.main()
        self.assertEqual(caught.exception.code, SETUP_ERROR)

    def test_kiosk_not_ready_is_retryable(self):
        with mock.patch.object(slider_worker, "device_run", side_effect=RuntimeError("not active")), \
                mock.patch.object(sys, "argv", ["slider_worker.py", "--device"]):
            with self.assertRaises(RuntimeError):
                slider_worker.main()

    def test_unit_does_not_restart_on_setup_errors(self):
        unit = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "systemd", "ha-kiosk-sliders.service")
        text = open(unit, encoding="utf-8").read()
        self.assertIn("RestartPreventExitStatus={}".format(SETUP_ERROR), text)
        self.assertIn("StartLimitBurst=", text)

    def test_missing_ha_url_is_a_setup_error(self):
        with self.assertRaises(SetupError):
            slider_worker.ha_base({})


if __name__ == "__main__":
    unittest.main()
