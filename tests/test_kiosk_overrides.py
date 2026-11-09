"""overrides.env is sourced by root shell scripts, so what ha-kiosk-mqtt.py writes must be inert.

Plain pytest, no Home Assistant: the kiosk script is loaded by path with a stub for paho.
"""

from __future__ import annotations

import importlib.machinery
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


# -- the bridge reads kiosk.env and overrides.env like ha-kiosk-setup does ---------------------


def _setup_tool() -> types.ModuleType:
    """kiosk/ha-kiosk-setup (no .py suffix) loaded as a module, to compare its parser."""
    path = ROOT / "kiosk" / "ha-kiosk-setup"
    loader = importlib.machinery.SourceFileLoader("ha_kiosk_setup_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ENV_TEXTS = [
    'MQTT_PASSWORD="pass#word"\nMQTT_USER=user#1\n',
    "HA_URL=http://ha.local:8123/#frag\n",
    "KIOSK_MODE=native # not a comment: part of the value\n",
    '# comment with KEY="x"\n   # indented comment\nDAY_LEVEL=15\n',
    "DIM_LEVEL = 5\nMIN_LEVEL= 5\nDAY_LEVEL =5\nSLEEP_START=21:00\n",  # spaces around "="
    "\tDAY_LEVEL=15\t\n  DIM_LEVEL=4  \n",
    "SCREENSAVER=\"a b\"\nNIGHT_MODE='c d'\nMOTION_WAKE=\"\"\nSMART_DIM=''\n",
    'SCREENSAVER="x\nNIGHT_MODE=y"\nMOTION_WAKE="\nSMART_DIM="""\n',  # unbalanced quotes
    "DAY_LEVEL=1\nDAY_LEVEL=$(touch pwned)\n",  # a rejected line does not replace the good one
    'DAY_LEVEL="1"\nDAY_LEVEL="2"\n',  # the last good assignment wins
    "DAY_LEVEL=`id`\nDIM_LEVEL=a\\b\nMIN_LEVEL=it's\nSLEEP_END=a\"b\n",
    "DAY_LEVEL=" + "x" * 1024 + "\nDIM_LEVEL=" + "y" * 1025 + "\n",
    "SLEEP_START=\x07bell\nSLEEP_END=ok\r\nTZ=UTC\r\n",
    "_LEADING=1\nlower=1\nA B=1\nNOT KEY\n=1\nTZ=UTC\n",
    "",
    "\n\n   \n",
]


@pytest.mark.parametrize("text", ENV_TEXTS)
def test_the_bridge_parses_env_files_exactly_like_ha_kiosk_setup(
    mqtt_mod: types.ModuleType, text: str
) -> None:
    expected, _ = _setup_tool().parse_env(text)
    values, _ = mqtt_mod.parse_env(text)
    assert values == expected


def test_a_hash_inside_a_value_is_not_the_end_of_it(
    mqtt_mod: types.ModuleType, tmp_path: Path
) -> None:
    """The bridge cut values at "#": a password or URL with one was silently truncated."""
    path = tmp_path / "kiosk.env"
    path.write_text('MQTT_PASSWORD="pass#word"\nHA_URL=http://h/#x\nTZ=UTC\n', encoding="utf-8")
    assert mqtt_mod.read_env(str(path)) == {
        "MQTT_PASSWORD": "pass#word",
        "HA_URL": "http://h/#x",
        "TZ": "UTC",
    }


def test_a_value_with_a_hash_survives_an_override_rewrite(mqtt_mod: types.ModuleType) -> None:
    path = Path(mqtt_mod.OVERRIDES)
    path.parent.mkdir(parents=True)
    path.write_text('TZ_LOCAL="Zone#1"\n', encoding="utf-8")
    mqtt_mod.set_override("SCREENSAVER", "0")
    assert mqtt_mod.read_env(mqtt_mod.OVERRIDES) == {"SCREENSAVER": "0", "TZ_LOCAL": "Zone#1"}


def test_parse_env_reports_the_keys_of_rejected_lines(mqtt_mod: types.ModuleType) -> None:
    values, rejected = mqtt_mod.parse_env("A=1\nB=$(x)\nC=ok\nD=`y`\nnot a line\n")
    assert values == {"A": "1", "C": "ok"}
    assert rejected == ["B", "D"]


def test_unsafe_lines_are_cleaned_out_even_when_the_value_did_not_change(
    mqtt_mod: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    path = Path(mqtt_mod.OVERRIDES)
    path.parent.mkdir(parents=True)
    path.write_text('SCREENSAVER="0"\nEVIL=$(touch pwned)\n', encoding="utf-8")
    mqtt_mod.set_override("SCREENSAVER", "0")
    assert "EVIL" not in path.read_text(encoding="utf-8")
    assert "dropped unsafe override" in capsys.readouterr().out
