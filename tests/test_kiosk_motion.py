"""ha-kiosk-mqtt motion window: the Motion sensor follows the faceplate's movement_detected.

The kiosk scripts have hyphens in their names and import paho (not installed here), so the pure
function under test is cut out of the source and run on its own (as the other kiosk tests do).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, *functions: str) -> dict[str, Any]:
    src = (ROOT / "kiosk" / name).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in functions]
    assert {n.name for n in keep} == set(functions)
    scope: dict[str, Any] = {}
    exec(compile(ast.Module(keep, []), name, "exec"), scope)  # noqa: S102
    return scope


motion_window = _load("ha-kiosk-mqtt.py", "motion_window")["motion_window"]


def test_detection_opens_the_clear_window() -> None:
    until, detected = motion_window("1", 1000.0, 0.0, 60)
    assert detected is True
    assert until == 1060.0


def test_still_holds_the_existing_window() -> None:
    until, detected = motion_window("0", 1000.0, 1050.0, 60)
    assert detected is False
    assert until == 1050.0


def test_absent_or_non_one_values_are_not_motion() -> None:
    for value in (None, "", "0", "2"):
        until, detected = motion_window(value, 5.0, 9.0, 60)
        assert detected is False
        assert until == 9.0
