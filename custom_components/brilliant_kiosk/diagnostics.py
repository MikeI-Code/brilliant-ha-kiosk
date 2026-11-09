"""Diagnostics for Brilliant Kiosk."""

from __future__ import annotations

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
from .redact import REDACTED, scrub

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


def _redact_settings(settings: Mapping[str, str]) -> dict[str, str]:
    """Settings without anything that names the panel, its network or a login."""
    return {
        key: REDACTED
        if value and (is_secret(key) or any(part in key.upper() for part in _SENSITIVE_PARTS))
        else value
        for key, value in settings.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BrilliantKioskConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a panel, with its address, ids and logins removed."""
    coordinator = entry.runtime_data
    data = coordinator.data
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
    # What identifies the panel is also removed from free text (the setup tool's messages, ...).
    scrubbed: dict[str, Any] = scrub(report, entry, data.env)
    return scrubbed
