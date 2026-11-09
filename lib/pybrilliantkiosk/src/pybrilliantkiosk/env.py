"""Pure parse/serialise helpers for ``/etc/ha-kiosk/kiosk.env``.

Format: ``KEY=VALUE`` lines, ``#`` comments, blank lines; the last assignment of a key wins
(like systemd's EnvironmentFile). Unmodified lines are preserved verbatim, so comments and
ordering survive an edit.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .exceptions import InvalidArgument

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_BAD_VALUE_RE = re.compile(r"[\"'`$\\\x00-\x1f\x7f]")
MAX_VALUE_LEN = 1024
#: Settings ``ha-kiosk-setup`` accepts (copy of KEYS in kiosk/ha-kiosk-setup, v1.4.0).
KNOWN_KEYS = frozenset(
    [
        "KIOSK_MODE",
        "HA_URL",
        "HA_ZOOM",
        "LAYOUT_DASHBOARD",
        "SLEEP_START",
        "SLEEP_END",
        "DAY_LEVEL",
        "DIM_LEVEL",
        "MIN_LEVEL",
        "IDLE_DIM_SECS",
        "NIGHT_WAKE_SECS",
        "NIGHT_MODE",
        "SCREENSAVER",
        "SCREENSAVER_WEATHER",
        "PHOTO_LEVEL",
        "PHOTO_SECS",
        "HA_PHOTO_FOLDER",
        "PHOTO_SYNC_MINS",
        "PHOTO_DIRS",
        "STOCK_ART",
        "TZ",
        "TZ_LOCAL",
        "MOTION_WAKE",
        "MOTION_CLEAR_SECS",
        "CPU_AWAKE_GOV",
        "CPU_IDLE_GOV",
        "CPU_SLEEP_GOV",
        "SWAP_MB",
        "SWAPPINESS",
        "SMART_DIM",
        "SUN_DARK_ELEV",
        "SUN_BRIGHT_ELEV",
        "AMBIENT_SECS",
        "LIGHT_SENSOR",
        "LUX_DARK",
        "LUX_BRIGHT",
        "LOAD_ENTITY",
        "ROOM_SPEAKERS",
        "ROOM_LIGHTS",
        "TOUCH_DEVICE",
        "KIOSK_MQTT",
        "MQTT_HOST",
        "MQTT_PORT",
        "MQTT_USER",
        "MQTT_PASSWORD",
        "MQTT_BASE",
        "MQTT_DISCOVERY_PREFIX",
        "MQTT_DEVICE_ID",
        "DIAG_SECS",
        "KIOSK_VOICE",
        "VOICE_PORT",
        "VOICE_NAME",
        "VOICE_WAKE",
        "VOICE_ALLOW",
        "VOICE_BIND",
        "VOICE_LISTEN_SECS",
        "VOICE_TTS_ENGINE",
        "VOICE_MIC",
        "VOICE_SPEAKER",
        "VOICE_DEBUG",
        "KIOSK_INTERCOM",
        "INTERCOM_MIC",
        "INTERCOM_PORT",
        "INTERCOM_MAX_SECS",
        "INTERCOM_MIC_DEVICE",
        "INTERCOM_SPEAKER",
        "INTERCOM_BIND",
        "KIOSK_CAPTURE",
        "CAPTURE_VIDEO",
        "CAPTURE_PORT",
        "CAPTURE_ALLOW",
        "CAPTURE_BIND",
        "CAPTURE_MAX_SECS",
        "CAPTURE_TTL",
        "CAPTURE_MIC",
        "CAPTURE_UPLOAD",
        "CAPTURE_UPLOAD_DIR",
        "CAMERA_LIVE",
        "CAMERA_LIVE_ON",
        "CLIMATE_CONTROLLER",
        "CLIMATE_THERMOSTATS",
        "DOORBELL",
        "DOORBELL_CAMERAS",
        "DOORBELL_MOTION",
        "DOORBELL_SECS",
        "SECURITY_TAB",
        "SECURITY_ACCESS",
        "SECURITY_DEVICES",
        "PANEL_GROUP",
        "ALLOW_MIC",
        "ALLOW_CAMERA",
    ]
)
_GESTURE_KEY_RE = re.compile(r"^GESTURE_[23]_(TAP|SWIPE_(LEFT|RIGHT|UP|DOWN))$")
SECRET_MARKERS = ("PASSWORD", "TOKEN", "SECRET")
MASK = "********"


def is_secret(key: str) -> bool:
    """True if the key looks like it holds a secret."""
    upper = key.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def format_value(value: str) -> str:
    """Validate a value for ``kiosk.env``; it is written bare (the setup tool rejects quotes)."""
    if _BAD_VALUE_RE.search(value):
        raise InvalidArgument(
            "env values must not contain quotes, backslash, $, backtick or control characters"
        )
    if len(value) > MAX_VALUE_LEN:
        raise InvalidArgument("env value is too long")
    return value


def validate_setting_key(key: str) -> str:
    """Return the key if ``ha-kiosk-setup`` accepts it as a setting."""
    validate_key(key)
    if key not in KNOWN_KEYS and not _GESTURE_KEY_RE.match(key):
        raise InvalidArgument(f"unknown setting: {key!r}")
    return key


def validate_key(key: str) -> str:
    """Return the key if it is a valid variable name."""
    if not KEY_RE.match(key):
        raise InvalidArgument(f"invalid env key: {key!r}")
    return key


@dataclass(frozen=True, slots=True)
class _Line:
    raw: str
    key: str | None = None
    value: str = ""


@dataclass(frozen=True, slots=True)
class EnvFile:
    """An immutable parsed env file."""

    lines: tuple[_Line, ...] = ()

    @classmethod
    def parse(cls, text: str) -> EnvFile:
        """Parse file text."""
        lines: list[_Line] = []
        for raw in text.splitlines():
            match = None if raw.lstrip().startswith("#") else _ASSIGN_RE.match(raw)
            if match:
                lines.append(_Line(raw, match.group(1), _unquote(match.group(2))))
            else:
                lines.append(_Line(raw))
        return cls(tuple(lines))

    def serialize(self) -> str:
        """Render back to text (each line newline-terminated)."""
        return "".join(f"{line.raw}\n" for line in self.lines)

    def values(self) -> dict[str, str]:
        """Effective values (last assignment wins), in first-seen order."""
        result: dict[str, str] = {}
        for line in self.lines:
            if line.key is not None:
                result[line.key] = line.value
        return result

    def masked_values(self) -> dict[str, str]:
        """Like :meth:`values` but non-empty secrets are replaced by a mask."""
        return {k: (MASK if v and is_secret(k) else v) for k, v in self.values().items()}

    def set(self, key: str, value: str) -> EnvFile:
        """Return a copy with ``key`` set (last occurrence updated in place, or appended)."""
        validate_key(key)
        new = _Line(f"{key}={format_value(value)}", key, value)
        lines = list(self.lines)
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].key == key:
                lines[idx] = new
                return EnvFile(tuple(lines))
        lines.append(new)
        return EnvFile(tuple(lines))

    def delete(self, key: str) -> EnvFile:
        """Return a copy without any assignment of ``key``."""
        validate_key(key)
        return EnvFile(tuple(line for line in self.lines if line.key != key))

    def apply(self, changes: Mapping[str, str], delete: Iterable[str] = ()) -> EnvFile:
        """Apply sets, then deletes."""
        result = self
        for key, value in changes.items():
            result = result.set(key, value)
        for key in delete:
            result = result.delete(key)
        return result
