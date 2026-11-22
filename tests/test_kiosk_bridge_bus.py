"""ha-kiosk-bridge: an error reply from the bus must not cost the bus connection."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


class FakeBus:
    """Stands in for brilliant_bus.Bus: counts connections and closes."""

    opened = 0
    closed = 0

    def __init__(self, name: str = "") -> None:
        type(self).opened += 1
        self.name = name

    def close(self) -> None:
        type(self).closed += 1


@pytest.fixture
def bridge() -> Iterator[types.ModuleType]:
    """The bridge module, loaded with a stub in place of the panel-only brilliant_bus."""
    FakeBus.opened = FakeBus.closed = 0
    stub = types.ModuleType("brilliant_bus")
    stub.Bus = FakeBus  # type: ignore[attr-defined]
    saved = sys.modules.get("brilliant_bus")
    sys.modules["brilliant_bus"] = stub
    path = sys.path[:]
    try:
        loader = importlib.machinery.SourceFileLoader(
            "ha_kiosk_bridge_under_test", str(ROOT / "kiosk" / "ha-kiosk-bridge.py")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = path
        if saved is None:
            del sys.modules["brilliant_bus"]
        else:
            sys.modules["brilliant_bus"] = saved


def test_error_reply_keeps_the_connection(bridge: types.ModuleType) -> None:
    control = bridge.LoadControl()

    def unknown_device(bus: Any) -> None:
        raise RuntimeError("get_device: Unknown device 'schlage' (code 4)")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            control._with_bus(unknown_device)
    assert control._with_bus(lambda bus: "ok") == "ok"
    assert (FakeBus.opened, FakeBus.closed) == (1, 0)


def test_refused_write_keeps_the_connection(bridge: types.ModuleType) -> None:
    control = bridge.LoadControl()

    def not_settable(bus: Any) -> None:
        raise ValueError("not settable: on")

    with pytest.raises(ValueError, match="not settable"):
        control._with_bus(not_settable)
    assert (FakeBus.opened, FakeBus.closed) == (1, 0)


@pytest.mark.parametrize(
    "error", [EOFError("message bus closed the connection"), BrokenPipeError()]
)
def test_lost_connection_still_reconnects_once(bridge: types.ModuleType, error: Exception) -> None:
    control = bridge.LoadControl()
    calls = []

    def flaky(bus: Any) -> str:
        calls.append(bus)
        if len(calls) == 1:
            raise error
        return "ok"

    assert control._with_bus(flaky) == "ok"
    assert (FakeBus.opened, FakeBus.closed) == (2, 1)
    assert calls[0] is not calls[1]


def test_lost_connection_twice_is_raised(bridge: types.ModuleType) -> None:
    control = bridge.LoadControl()

    def dead(bus: Any) -> None:
        raise EOFError("message bus closed the connection")

    with pytest.raises(EOFError):
        control._with_bus(dead)
    assert (FakeBus.opened, FakeBus.closed) == (2, 2)
    assert control.bus is None
