"""The Brilliant Kiosk integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from pybrilliantkiosk import parse_version

from .const import (
    CHANNEL_NIGHTLY,
    CHANNEL_STABLE,
    CONF_CHANNEL,
    CONF_MANAGE_INSTALLS,
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    DOMAIN,
    ISSUE_ENV_DRIFT,
    PROVIDER_BUILTIN,
)
from .coordinator import BrilliantKioskCoordinator, async_delete_issues
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
    _default_channel(hass, entry, coordinator.data.health.version)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _default_channel(
    hass: HomeAssistant, entry: BrilliantKioskConfigEntry, installed: str | None
) -> None:
    """On first setup, follow the channel of what is installed: nightly for a nightly build.

    Without this a panel running a nightly shows the release channel "stable" and the update
    entity would offer nothing newer than the last stable release. Done before the update
    listener exists, so it does not look like an option change; later choices are never touched.
    """
    if CONF_CHANNEL in entry.options:
        return
    version = parse_version(installed)
    channel = CHANNEL_NIGHTLY if version is not None and version.nightly else CHANNEL_STABLE
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CHANNEL: channel})


async def _async_options_updated(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> None:
    """Apply option changes: the MQTT provider on the panel, then reload when needed.

    The options flow only validates and stores; the SSH write to the panel happens here. A
    release channel change needs neither the panel write nor a reload.

    The provider the coordinator uses is the one the panel runs. When the panel does not accept
    the new one, the old provider stays effective and there is no reload for it: the (fixable)
    repair issue offers to apply it again, and every poll keeps comparing the panel with the
    options. A reload for a changed ``manage_installs`` still happens.
    """
    coordinator = entry.runtime_data
    provider = entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN)
    device_id = entry.options.get(CONF_MQTT_DEVICE_ID, "")
    manage = entry.options.get(CONF_MANAGE_INSTALLS, True)
    provider_changed = provider != coordinator.mqtt_provider
    applied = True
    if provider_changed or device_id != coordinator.mqtt_device_id:
        # A failure is logged and raises a repair issue that offers to apply it again.
        try:
            await coordinator.async_apply_mqtt_provider(provider, device_id)
        except HomeAssistantError:
            applied = False
    if (provider_changed and applied) or manage != coordinator.manage_installs:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> bool:
    """Migrate a config entry to the current version.

    Version 2: the unique id is always the node of the panel's Brilliant device id. A version 1
    entry made while that id could not be read carries the host key fingerprint instead; it needs
    the panel to be converted, which the coordinator does on the first successful poll (this
    runs before any connection, and must not wait for one). Entries of a newer version are not
    understood by this one.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> bool:
    """Unload a panel.

    The drift issue is dropped with it: its fix needs a loaded entry, and the first poll after a
    reload raises it again if the copies still differ. The MQTT provider issue stays, because the
    options listener raises it and then reloads the entry.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_delete_issues(hass, entry.entry_id, only=(ISSUE_ENV_DRIFT,))
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> None:
    """Delete everything of this panel's that outlives the entry: its repair issues."""
    async_delete_issues(hass, entry.entry_id)
