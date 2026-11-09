"""Data coordinators for Brilliant Kiosk."""

from __future__ import annotations

import logging
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
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
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    CONF_NODE,
    DATA_RELEASES,
    DOMAIN,
    ISSUE_ENV_DRIFT,
    ISSUE_MQTT_PROVIDER,
    MQTT_DOMAIN,
    PANEL_MQTT_FULL,
    PANEL_MQTT_KIOSK,
    PROVIDER_BRILLIANT_MQTT,
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
    #: Keys that differ between the running and the saved copy of kiosk.env.
    drift: tuple[str, ...] = ()


class ReleasesCoordinator(DataUpdateCoordinator[tuple[Release, ...]]):
    """GitHub releases of the kiosk, fetched with Home Assistant's shared session.

    One instance serves every panel (see :func:`async_acquire_releases`): GitHub's anonymous API
    allows 60 requests per hour per address, so one poll per panel would not scale.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
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


def async_acquire_releases(hass: HomeAssistant, entry_id: str) -> ReleasesCoordinator:
    """The one shared :class:`ReleasesCoordinator` (created on first use) for ``entry_id``."""
    shared: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if DATA_RELEASES not in shared:
        shared[DATA_RELEASES] = {"coordinator": ReleasesCoordinator(hass), "users": set()}
    holder: dict[str, Any] = shared[DATA_RELEASES]
    holder["users"].add(entry_id)
    coordinator: ReleasesCoordinator = holder["coordinator"]
    return coordinator


async def async_release_releases(hass: HomeAssistant, entry_id: str) -> None:
    """Drop ``entry_id``'s use of the shared coordinator; stop it with the last user."""
    shared: dict[str, Any] = hass.data.get(DOMAIN, {})
    holder: dict[str, Any] | None = shared.get(DATA_RELEASES)
    if holder is None:
        return
    holder["users"].discard(entry_id)
    if not holder["users"]:
        del shared[DATA_RELEASES]
        coordinator: ReleasesCoordinator = holder["coordinator"]
        await coordinator.async_shutdown()


async def _resolve(hass: HomeAssistant, host: str) -> set[str]:
    """IP addresses of ``host`` (a literal resolves to itself); empty if it does not resolve."""
    try:
        infos = await hass.loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return set()
    return {str(info[4][0]) for info in infos}


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
        self.releases = async_acquire_releases(hass, entry.entry_id)
        self.mqtt_provider: str = entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN)
        self.mqtt_device_id: str = entry.options.get(CONF_MQTT_DEVICE_ID, "")
        self.manage_installs: bool = entry.options.get(CONF_MANAGE_INSTALLS, True)

    async def _async_update_data(self) -> KioskData:
        host_key_issue = f"host_key_changed_{self.config_entry.entry_id}"
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
            try:
                drift = await self.panel.env_drift()
            except CommandFailed:
                drift = ()
        except AuthFailed as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except PanelError as err:
            if isinstance(err, HostKeyMismatch):
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    host_key_issue,
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="host_key_changed",
                    translation_placeholders={
                        "host": self.config_entry.data[CONF_HOST],
                        "fingerprint": err.fingerprint or "unknown",
                    },
                )
            key = "host_key_mismatch" if isinstance(err, HostKeyMismatch) else "cannot_connect"
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key=key,
                translation_placeholders={"error": str(err)},
            ) from err
        ir.async_delete_issue(self.hass, DOMAIN, host_key_issue)
        await self._sync_voice_issue(env)
        self._sync_drift_issue(drift)
        self._link_mqtt_device(env)
        return KioskData(health=health, env=env, setup=setup, drift=drift)

    # -- device registry ----------------------------------------------------------------

    def mqtt_device_ids(self, env: Mapping[str, str] | None = None) -> list[str]:
        """Identifiers the panel's MQTT device may have, most likely first.

        Built-in kiosk MQTT registers ``brilliant_<first 12 chars of the Brilliant device id>``
        (the config entry's node); brilliant-mqtt registers its ``MQTT_DEVICE_ID``.
        """
        if env is None:
            env = self.data.env if self.data else {}
        if self.mqtt_provider == PROVIDER_BRILLIANT_MQTT:
            found = [env.get("MQTT_DEVICE_ID"), self.mqtt_device_id]
        else:
            found = [self.config_entry.data.get(CONF_NODE)]
        return [str(ident) for ident in found if ident]

    @callback
    def find_mqtt_device(self, env: Mapping[str, str] | None = None) -> dr.DeviceEntry | None:
        """The panel's device created by the MQTT integration, if it exists (yet)."""
        registry = dr.async_get(self.hass)
        for ident in self.mqtt_device_ids(env):
            if device := registry.async_get_device(identifiers={(MQTT_DOMAIN, ident)}):
                return device
        return None

    @callback
    def _link_mqtt_device(self, env: Mapping[str, str]) -> None:
        """Point our device at the MQTT device (``via_device``) once MQTT discovery made it.

        Runs on every update, so a device that shows up after the integration was set up is
        linked without a reload.
        """
        registry = dr.async_get(self.hass)
        own = registry.async_get_device(identifiers={(DOMAIN, str(self.config_entry.unique_id))})
        mqtt_device = self.find_mqtt_device(env)
        if own and mqtt_device and own.via_device_id != mqtt_device.id:
            registry.async_update_device(own.id, via_device_id=mqtt_device.id)

    # -- repairs ------------------------------------------------------------------------

    async def _voice_configured(self, host: str) -> bool:
        """Whether a Wyoming entry points at ``host`` (by name or by resolved address)."""
        known = {
            str(entry.data[CONF_HOST])
            for entry in self.hass.config_entries.async_entries(WYOMING_DOMAIN)
            if entry.data.get(CONF_HOST)
        }
        if host in known:
            return True
        addresses = await _resolve(self.hass, host)
        for other in known:
            if other in addresses or addresses & await _resolve(self.hass, other):
                return True
        return False

    async def _sync_voice_issue(self, env: Mapping[str, str]) -> None:
        """Repair issue when the panel runs the voice satellite but Wyoming has no entry for it."""
        issue_id = f"voice_not_in_wyoming_{self.config_entry.entry_id}"
        host = self.config_entry.data[CONF_HOST]
        if env.get("KIOSK_VOICE") == "1" and not await self._voice_configured(host):
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

    @callback
    def _sync_drift_issue(self, drift: tuple[str, ...]) -> None:
        """Repair issue while the running and the saved copy of kiosk.env differ.

        ``ha-kiosk-setup`` rebuilds the running copy from the saved one on install and reapply, so
        settings applied only to the running copy would be dropped then.
        """
        issue_id = f"{ISSUE_ENV_DRIFT}_{self.config_entry.entry_id}"
        if drift:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                data={"entry_id": self.config_entry.entry_id},
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_ENV_DRIFT,
                translation_placeholders={
                    "host": self.config_entry.data[CONF_HOST],
                    "keys": ", ".join(drift),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    # -- actions ------------------------------------------------------------------------

    async def async_reconcile_env(self) -> tuple[str, ...]:
        """Make the running and the saved copy of kiosk.env identical; returns the keys fixed."""
        with panel_errors():
            keys = await self.panel.reconcile_env()
        await self.async_request_refresh()
        return keys

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

    async def async_apply_mqtt_provider(self, provider: str, device_id: str) -> bool:
        """Set KIOSK_MQTT (and MQTT_DEVICE_ID) on the panel when it differs; never switch off.

        Called from the options update listener, not from the options flow, and by the repair
        flow that re-applies it. A failure raises a (fixable) repair issue instead of blocking the
        options dialog; returns whether the panel now has the provider.

        A panel without ``KIOSK_MQTT`` already runs the built-in mode (setup's default), so
        nothing is written for it when the built-in provider is wanted.
        """
        issue_id = f"{ISSUE_MQTT_PROVIDER}_{self.config_entry.entry_id}"
        env = self.data.env if self.data else {}
        if provider == PROVIDER_BRILLIANT_MQTT:
            changes = {"KIOSK_MQTT": PANEL_MQTT_KIOSK, "MQTT_DEVICE_ID": device_id}
            delete: tuple[str, ...] = ()
        else:
            changes = {"KIOSK_MQTT": PANEL_MQTT_FULL} if "KIOSK_MQTT" in env else {}
            delete = ("MQTT_DEVICE_ID",) if env.get("MQTT_DEVICE_ID") else ()
        try:
            if (changes or delete) and await self.panel.set_env(changes, delete):
                await self.panel.restart("ha-kiosk-bridge")
        except PanelError as err:
            _LOGGER.warning("Could not apply the MQTT provider to %s: %s", self.panel.host, err)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                data={"entry_id": self.config_entry.entry_id},
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_MQTT_PROVIDER,
                translation_placeholders={
                    "host": self.config_entry.data[CONF_HOST],
                    "error": str(err),
                },
            )
            return False
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        return True

    async def async_shutdown(self) -> None:
        """Stop timers, close the SSH connection and drop the shared releases coordinator."""
        await super().async_shutdown()
        await async_release_releases(self.hass, self.config_entry.entry_id)
        await self.panel.close()
