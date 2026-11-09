"""Retained discovery configs: recorded when published, withdrawn by ha-kiosk-setup.

Plain pytest, no Home Assistant: the kiosk scripts are loaded by path, paho is stubbed.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tests.test_kiosk_overrides import mqtt_mod  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
KIOSK = ROOT / "kiosk"
NODE = "brilliant_0123456789ab"


def _module(name: str, filename: str) -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(KIOSK / filename))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def common() -> types.ModuleType:
    return _module("ha_kiosk_common_under_test", "ha-kiosk-common.py")


class FakeClient:
    """Records publishes (topic, payload, retain) like paho's client."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []

    def publish(self, topic: str, payload: str = "", retain: bool = False, **_: Any) -> Any:
        self.published.append((topic, payload, retain))
        return types.SimpleNamespace(wait_for_publish=lambda *_a, **_k: None)

    def configs(self) -> set[str]:
        return {t for t, p, _ in self.published if t.endswith("/config") and p}


class FakeControl:
    def peripherals(self) -> list[str]:
        return ["gangbox_peripheral_0", "homekit_peripheral", "ble_peripheral"]


def _kiosk_mqtt(mod: types.ModuleType, tmp_path: Path, env: dict[str, str]) -> Any:
    topics_file = tmp_path / "var" / "mqtt-discovery-topics"
    mod.topics = mod.common.DiscoveryTopics(str(topics_file))
    k = mod.KioskMqtt.__new__(mod.KioskMqtt)
    k.env = env
    k.control = FakeControl()
    k.dev_id = "0123456789abcdef"
    k.room_name, k.room = "Kitchen", "Kitchen"
    k.node = NODE
    k.base = f"{env.get('MQTT_BASE', 'brilliant_kiosk')}/{k.node}"
    k.disc = env.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
    k.kiosk_only = env.get("KIOSK_MQTT") == "kiosk"
    k.device_id = env.get("MQTT_DEVICE_ID", "")
    k.last, k.peripherals, k.gangboxes, k.next_diag = {}, set(), [0], 0.0
    k.c = FakeClient()
    k.topics_file = topics_file
    return k


# ---------------------------------------------------------------- recording


def test_the_mqtt_service_records_every_config_it_publishes(
    mqtt_mod: types.ModuleType,  # noqa: F811
    tmp_path: Path,
) -> None:
    k = _kiosk_mqtt(mqtt_mod, tmp_path, {})
    k.publish_discovery()
    published = k.c.configs()
    assert f"homeassistant/light/{NODE}/load/config" in published
    assert f"homeassistant/sensor/{NODE}/shelly_ble/config" in published
    assert mqtt_mod.common.read_topics(str(k.topics_file)) == published


def test_a_custom_discovery_prefix_is_recorded_as_published(
    mqtt_mod: types.ModuleType,  # noqa: F811
    tmp_path: Path,
) -> None:
    k = _kiosk_mqtt(mqtt_mod, tmp_path, {"MQTT_DISCOVERY_PREFIX": "ha/disc", "MQTT_BASE": "kb"})
    k.publish_discovery()
    recorded = mqtt_mod.common.read_topics(str(k.topics_file))
    assert recorded == k.c.configs()
    assert recorded
    assert all(t.startswith("ha/disc/") for t in recorded)


def test_ring_locks_and_security_configs_are_recorded_too(
    mqtt_mod: types.ModuleType,  # noqa: F811
    tmp_path: Path,
) -> None:
    k = _kiosk_mqtt(mqtt_mod, tmp_path, {})
    k.ring_alarm("disarmed")
    k.brilliant_lock({"name": "front door", "state": "locked"})
    k.ring_doorbell("Front")
    recorded = mqtt_mod.common.read_topics(str(k.topics_file))
    assert recorded == k.c.configs()
    assert f"homeassistant/sensor/{NODE}/lock_front_door/config" in recorded
    assert f"homeassistant/sensor/{NODE}/ring_alarm/config" in recorded
    assert f"homeassistant/event/{NODE}/ring_doorbell/config" in recorded


def test_kiosk_mode_records_only_what_the_kiosk_owns(
    mqtt_mod: types.ModuleType,  # noqa: F811
    tmp_path: Path,
) -> None:
    k = _kiosk_mqtt(mqtt_mod, tmp_path, {"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "other"})
    # An earlier full-mode run recorded a panel entity; the other integration owns it now.
    owned_before = f"homeassistant/light/{NODE}/load/config"
    mqtt_mod.topics.add(owned_before)
    k.publish_discovery()
    recorded = mqtt_mod.common.read_topics(str(k.topics_file))
    assert recorded == k.c.configs()
    assert owned_before not in recorded
    panel_owned = {f"homeassistant/{c}/{NODE}/{key}/config" for c, key in k.PANEL_OWNED}
    assert not panel_owned & recorded
    assert f"homeassistant/sensor/{NODE}/display_state/config" in recorded


def test_recording_is_a_union_across_processes_and_atomic(
    common: types.ModuleType, tmp_path: Path
) -> None:
    path = str(tmp_path / "var" / "mqtt-discovery-topics")
    a, b = common.DiscoveryTopics(path), common.DiscoveryTopics(path)
    a.add("homeassistant/sensor/n/a/config")
    b.add("homeassistant/button/n/b/config")
    a.add("homeassistant/sensor/n/a/config")  # a repeat does not duplicate
    assert common.read_topics(path) == {
        "homeassistant/sensor/n/a/config",
        "homeassistant/button/n/b/config",
    }
    assert Path(path).read_text(encoding="utf-8").count("\n") == 2
    b.discard("homeassistant/sensor/n/a/config")
    assert common.read_topics(path) == {"homeassistant/button/n/b/config"}
    assert [p.name for p in Path(path).parent.iterdir() if p.name.startswith(".mqtt-")] == []


def test_invalid_topics_are_never_recorded(common: types.ModuleType, tmp_path: Path) -> None:
    path = str(tmp_path / "t")
    rec = common.DiscoveryTopics(path)
    for bad in (
        "homeassistant/+/n/k/config",
        "homeassistant/#",
        "a/b/c/config",
        "x y/b/c/d/config",
        "homeassistant//n/k/config",
        "homeassistant/sensor/n/k/state",
    ):
        rec.add(bad)
    assert not Path(path).exists()


def _publisher_scope(filename: str, **extra: Any) -> dict[str, Any]:
    """The ``_cfg`` method (and ``_topics``) of a service script, cut out and run on its own."""
    tree = ast.parse((KIOSK / filename).read_text(encoding="utf-8"))
    keep: list[ast.stmt] = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_topics"
    ]
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            keep += [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "_cfg"]
    assert {n.name for n in keep} == {"_cfg", "_topics"}, filename
    scope: dict[str, Any] = {"__file__": str(KIOSK / filename), "json": json, "os": os}
    scope.update(extra)
    exec(compile(ast.Module(keep, []), filename, "exec"), scope)  # noqa: S102
    return scope


def _publisher(client: FakeClient) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        c=client,
        disc="ha/x",
        node=NODE,
        base=f"kb/{NODE}",
        room="Den",
        ic=types.SimpleNamespace(node=NODE, room="Den"),
    )


@pytest.mark.parametrize(
    "filename", ["ha-kiosk-voice.py", "ha-kiosk-capture.py", "ha-kiosk-intercom.py"]
)
def test_voice_capture_and_intercom_record_what_they_publish(
    common: types.ModuleType, tmp_path: Path, filename: str
) -> None:
    path = str(tmp_path / "var" / "topics")
    scope = _publisher_scope(filename, _TOPICS=common.DiscoveryTopics(path))
    client = FakeClient()
    scope["_cfg"](_publisher(client), "sensor", "thing", {"name": "Thing"})
    expected = f"ha/x/sensor/{NODE}/thing/config"
    assert [(t, r) for t, _, r in client.published] == [(expected, True)]
    assert common.read_topics(path) == {expected}


def test_intercom_withdrawals_are_not_recorded(common: types.ModuleType, tmp_path: Path) -> None:
    path = str(tmp_path / "topics")
    scope = _publisher_scope("ha-kiosk-intercom.py", _TOPICS=common.DiscoveryTopics(path))
    client = FakeClient()
    scope["_cfg"](_publisher(client), "select", "intercom_target", None)
    assert client.published == [(f"ha/x/select/{NODE}/intercom_target/config", "", True)]
    assert common.read_topics(path) == set()


# ---------------------------------------------------------------- withdrawal


@pytest.fixture
def setup_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """ha-kiosk-setup with its state under tmp_path and a stub paho recording publishes."""
    tool = _module("ha_kiosk_setup_withdraw_test", "ha-kiosk-setup")
    var = tmp_path / "ha-kiosk"
    (var / "config").mkdir(parents=True)
    monkeypatch.setattr(tool, "VAR", str(var))
    monkeypatch.setattr(tool, "CFG", str(var / "config"))
    monkeypatch.setattr(tool, "DISCOVERY_TOPICS", str(var / "mqtt-discovery-topics"))
    monkeypatch.setattr(tool, "LOG", str(var / "setup.log"))
    clients: list[FakeClient] = []

    class Client(FakeClient):
        def __init__(self, *_a: Any, **_k: Any) -> None:
            super().__init__()
            clients.append(self)

        def username_pw_set(self, *_a: Any) -> None: ...
        def connect(self, *_a: Any) -> None: ...
        def loop_start(self) -> None: ...
        def loop_stop(self) -> None: ...
        def disconnect(self) -> None: ...

    client_mod = types.ModuleType("paho.mqtt.client")
    client_mod.Client = Client  # type: ignore[attr-defined]
    client_mod.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "paho", types.ModuleType("paho"))
    monkeypatch.setitem(sys.modules, "paho.mqtt", types.ModuleType("paho.mqtt"))
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", client_mod)
    tool.clients = clients
    return tool


def _panel_state(
    tool: types.ModuleType, topics: list[str], *, base: str = f"kb/{NODE}", host: bool = True
) -> None:
    env = 'MQTT_HOST="broker"\n' if host else "TZ=UTC\n"
    Path(tool.CFG, "kiosk.env").write_text(env, encoding="utf-8")
    if base:
        Path(tool.VAR, "mqtt_base").write_text(base, encoding="utf-8")
    Path(tool.DISCOVERY_TOPICS).write_text("".join(t + "\n" for t in topics), encoding="utf-8")


TOPICS = [
    f"homeassistant/sensor/{NODE}/display_state/config",
    f"ha/disc/button/{NODE}/voice_talk/config",
    f"homeassistant/event/{NODE}/capture_event/config",
]


def _empties(tool: types.ModuleType) -> list[str]:
    return sorted(t for c in tool.clients for t, p, _ in c.published if p == "")


@pytest.mark.parametrize("state", ["disabled", "not_installed"])
def test_disable_and_uninstall_withdraw_exactly_the_recorded_topics(
    setup_tool: types.ModuleType, state: str
) -> None:
    _panel_state(setup_tool, TOPICS)
    setup_tool.mqtt_state(state)
    (client,) = setup_tool.clients
    assert _empties(setup_tool) == sorted(TOPICS)
    assert all(retain for _, _, retain in client.published)
    assert (f"kb/{NODE}/kiosk_state", state, True) in client.published
    assert not Path(setup_tool.DISCOVERY_TOPICS).exists()


def test_withdrawal_ignores_damaged_and_wildcard_lines(setup_tool: types.ModuleType) -> None:
    bad = [
        "homeassistant/#",
        "homeassistant/+/n/k/config",
        "junk",
        "homeassistant/sensor/n/config",
        "",
    ]
    _panel_state(setup_tool, [*TOPICS, *bad])
    setup_tool.mqtt_state("not_installed")
    assert _empties(setup_tool) == sorted(TOPICS)


def test_a_failed_install_withdraws_nothing(setup_tool: types.ModuleType) -> None:
    _panel_state(setup_tool, TOPICS)
    setup_tool.mqtt_state("failed")
    assert _empties(setup_tool) == []
    assert Path(setup_tool.DISCOVERY_TOPICS).exists()


def test_withdrawal_works_without_a_recorded_base(setup_tool: types.ModuleType) -> None:
    _panel_state(setup_tool, TOPICS, base="")
    setup_tool.mqtt_state("disabled")
    (client,) = setup_tool.clients
    assert sorted(t for t, _, _ in client.published) == sorted(TOPICS)


def test_nothing_is_published_without_a_broker(setup_tool: types.ModuleType) -> None:
    _panel_state(setup_tool, TOPICS, host=False)
    setup_tool.mqtt_state("disabled")
    assert setup_tool.clients == []
    assert Path(setup_tool.DISCOVERY_TOPICS).exists()


def test_an_empty_record_publishes_the_state_only(setup_tool: types.ModuleType) -> None:
    _panel_state(setup_tool, [])
    setup_tool.mqtt_state("disabled")
    assert _empties(setup_tool) == []
    assert len(setup_tool.clients) == 1


def test_the_record_is_kept_when_the_broker_is_unreachable(setup_tool: types.ModuleType) -> None:
    _panel_state(setup_tool, TOPICS)

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("no route")

    sys.modules["paho.mqtt.client"].Client.connect = boom  # type: ignore[attr-defined]
    setup_tool.mqtt_state("disabled")  # best effort: no exception
    assert Path(setup_tool.DISCOVERY_TOPICS).exists()
