"""WEATHER_FX defaults to the cheap ``sky`` mode; ``full`` (about one CPU core) is opt-in."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_sky_qml_falls_back_to_sky_without_a_setting() -> None:
    src = (ROOT / "native" / "Sky.qml").read_text(encoding="utf-8")
    block = re.search(r"readonly property string mode: \{(.*?)\n    \}", src, re.S)
    assert block, "mode property not found"
    fallback = re.search(r"return m \? m\[1\]\.toLowerCase\(\) : \"(\w+)\";", block.group(1))
    assert fallback, "fallback of mode not found"
    assert fallback.group(1) == "sky"
    assert "(off|sky|full)" in block.group(1), "full must stay selectable"


def test_no_other_qml_file_picks_its_own_default() -> None:
    for qml in (ROOT / "native").glob("*.qml"):
        if qml.name != "Sky.qml":
            code = re.sub(r"//.*", "", qml.read_text(encoding="utf-8"))
            assert "WEATHER_FX" not in code, qml.name


def test_the_example_file_sets_sky() -> None:
    text = (ROOT / "config" / "kiosk.env.example").read_text(encoding="utf-8")
    assert re.search(r"^WEATHER_FX=sky\s*$", text, re.MULTILINE)
    assert "Default sky" in text
