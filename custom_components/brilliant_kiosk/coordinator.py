"""Data coordinators for Brilliant Kiosk."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pybrilliantkiosk import (
    AuthFailed,
    CommandFailed,
    HostKeyMismatch,
    Panel,
    PanelError,
    PanelHealth,
    Release,
    ReleaseError,
    Releases,
    SetupError,
    SetupStatus,
)

from .const import (
    CONF_HOST_KEY,
    CONF_MANAGE_INSTALLS,
    CONF_MQTT_PROVIDER,
    DOMAIN,
    PROVIDER_BUILTIN,
    RELEASES_INTERVAL,
    SCAN_INTERVAL,
    WYOMING_DOMAIN,
)
from .errors import panel_errors

if TYPE_CHECKING:
    from . import BrilliantKioskConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KioskData:
    """One poll: health, effective settings and the ha-kiosk-setup status."""

    health: PanelHealth
    env: Mapping[str, str]
    setup: SetupStatus


class ReleasesCoordinator(DataUpdateCoordinator[tuple[Release, ...]]):
    """GitHub releases of the kiosk, fetched with Home Assistant's shared session."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} releases",
            update_interval=RELEASES_INTERVAL,
        )
        self.client = Releases(async_get_clientsession(hass))

    async def _async_update_data(self) -> tuple[Release, ...]:
        try:
            return await self.client.list()
        except ReleaseError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="release_error",
                translation_placeholders={"error": str(err)},
            ) from err


class BrilliantKioskCoordinator(DataUpdateCoordinator[KioskData]):
    """Polls panel health, settings and setup status every 60 s."""

    config_entry: BrilliantKioskConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BrilliantKioskConfigEntry) -> None:
        """Initialise."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}",
            update_interval=SCAN_INTERVAL,
        )
        self.panel = Panel(
            entry.data[CONF_HOST],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            host_key=entry.data[CONF_HOST_KEY],
        )
        self.releases = ReleasesCoordinator(hass, entry)
        self.mqtt_provider: str = entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN)
        self.manage_installs: bool = entry.options.get(CONF_MANAGE_INSTALLS, True)

    async def _async_update_data(self) -> KioskData:
        try:
            health = await self.panel.health()
            try:
                env = await self.panel.get_settings()
            except CommandFailed:
                env = {}
            try:
                setup = await self.panel.setup_status()
            except SetupError:
                setup = SetupStatus(state="unknown")
        except AuthFailed as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except PanelError as err:
            key = "host_key_mismatch" if isinstance(err, HostKeyMismatch) else "cannot_connect"
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key=key,
                translation_placeholders={"error": str(err)},
            ) from err
        self._sync_voice_issue(env)
        return KioskData(health=health, env=env, setup=setup)

    # -- repairs ------------------------------------------------------------------------

    def _sync_voice_issue(self, env: Mapping[str, str]) -> None:
        """Repair issue when the panel runs the voice satellite but Wyoming has no entry for it."""
        issue_id = f"voice_not_in_wyoming_{self.config_entry.entry_id}"
        host = self.config_entry.data[CONF_HOST]
        known = {
            entry.data.get(CONF_HOST)
            for entry in self.hass.config_entries.async_entries(WYOMING_DOMAIN)
        }
        if env.get("KIOSK_VOICE") == "1" and host not in known:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="voice_not_in_wyoming",
                translation_placeholders={"host": host},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    # -- actions ------------------------------------------------------------------------

    async def async_set_settings(self, changes: Mapping[str, str]) -> None:
        """Merge settings into kiosk.env, restart the display service and refresh."""
        with panel_errors():
            changed = await self.panel.set_env(changes)
            if changed:
                await self.panel.restart("ha-kiosk-display")
        await self.async_request_refresh()

    async def async_install(self, release: Release) -> None:
        """Download a release and install it with ha-kiosk-setup."""
        with panel_errors():
            bundle, sha256 = await self.releases.client.download(release)
            await self.panel.install(bundle, sha256)
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Stop timers and close the SSH connection."""
        await super().async_shutdown()
        await self.releases.async_shutdown()
        await self.panel.close()
