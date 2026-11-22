"""An always-on (smart bulb) load that keeps its peripheral but has no `on` variable.

Seen on Brilliant OS v26.11.18.1: gangbox_peripheral_0 still exists, with power metering only.
The bridge must not wait for it at start, and HA must not be offered a "Wired light" for it.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.test_kiosk_discovery_withdraw import NODE, _kiosk_mqtt
from tests.test_kiosk_overrides import mqtt_mod  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent

DIMMER = {"display_name": "Hanging", "on": "1", "intensity": "500", "max_intensity_value": "1000"}
ALWAYS_ON = {"display_name": "Kitchen", "power": "665", "temperature": "47"}


def _variable(value: str) -> Any:
    return types.SimpleNamespace(value=value, timestamp=1, externally_settable=True)


def _fake_bus(gangs: dict[str, dict[str, str]]) -> type:
    class FakeBus:
        device_id = "panel"

        def __init__(self, name: str = "") -> None:
            self.name = name

        def device(self) -> Any:
            return types.SimpleNamespace(peripherals=dict.fromkeys(gangs))

        def peripheral(self, name: str) -> Any:
            if name not in gangs:
                raise RuntimeError(f"get_peripheral: Unknown peripheral '{name}' (code 4)")
            return types.SimpleNamespace(
                variables={k: _variable(v) for k, v in gangs[name].items()}
            )

        def close(self) -> None:
            pass

    return FakeBus


@pytest.fixture
def load_bridge() -> Iterator[Any]:
    """Loads ha-kiosk-bridge.py against a fake bus with the given gangbox peripherals."""
    saved = sys.modules.get("brilliant_bus")
    path = sys.path[:]

    def load(gangs: dict[str, dict[str, str]]) -> types.ModuleType:
        stub = types.ModuleType("brilliant_bus")
        stub.Bus = _fake_bus(gangs)  # type: ignore[attr-defined]
        sys.modules["brilliant_bus"] = stub
        loader = importlib.machinery.SourceFileLoader(
            "ha_kiosk_bridge_under_test", str(ROOT / "kiosk" / "ha-kiosk-bridge.py")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    try:
        yield load
    finally:
        sys.path[:] = path
        if saved is None:
            sys.modules.pop("brilliant_bus", None)
        else:
            sys.modules["brilliant_bus"] = saved


@pytest.mark.parametrize(
    ("gangs", "expected"),
    [
        ({"gangbox_peripheral_0": DIMMER}, True),
        ({"gangbox_peripheral_0": ALWAYS_ON, "gangbox_peripheral_1": DIMMER}, False),
        ({"gangbox_always_on_peripheral_0": ALWAYS_ON}, False),
    ],
    ids=["dimmer", "always-on-same-peripheral", "always-on-peripheral-absent"],
)
def test_bridge_has_load(
    load_bridge: Any, gangs: dict[str, dict[str, str]], expected: bool
) -> None:
    bridge = load_bridge(gangs)
    assert bridge.control.has_load() is expected
    if expected:
        assert bridge.control.state()["name"] == "Hanging"
    else:
        with pytest.raises((KeyError, RuntimeError)):  # what made the old start-up loop retry
            bridge.control.state()


class Control:
    """The part of the bridge's LoadControl that the MQTT link uses."""

    def __init__(self, gangs: dict[str, dict[str, str]]) -> None:
        self.gangs = gangs

    def peripherals(self) -> list[str]:
        return [*self.gangs, "homekit_peripheral", "ble_peripheral"]

    def variables(self, peripheral: str = "gangbox_peripheral_0") -> dict[str, str]:
        return dict(self.gangs[peripheral])


def test_mqtt_offers_no_wired_light_for_an_always_on_load(
    mqtt_mod: types.ModuleType,  # noqa: F811
    tmp_path: Path,
) -> None:
    k = _kiosk_mqtt(mqtt_mod, tmp_path, {})
    k.control = Control({"gangbox_peripheral_0": ALWAYS_ON, "gangbox_peripheral_1": DIMMER})
    k.publish_discovery()
    assert not k.has_load()
    assert f"homeassistant/light/{NODE}/load/config" not in k.c.configs()
    assert (f"homeassistant/light/{NODE}/load/config", "", True) in k.c.published


def test_mqtt_still_offers_the_wired_light_for_a_dimmer(
    mqtt_mod: types.ModuleType,  # noqa: F811
    tmp_path: Path,
) -> None:
    k = _kiosk_mqtt(mqtt_mod, tmp_path, {})
    k.control = Control({"gangbox_peripheral_0": DIMMER})
    k.publish_discovery()
    assert k.has_load()
    assert f"homeassistant/light/{NODE}/load/config" in k.c.configs()
