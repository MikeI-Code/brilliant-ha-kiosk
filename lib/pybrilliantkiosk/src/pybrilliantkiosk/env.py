"""Pure parse/serialise helpers for ``/etc/ha-kiosk/kiosk.env``.

Format: ``KEY=VALUE`` lines, ``#`` comments, blank lines; the last assignment of a key wins
(like systemd's EnvironmentFile). Unmodified lines are preserved verbatim, so comments and
ordering survive an edit.

SECURITY: ``kiosk.env`` is *sourced by root shell scripts* on the panel
(``. /etc/ha-kiosk/kiosk.env`` in ``kiosk/ha-kiosk-display.sh`` and ``ha-kiosk-swap.sh``).
Every line this module writes is therefore rendered exactly like ``render_env`` in
``kiosk/ha-kiosk-setup``: ``KEY="value"``, with values restricted to a charset in which a double
quoted shell string is inert (no ``"``, `` ` ``, ``$``, backslash or control character). A bare
``KEY=a;b`` would run ``b`` as root when sourced.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from .exceptions import InvalidArgument

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_BAD_VALUE_RE = re.compile(r"[\"'`$\\\x00-\x1f\x7f]")
#: A line a root shell can ``.``-source without running anything: ``KEY="inert"``, ``KEY='inert'``
#: or a bare value made of characters that need no quoting.
_SAFE_LINE_RE = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*="
    r"""(?:"[^"'`$\\\x00-\x1f\x7f]*"|'[^'"`$\\\x00-\x1f\x7f]*'|[A-Za-z0-9_@%+=:,./-]*)\s*$"""
)
MAX_VALUE_LEN = 1024
#: Settings ``ha-kiosk-setup`` accepts: a copy of ``KEYS`` in kiosk/ha-kiosk-setup (a test in the
#: repository keeps the two equal). Only the fallback: the panel's own tool is parsed when possible.
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
        "WEATHER_FX",
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
    """Validate a value for ``kiosk.env`` and return it unchanged.

    The setup tool rejects ``"``, `` ` ``, ``$``, backslash and control characters in values (and
    ``'`` separately); anything else, including spaces and ``; | & > ( )``, is accepted because
    :func:`render_line` writes the value inside double quotes, where those are inert.
    """
    if _BAD_VALUE_RE.search(value):
        raise InvalidArgument(
            "env values must not contain quotes, backslash, $, backtick or control characters"
        )
    if len(value) > MAX_VALUE_LEN:
        raise InvalidArgument("env value is too long")
    return value


def render_line(key: str, value: str) -> str:
    """One ``KEY="value"`` line, as ``ha-kiosk-setup`` renders it (safe to ``.``-source)."""
    return f'{validate_key(key)}="{format_value(value)}"'


def validate_setting_key(key: str, allowed: Collection[str] | None = None) -> str:
    """Return the key if ``ha-kiosk-setup`` accepts it as a setting.

    ``allowed`` is the panel's own list (see :func:`parse_setup_keys`); the bundled
    :data:`KNOWN_KEYS` is used when it is not known. Only the allowlist is dynamic: key and value
    charset rules stay as strict as ever.
    """
    validate_key(key)
    known = KNOWN_KEYS if allowed is None else allowed
    if key not in known and not _GESTURE_KEY_RE.match(key):
        raise InvalidArgument(f"unknown setting: {key!r}")
    return key


_SETTING_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_setup_keys(source: str) -> frozenset[str] | None:
    """The setting names in ``KEYS`` of a ``ha-kiosk-setup`` script, or None if not found.

    The script is parsed (never run). ``KEYS`` may be any module-level expression made of string
    literals: ``set("A B C".split())`` (a triple-quoted string in the tool today), ``{"A", "B"}``,
    ``frozenset([...])``; every whitespace separated word of every literal in it counts, which
    covers all of these. A word that is not an upper-case setting name is ignored.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value: ast.expr | None = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        else:
            continue
        if "KEYS" not in names or value is None:
            continue
        keys = {
            word
            for part in ast.walk(value)
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            for word in part.value.split()
            if _SETTING_NAME_RE.match(word)
        }
        return frozenset(keys) if keys else None
    return None


def validate_key(key: str) -> str:
    """Return the key if it is a valid variable name."""
    if not KEY_RE.match(key):
        raise InvalidArgument(f"invalid env key: {key!r}")
    return key


def drift_keys(saved: EnvFile, running: EnvFile) -> tuple[str, ...]:
    """Keys whose effective value differs between two copies (or that only one copy has)."""
    a, b = saved.values(), running.values()
    return tuple(k for k in dict.fromkeys((*a, *b)) if a.get(k) != b.get(k))


def reconcile(saved: EnvFile, running: EnvFile) -> tuple[EnvFile, tuple[str, ...], tuple[str, ...]]:
    """Merge two diverged copies: the saved copy plus what only the running copy has.

    The running copy (``/etc/ha-kiosk/kiosk.env``) is what the panel actually uses, so its values
    win for keys in both. Comments and order of the saved copy are kept; keys only the running
    copy has are appended. Returns ``(merged, adopted, skipped)``: the keys taken from the
    running copy, and those whose value could not be taken because it is not safe to write
    (kept as in the saved copy, or left out).
    """
    merged = saved
    saved_values = saved.values()
    adopted: list[str] = []
    skipped: list[str] = []
    for key, value in running.values().items():
        if saved_values.get(key) == value:
            continue
        try:
            merged = merged.set(key, value)
        except InvalidArgument:
            skipped.append(key)
        else:
            adopted.append(key)
    return merged, tuple(adopted), tuple(skipped)


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
        """Return a copy with ``key`` set.

        The last assignment is updated in place (or the line appended); earlier assignments of the
        same key are dropped, since they are shadowed anyway and would otherwise be carried over
        verbatim, whatever they contain.
        """
        validate_key(key)
        new = _Line(render_line(key, value), key, value)
        last = max((i for i, line in enumerate(self.lines) if line.key == key), default=None)
        if last is None:
            return EnvFile((*self.lines, new))
        return EnvFile(
            tuple(
                new if i == last else line
                for i, line in enumerate(self.lines)
                if i == last or line.key != key
            )
        )

    def unsafe_lines(self) -> tuple[str, ...]:
        """Keys (or ``line N`` for a line without one) of lines that are not inert when sourced.

        Lines this class rendered are always safe. Lines carried over verbatim from a file someone
        edited are not necessarily: ``A=1;rm -rf /`` assigns nothing, it runs a command, and
        ``kiosk.env`` is sourced by root shell scripts. Comments and blank lines are fine.
        """
        bad: list[str] = []
        for number, line in enumerate(self.lines, 1):
            text = line.raw.strip()
            if not text or text.startswith("#"):
                continue
            if line.key is None:
                bad.append(f"line {number}")
            elif not _SAFE_LINE_RE.match(line.raw) or len(line.value) > MAX_VALUE_LEN:
                bad.append(line.key)
        return tuple(dict.fromkeys(bad))

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
