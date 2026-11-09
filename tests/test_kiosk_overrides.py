"""overrides.env is sourced by root shell scripts, so what ha-kiosk-mqtt.py writes must be inert.

Plain pytest, no Home Assistant: the kiosk script is loaded by path with a stub for paho.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH = shutil.which("sh")


@pytest.fixture
def mqtt_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """ha-kiosk-mqtt.py with paho stubbed, OVERRIDES in tmp_path and systemctl recorded."""
    paho = types.ModuleType("paho")
    client = types.ModuleType("paho.mqtt.client")
    mqtt_pkg = types.ModuleType("paho.mqtt")
    monkeypatch.setitem(sys.modules, "paho", paho)
    monkeypatch.setitem(sys.modules, "paho.mqtt", mqtt_pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", client)
    mqtt_pkg.client = client  # type: ignore[attr-defined]
    spec = importlib.util.spec_from_file_location(
        "ha_kiosk_mqtt_under_test", ROOT / "kiosk" / "ha-kiosk-mqtt.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OVERRIDES", str(tmp_path / "var" / "overrides.env"))
    calls: list[list[str]] = []
    # The module's own reference only: patching subprocess.run itself would break the tests.
    fake_subprocess = types.SimpleNamespace(run=lambda cmd, **_: calls.append(cmd))
    monkeypatch.setattr(module, "subprocess", fake_subprocess)
    module.restarts = calls
    return module


def _source(path: Path, marker: Path) -> str:
    """Source ``path`` in sh from a scratch directory; returns what VALUE ended up as."""
    assert SH
    script = f'cd "{marker.parent}" && . "{path}"; printf %s "$VALUE"'
    done = subprocess.run([SH, "-c", script], capture_output=True, text=True, check=True)  # noqa: S603
    return done.stdout


def test_values_are_written_quoted(mqtt_mod: types.ModuleType) -> None:
    mqtt_mod.set_override("SCREENSAVER", "0")
    mqtt_mod.set_override("MOTION_WAKE", "day")
    text = Path(mqtt_mod.OVERRIDES).read_text(encoding="utf-8")
    assert 'MOTION_WAKE="day"\n' in text
    assert 'SCREENSAVER="0"\n' in text
    assert "SCREENSAVER=0" not in text.replace('SCREENSAVER="0"', "")
    assert mqtt_mod.read_env(mqtt_mod.OVERRIDES) == {"MOTION_WAKE": "day", "SCREENSAVER": "0"}
    assert mqtt_mod.restarts == [["systemctl", "restart", "ha-kiosk-display"]] * 2


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("DAY_LEVEL", "1$(touch pwned)"),
        ("DAY_LEVEL", "`touch pwned`"),
        ("DAY_LEVEL", 'x"; touch pwned; "'),
        ("DAY_LEVEL", "it's"),
        ("DAY_LEVEL", "back\\slash"),
        ("DAY_LEVEL", "a\nb"),
        ("DAY_LEVEL", "x" * 1025),
        ("DAY_LEVEL;touch pwned", "1"),
        ("lower", "1"),
        ("", "1"),
    ],
)
def test_unsafe_payloads_are_refused_and_logged(
    mqtt_mod: types.ModuleType,
    var: str,
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mqtt_mod.set_override(var, value)
    assert not Path(mqtt_mod.OVERRIDES).exists()
    assert mqtt_mod.restarts == []
    assert "refusing to write unsafe override" in capsys.readouterr().out


def test_unchanged_value_does_not_rewrite_or_restart(mqtt_mod: types.ModuleType) -> None:
    mqtt_mod.set_override("SCREENSAVER", "1")
    mqtt_mod.restarts.clear()
    mqtt_mod.set_override("SCREENSAVER", "1")
    assert mqtt_mod.restarts == []


def test_unsafe_entries_already_in_the_file_are_dropped(
    mqtt_mod: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    path = Path(mqtt_mod.OVERRIDES)
    path.parent.mkdir(parents=True)
    path.write_text(
        "OLD=1\nEVIL=$(touch pwned)\nTICK=`touch pwned`\nbad key=1\nKEPT=a;b\n", encoding="utf-8"
    )
    mqtt_mod.set_override("SCREENSAVER", "0")
    text = path.read_text(encoding="utf-8")
    assert "touch" not in text
    assert 'OLD="1"' in text
    assert 'KEPT="a;b"' in text
    assert "EVIL" not in text
    assert "dropped unsafe override" in capsys.readouterr().out


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh")
def test_written_file_is_inert_when_sourced_by_sh(
    mqtt_mod: types.ModuleType, tmp_path: Path
) -> None:
    """Hand-written or legacy lines with shell metacharacters must not run when sourced."""
    path = Path(mqtt_mod.OVERRIDES)
    path.parent.mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    marker = work / "pwned"
    # Unquoted legacy lines as an older version (or a person) may have left them.
    path.write_text(
        f"VALUE=x;touch {marker}\nOTHER=a|touch {marker}\nAMP=a&touch {marker}\n",
        encoding="utf-8",
    )
    mqtt_mod.set_override("SCREENSAVER", "0")
    assert _source(path, marker) == f"x;touch {marker}"  # the text, not a command
    assert not marker.exists()
    assert 'SCREENSAVER="0"' in path.read_text(encoding="utf-8")


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh")
def test_values_with_shell_metacharacters_that_are_inert_in_quotes_round_trip(
    mqtt_mod: types.ModuleType, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    marker = work / "pwned"
    value = "a b; c | d & e > f ( g ) # h"
    mqtt_mod.set_override("VALUE", value)
    assert _source(Path(mqtt_mod.OVERRIDES), marker) == value
    assert not marker.exists()
    assert list(work.iterdir()) == []  # `>` created no file either
