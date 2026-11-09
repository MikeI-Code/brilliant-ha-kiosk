"""The Brilliant Kiosk integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_MANAGE_INSTALLS,
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    DOMAIN,
    PROVIDER_BUILTIN,
)
from .coordinator import BrilliantKioskCoordinator
from .services import async_setup_services

type BrilliantKioskConfigEntry = ConfigEntry[BrilliantKioskCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.UPDATE,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> bool:
    """Set up one panel."""
    coordinator = BrilliantKioskCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    if coordinator.releases.data is None:
        await coordinator.releases.async_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> None:
    """Apply option changes: the MQTT provider on the panel, then reload when needed.

    The options flow only validates and stores; the SSH write to the panel happens here. A
    release channel change needs neither the panel write nor a reload.
    """
    coordinator = entry.runtime_data
    provider = entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN)
    device_id = entry.options.get(CONF_MQTT_DEVICE_ID, "")
    manage = entry.options.get(CONF_MANAGE_INSTALLS, True)
    if provider != coordinator.mqtt_provider or device_id != coordinator.mqtt_device_id:
        await coordinator.async_apply_mqtt_provider(provider, device_id)
    if provider != coordinator.mqtt_provider or manage != coordinator.manage_installs:
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        coordinator.mqtt_device_id = device_id


async def async_unload_entry(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> bool:
    """Unload a panel."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
