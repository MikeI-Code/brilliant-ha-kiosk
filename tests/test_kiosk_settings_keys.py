"""Every setting the kiosk reads must be on ha-kiosk-setup's allowlist (KEYS).

WEATHER_FX shipped in the nightly without being added, so the setup tool (and anything that
validates against it) rejected a setting the UI uses.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _setup_keys() -> set[str]:
    src = (ROOT / "kiosk" / "ha-kiosk-setup").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "KEYS" for t in node.targets
        ):
            segment = ast.get_source_segment(src, node.value) or ""
            return set(re.findall(r"[A-Z][A-Z0-9_]+", segment))
    raise AssertionError("KEYS not found in kiosk/ha-kiosk-setup")


def _documented_keys() -> set[str]:
    text = (ROOT / "config" / "kiosk.env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)=", text, re.M))


def _qml_file_keys() -> set[str]:
    """Keys the QML UI parses straight out of kiosk.env with a regex."""
    keys: set[str] = set()
    for qml in (ROOT / "native").glob("*.qml"):
        # e.g.  /^\s*WEATHER_FX\s*=\s*"?(off|sky|full)/mi.exec(Ha.readFile(".../kiosk.env"))
        pattern = re.escape("^\\s*") + r"([A-Z][A-Z0-9_]+)" + re.escape("\\s*=")
        keys |= set(re.findall(pattern, qml.read_text(encoding="utf-8")))
    return keys


def _allowed(key: str, keys: set[str]) -> bool:
    """KEYS, or one of ha-kiosk-setup's KEY_PATTERNS (gesture bindings)."""
    return (
        key in keys
        or re.fullmatch(r"GESTURE_[23]_(TAP|SWIPE_(LEFT|RIGHT|UP|DOWN))", key) is not None
    )


def test_documented_settings_are_allowed() -> None:
    keys = _setup_keys()
    assert {k for k in _documented_keys() if not _allowed(k, keys)} == set()


def test_settings_read_by_the_ui_are_allowed() -> None:
    found = _qml_file_keys()
    assert "WEATHER_FX" in found  # the scan itself works
    assert found - _setup_keys() == set()
