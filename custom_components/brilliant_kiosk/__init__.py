"""The Brilliant Kiosk integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
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
    panel_title,
)
from .coordinator import BrilliantKioskCoordinator, async_delete_issues
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

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
    await _async_name_after_room(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _is_default_title(entry: ConfigEntry, host: str) -> bool:
    """Whether the title is one this integration made itself (the user has not renamed it)."""
    return entry.title in (f"Brilliant panel {host}", panel_title(None, host))


async def _async_name_after_room(
    hass: HomeAssistant, entry: BrilliantKioskConfigEntry, coordinator: BrilliantKioskCoordinator
) -> None:
    """Name a panel that still has a default title after its room, once, at setup.

    Brilliant's own name for the room is read from the stock message bus (one Python run on the
    panel, so not on every poll). The title and the device name change; entity ids do not. A
    name the user chose (entry title or device name) is never touched, and a panel whose room
    cannot be read keeps its title: this must not stop the setup.
    """
    host = str(entry.data.get(CONF_HOST, ""))
    if not _is_default_title(entry, host):
        return
    try:
        room = (await coordinator.panel.identity()).room
    except Exception as err:  # the room is cosmetic: never block the setup
        _LOGGER.debug("Cannot read the room of %s: %s", host, err)
        return
    if room is None:
        return
    title = panel_title(room, host)
    hass.config_entries.async_update_entry(entry, title=title)
    devices = dr.async_get(hass)
    device = devices.async_get_device(identifiers={(DOMAIN, str(entry.unique_id))})
    if device is not None and device.name_by_user is None:
        devices.async_update_device(device.id, name=title)


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
