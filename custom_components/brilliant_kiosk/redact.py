"""Scrubbing of text that leaves Home Assistant (diagnostics, the ``logs`` action).

Free text from the panel (journal lines, the setup tool's messages) can name the panel's address,
its logins and, in a bad moment, a token. Everything that identifies this panel is replaced by
value, whichever field it appears in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST

from .const import CONF_HOST_KEY, CONF_MQTT_DEVICE_ID, CONF_NODE

REDACTED = "**REDACTED**"

#: Setting names whose values are also removed from free text.
_IDENTIFYING_PARTS = ("HOST", "USER", "DEVICE_ID", "BASE")
#: Values that are words, not identifiers: scrubbing them out of free text would mangle it (every
#: "root" or "mqtt" in a log line) without hiding anything. A setting holding one of these is
#: still redacted by name in the settings themselves.
_COMMON_WORDS = frozenset(
    {
        "admin",
        "brilliant",
        "brilliant_kiosk",
        "default",
        "disabled",
        "enabled",
        "false",
        "homeassistant",
        "kiosk",
        "localhost",
        "mqtt",
        "none",
        "null",
        "root",
        "true",
        "user",
    }
)
_MIN_IDENTIFIER_LENGTH = 4
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6_RE = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:.])")


def _ipv6_or_same(match: re.Match[str]) -> str:
    """Redact an IPv6 address; a time of day such as 12:30:45 has neither ``::`` nor 7 colons."""
    text = match.group(0)
    return REDACTED if "::" in text or text.count(":") >= 7 else text


def _is_identifier(value: object) -> bool:
    """Whether a value names this panel, its network or its login, and may be scrubbed from text.

    Short values and common words are left alone: removing "root" or "mqtt" from every sentence
    would corrupt the report without protecting anything.
    """
    text = str(value) if value else ""
    return len(text) >= _MIN_IDENTIFIER_LENGTH and text.lower() not in _COMMON_WORDS


def _scrub(value: Any, secrets: tuple[str, ...]) -> Any:
    """Replace every known identifier and every IP address in all strings of ``value``.

    An identifier is replaced as a whole token only: ``10.0.0.5`` inside ``10.0.0.55`` or a host
    name inside a longer word is not an occurrence of it.
    """
    if isinstance(value, str):
        for secret in secrets:
            value = re.sub(rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])", REDACTED, value)
        return _IPV6_RE.sub(_ipv6_or_same, _IPV4_RE.sub(REDACTED, value))
    if isinstance(value, Mapping):
        return {key: _scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item, secrets) for item in value]
    return value


def panel_identifiers(entry: ConfigEntry[Any], env: Mapping[str, str]) -> tuple[str, ...]:
    """What identifies one panel: its address, node, host key, device ids and logins.

    Longest first, so a value is never half-replaced by a shorter one it contains.
    """
    identifying = [
        entry.data.get(CONF_HOST),
        entry.data.get(CONF_NODE),
        entry.data.get(CONF_HOST_KEY),
        entry.options.get(CONF_MQTT_DEVICE_ID),
        entry.unique_id,
        *(v for k, v in env.items() if any(p in k.upper() for p in _IDENTIFYING_PARTS)),
    ]
    return tuple(sorted({str(v) for v in identifying if _is_identifier(v)}, key=len, reverse=True))


def scrub(value: Any, entry: ConfigEntry[Any], env: Mapping[str, str]) -> Any:
    """:func:`_scrub` ``value`` of everything that identifies the panel of ``entry``."""
    return _scrub(value, panel_identifiers(entry, env))


#: ``password=...``, ``token: ...``, ``Authorization: ...`` (the value goes, the name stays).
_ASSIGNED_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)(?:bearer\s+)?[^\s\"',;]+"
)
#: ``Bearer abc...`` anywhere.
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[\w.~+/=-]{8,}")
#: A JSON web token, which is what a Home Assistant long-lived access token is.
_JWT_RE = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*")
#: Any long unbroken run of key-like characters (hex or base64 keys, hashes, opaque tokens).
_OPAQUE_RE = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{32,}(?![\w-])")


def scrub_logs(text: str, entry: ConfigEntry[Any], env: Mapping[str, str]) -> str:
    """Journal lines safe to hand out: the diagnostics scrub plus anything that looks like a secret.

    Identifiers and IP addresses go exactly as in the diagnostics; so do values assigned to a
    secret-sounding name, bearer tokens, JSON web tokens and any 32+ character run of key-like
    characters. This is a best effort on free text, not a guarantee.
    """
    result: str = scrub(text, entry, env)
    result = _ASSIGNED_SECRET_RE.sub(lambda m: f"{m[1]}{m[2]}{REDACTED}", result)
    for pattern in (_BEARER_RE, _JWT_RE, _OPAQUE_RE):
        result = pattern.sub(REDACTED, result)
    return result
