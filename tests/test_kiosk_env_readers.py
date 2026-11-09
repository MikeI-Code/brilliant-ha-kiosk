"""Every Python reader of kiosk.env / overrides.env reads it like ha-kiosk-setup's parse_env."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

from tests.test_kiosk_overrides import ENV_TEXTS, mqtt_mod  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
KIOSK = ROOT / "kiosk"

EXTRA_TEXTS = [
    "export DAY_LEVEL=5\nDIM_LEVEL=4\n",  # setup does not accept `export`; nor may a reader
    'DAY_LEVEL=5\r\nDIM_LEVEL="4"\r\n# c\r\nMIN_LEVEL=3\r',
    "\ufeffDAY_LEVEL=5\nDIM_LEVEL=4\n",
    'MQTT_PASSWORD="a b # c"\nMQTT_PASSWORD=second#\nTZ="x y"  \n',
    'HA_URL="http://h:8123/a#b"\nPHOTO_SYNC_MINS="0"\n',
    "SECURITY_DEVICES=Front Door,Unit 2B Door\nSECURITY_ACCESS=view\n",
    'SECURITY_ACCESS="view" # trailing\nSECURITY_DEVICES=\n',
    'ROOM_LIGHTS=light.a, light.b\nSCREENSAVER="\'"\n',
]
ALL_TEXTS = [*ENV_TEXTS, *EXTRA_TEXTS]


def _load(name: str, filename: str) -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(KIOSK / filename))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def setup_tool() -> types.ModuleType:
    return _load("ha_kiosk_setup_parity", "ha-kiosk-setup")


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    bus = types.ModuleType("brilliant_bus")
    bus.Bus = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "brilliant_bus", bus)
    return _load("ha_kiosk_bridge_parity", "ha-kiosk-bridge.py")


def _known(setup_tool: types.ModuleType, env: dict[str, str]) -> dict[str, str]:
    """Readers keep any KEY; the setup tool also drops settings it does not know."""
    return {
        k: v
        for k, v in env.items()
        if k in setup_tool.KEYS or any(p.fullmatch(k) for p in setup_tool.KEY_PATTERNS)
    }


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return str(path)


@pytest.mark.parametrize("text", ALL_TEXTS)
def test_the_shared_parser_matches_setup(setup_tool: types.ModuleType, text: str) -> None:
    common = _load("ha_kiosk_common_parity", "ha-kiosk-common.py")
    expected, _ = setup_tool.parse_env(text)
    assert _known(setup_tool, common.parse_env(text)[0]) == expected


@pytest.mark.parametrize("text", ALL_TEXTS)
def test_the_mqtt_service_reads_like_setup(
    setup_tool: types.ModuleType,
    mqtt_mod: types.ModuleType,  # noqa: F811
    tmp_path: Path,
    text: str,
) -> None:
    expected, _ = setup_tool.parse_env(text)
    assert _known(setup_tool, mqtt_mod.parse_env(text)[0]) == expected
    assert _known(setup_tool, mqtt_mod.read_env(_write(tmp_path / "kiosk.env", text))) == expected


@pytest.mark.parametrize("text", ALL_TEXTS)
def test_the_ambient_script_reads_like_setup(
    setup_tool: types.ModuleType, tmp_path: Path, text: str
) -> None:
    ambient = _load("ha_kiosk_ambient_parity", "ha-kiosk-ambient.py")
    expected, _ = setup_tool.parse_env(text)
    assert _known(setup_tool, ambient.env(_write(tmp_path / "kiosk.env", text))) == expected


@pytest.mark.parametrize("text", ALL_TEXTS)
def test_the_bridge_reads_like_setup_and_overrides_win(
    setup_tool: types.ModuleType,
    bridge: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
) -> None:
    monkeypatch.setattr(bridge.common, "KIOSK_ENV", _write(tmp_path / "kiosk.env", text))
    monkeypatch.setattr(bridge.common, "OVERRIDES_ENV", str(tmp_path / "missing.env"))
    for k in ("SECURITY_ACCESS", "SECURITY_DEVICES"):
        monkeypatch.delenv(k, raising=False)
    expected, _ = setup_tool.parse_env(text)
    assert _known(setup_tool, bridge.read_kiosk_env()) == expected
    # overrides.env is read after kiosk.env, with the same rules
    over = text + '\nTZ="Zone#1"\n'
    monkeypatch.setattr(bridge.common, "OVERRIDES_ENV", _write(tmp_path / "var" / "o.env", over))
    merged = {**expected, **setup_tool.parse_env(over)[0]}
    assert _known(setup_tool, bridge.read_kiosk_env()) == merged


def test_the_bridge_photo_sync_and_mqtt_start_use_the_shared_reader() -> None:
    src = (KIOSK / "ha-kiosk-bridge.py").read_text(encoding="utf-8")
    assert src.count("common.read_settings()") == 2  # read_kiosk_env and start_photo_sync
    assert "common.read_env(" in src


def test_no_service_has_its_own_kiosk_env_parser() -> None:
    """A naive parser (split on '=', strip quotes, cut at '#') must not come back."""
    naive = (r"""\.split\(\s*["']=["']""", r"""\.strip\(\s*['"]"['"]\s*\)""", r"""\[\^"\\n#\]""")
    for path in sorted(KIOSK.glob("ha-kiosk-*.py")):
        if path.name == "ha-kiosk-common.py":
            continue
        src = path.read_text(encoding="utf-8")
        for pattern in naive:
            assert not re.search(pattern, src), f"{path.name}: naive env parsing ({pattern})"
        for line in src.splitlines():
            assert not ("kiosk.env" in line and "open(" in line), f"{path.name}: {line.strip()}"
