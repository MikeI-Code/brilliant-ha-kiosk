"""The retained ``<base>/discovery`` JSON the kiosk publishes for the Home Assistant integration.

The kiosk scripts have hyphens in their names and import paho (not installed here), so the
functions are cut out of the source and run on their own.
"""

import ast
import json
import socket
from pathlib import Path
from typing import Any, ClassVar

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, *functions: str) -> dict[str, Any]:
    src = (ROOT / "kiosk" / name).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in functions]
    assert {n.name for n in keep} == set(functions)
    scope: dict[str, Any] = {"json": json}
    exec(compile(ast.Module(keep, []), name, "exec"), scope)  # noqa: S102
    return scope


MQTT = _load("ha-kiosk-mqtt.py", "discovery_payload", "lan_ip")
SETUP = _load("ha-kiosk-setup", "discovery_payload")


@pytest.mark.parametrize("scope", [MQTT, SETUP])
def test_payload_has_state_id_room_and_host(scope: dict[str, Any]) -> None:
    text = scope["discovery_payload"]("active", "192.168.4.20", "0123456789abcdef", "Kitchen")
    assert json.loads(text) == {
        "state": "active",
        "host": "192.168.4.20",
        "device_id": "0123456789abcdef",
        "room": "Kitchen",
    }


@pytest.mark.parametrize("scope", [MQTT, SETUP])
@pytest.mark.parametrize("room", [None, ""])
def test_payload_without_host_or_room(scope: dict[str, Any], room: str | None) -> None:
    data = json.loads(scope["discovery_payload"]("disabled", None, "0123456789ab", room))
    assert data == {"state": "disabled", "device_id": "0123456789ab", "room": None}


class _Sock:
    def __init__(self, address: Any) -> None:
        self.address = address

    def getsockname(self) -> Any:
        if isinstance(self.address, Exception):
            raise self.address
        return self.address


def test_lan_ip_is_the_local_address_of_the_mqtt_socket() -> None:
    assert MQTT["lan_ip"](_Sock(("192.168.4.20", 40000)), "broker") == "192.168.4.20"


class _Probe:
    """A stand-in for the UDP socket the route lookup opens (the test run blocks sockets)."""

    address: Any = ("192.168.4.20", 40000)
    connected: ClassVar[list[Any]] = []

    def __init__(self, *args: Any) -> None:
        pass

    def __enter__(self) -> "_Probe":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def connect(self, target: Any) -> None:
        self.connected.append(target)
        if isinstance(self.address, Exception):
            raise self.address

    def getsockname(self) -> Any:
        return self.address


@pytest.mark.parametrize("address", [("127.0.0.1", 1), ("::1", 1), ("", 1), OSError("closed")])
def test_lan_ip_falls_back_to_the_route_to_the_broker(
    address: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Probe.connected = []
    monkeypatch.setattr(socket, "socket", _Probe)
    assert MQTT["lan_ip"](_Sock(address), "broker.lan") == "192.168.4.20"
    assert _Probe.connected == [("broker.lan", 9)]


@pytest.mark.parametrize("address", [OSError("no route"), ("127.0.0.1", 1), ("", 1)])
def test_lan_ip_is_unknown_when_even_the_route_lookup_fails(
    address: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "socket", _Probe)
    monkeypatch.setattr(_Probe, "address", address)
    assert MQTT["lan_ip"](_Sock(OSError()), "broker.lan") is None
