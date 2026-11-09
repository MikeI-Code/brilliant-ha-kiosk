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
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_@%+=:,./-]*$")
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
    """Render a value for the file: bare when safe, single-quoted otherwise."""
    if "\n" in value or "\r" in value or "\x00" in value:
        raise InvalidArgument("env values must not contain newlines or NUL")
    if _SAFE_VALUE_RE.match(value):
        return value
    if "'" in value:
        raise InvalidArgument("env values must not contain single quotes")
    return f"'{value}'"


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
