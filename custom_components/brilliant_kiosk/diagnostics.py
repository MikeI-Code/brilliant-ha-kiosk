"""Diagnostics for Brilliant Kiosk."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pybrilliantkiosk import is_secret

from . import BrilliantKioskConfigEntry
from .const import CONF_HOST_KEY, CONF_MQTT_DEVICE_ID, CONF_MQTT_PASSWORD, CONF_NODE, CONF_TOKEN

TO_REDACT = {
    CONF_PASSWORD,
    CONF_HOST_KEY,
    CONF_HOST,
    CONF_NODE,
    CONF_TOKEN,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_DEVICE_ID,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BrilliantKioskConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a panel."""
    coordinator = entry.runtime_data
    data = coordinator.data
    env_keys = {key for key in data.env if is_secret(key)} | {"MQTT_DEVICE_ID", "HA_URL"}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "health": asdict(data.health),
        "settings": async_redact_data(dict(data.env), env_keys),
        "setup": asdict(data.setup),
        "releases": [r.tag for r in coordinator.releases.data or ()],
        "last_update_success": coordinator.last_update_success,
    }
