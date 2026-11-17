"""ha-kiosk-slider: slider readings -> HA commands (no bus, no HA)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ha_kiosk_slider", ROOT / "kiosk" / "ha-kiosk-slider.py"
)
slider = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slider)


def test_targets_are_one_based_and_validated() -> None:
    env = {
        "SLIDER_TARGET_1": "light.hue_a",
        "SLIDER_TARGET_2": " light.b ",
        "SLIDER_TARGET_3": "bad",
        "OTHER": "light.x",
    }
    assert slider.targets_from(env) == {0: "light.hue_a", 1: "light.b"}


def test_always_on_peripheral_wins() -> None:
    names = [
        "gangbox_peripheral_0",
        "gangbox_always_on_peripheral_1",
        "gangbox_peripheral_1",
        "hardware_peripheral",
    ]
    assert slider.gang_peripherals(names) == {
        0: "gangbox_peripheral_0",
        1: "gangbox_always_on_peripheral_1",
    }


def test_level() -> None:
    assert slider.level({"on": "1", "intensity": "500", "max_intensity_value": "1000"}) == (
        True,
        50,
    )
    assert slider.level({"on": "0", "intensity": "500"}) == (False, 50)
    assert slider.level({"power": "3"}) is None


def test_first_reading_is_not_forwarded_then_moves_are() -> None:
    g = slider.Gang(0, "light.x")
    assert g.reading((True, 100), 0.0) is None
    assert g.reading((True, 40), 5.0) == (True, 40)


def test_snap_back_to_rest_is_brilliants_reset_not_a_command() -> None:
    g = slider.Gang(0, "light.x")
    g.reading((True, 100), 0.0)
    assert g.reading((True, 30), 10.0) == (True, 30)
    assert g.reading((True, 100), 10.5) is None  # always-on reset, ignored
    assert g.reading((True, 100), 20.0) is None


def test_rate_limit_keeps_latest_value() -> None:
    g = slider.Gang(0, "light.x")
    g.reading((True, 100), 0.0)
    assert g.reading((True, 80), 10.0) == (True, 80)
    assert g.reading((True, 60), 10.1) is None  # too soon
    assert g.reading((True, 60), 10.5) == (True, 60)  # sent once the gap passes


def test_off() -> None:
    g = slider.Gang(0, "light.x")
    g.reading((True, 100), 0.0)
    assert g.reading((False, 0), 10.0) == (False, 0)
