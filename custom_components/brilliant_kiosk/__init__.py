"""The Brilliant Kiosk integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_MANAGE_INSTALLS, CONF_MQTT_PROVIDER, DOMAIN, PROVIDER_BUILTIN
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
    await coordinator.releases.async_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> None:
    """Reload when install management or the MQTT provider changes (not the release channel)."""
    coordinator = entry.runtime_data
    if (
        entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN) != coordinator.mqtt_provider
        or entry.options.get(CONF_MANAGE_INSTALLS, True) != coordinator.manage_installs
    ):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> bool:
    """Unload a panel."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
