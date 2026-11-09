"""Diagnostics for Brilliant Kiosk."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pybrilliantkiosk import is_secret

from . import BrilliantKioskConfigEntry
from .const import (
    CONF_HOST_KEY,
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PASSWORD,
    CONF_NODE,
    CONF_PRIVATE_KEY,
    CONF_TOKEN,
)

REDACTED = "**REDACTED**"

#: Keys of the config entry, options and ``ha-kiosk-setup status`` that identify the panel or
#: carry a secret. ``message`` is the free text of the last setup run, which names hosts and paths.
TO_REDACT = {
    CONF_PASSWORD,
    CONF_PRIVATE_KEY,
    CONF_HOST_KEY,
    CONF_HOST,
    CONF_USERNAME,
    CONF_NODE,
    CONF_TOKEN,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_DEVICE_ID,
    "mqtt_base",
    "message",
    "error",
}

#: A kiosk.env setting is redacted when its name contains one of these: addresses, logins,
#: allow-lists of clients, entity and device names, directories.
_SENSITIVE_PARTS = (
    "HOST",
    "USER",
    "URL",
    "BIND",
    "ALLOW",
    "BASE",
    "DEVICE_ID",
    "DEVICE",
    "NAME",
    "ENTITY",
    "SPEAKERS",
    "LIGHTS",
    "CAMERAS",
    "THERMOSTATS",
    "SENSOR",
    "GROUP",
    "FOLDER",
    "DIRS",
    "DIR",
    "ACCESS",
    "PASSWORD",
    "TOKEN",
    "SECRET",
)
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


def _redact_settings(settings: Mapping[str, str]) -> dict[str, str]:
    """Settings without anything that names the panel, its network or a login."""
    return {
        key: REDACTED
        if value and (is_secret(key) or any(part in key.upper() for part in _SENSITIVE_PARTS))
        else value
        for key, value in settings.items()
    }


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


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BrilliantKioskConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a panel, with its address, ids and logins removed."""
    coordinator = entry.runtime_data
    data = coordinator.data
    # What identifies the panel is also removed from free text (the setup tool's messages, ...).
    identifying = [
        entry.data.get(CONF_HOST),
        entry.data.get(CONF_NODE),
        entry.data.get(CONF_HOST_KEY),
        entry.options.get(CONF_MQTT_DEVICE_ID),
        entry.unique_id,
        *(v for k, v in data.env.items() if any(p in k.upper() for p in _IDENTIFYING_PARTS)),
    ]
    secrets = tuple(
        sorted({str(v) for v in identifying if _is_identifier(v)}, key=len, reverse=True)
    )
    report = {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "health": asdict(data.health),
        "settings": _redact_settings(data.env),
        "setup": async_redact_data(asdict(data.setup), TO_REDACT),
        "env_drift": list(data.drift),
        "env_unsafe": list(data.unsafe),
        "releases": [r.tag for r in coordinator.releases.data or ()],
        "last_update_success": coordinator.last_update_success,
    }
    scrubbed: dict[str, Any] = _scrub(report, secrets)
    return scrubbed
