"""Data coordinators for Brilliant Kiosk."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.hass_dict import HassKey
from pybrilliantkiosk import (
    AuthFailed,
    HostKeyMismatch,
    Panel,
    PanelError,
    PanelHealth,
    Release,
    ReleaseError,
    Releases,
    SetupStatus,
)

from .const import (
    CHANNEL_NIGHTLY,
    CHANNEL_STABLE,
    CONF_CHANNEL,
    CONF_HOST_KEY,
    CONF_MANAGE_INSTALLS,
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    CONF_NODE,
    CONF_PRIVATE_KEY,
    DEFAULT_PORT,
    DOMAIN,
    ISSUE_ENV_DRIFT,
    ISSUE_ENV_DRIFT_UNSAFE,
    ISSUE_MQTT_PROVIDER,
    MQTT_DOMAIN,
    PANEL_MQTT_FULL,
    PANEL_MQTT_KIOSK,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
    RELEASES_INTERVAL,
    SCAN_INTERVAL,
    TROUBLESHOOTING_URL,
    is_fingerprint,
    node_of,
    provider_of,
)
from .errors import panel_errors
from .install import async_fetch_bundle

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
    #: Drifted keys whose running value cannot be written safely (never rewritten by us).
    unsafe: tuple[str, ...] = ()


class ReleasesCoordinator(DataUpdateCoordinator[tuple[Release, ...]]):
    """GitHub releases of the kiosk, fetched with Home Assistant's shared session.

    One instance serves every panel (see :func:`async_acquire_releases`): GitHub's anonymous API
    allows 60 requests per hour per address, so one poll per panel would not scale. A refresh is
    two requests: the newest 30 releases (mostly nightlies) and the release GitHub marks *Latest*,
    so the newest stable release is found however many nightlies were published since.
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
            releases = list(await self.client.list())
            latest = await self.client.latest_stable()
        except ReleaseError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="release_error",
                translation_placeholders={"error": str(err)},
            ) from err
        if latest is not None and all(release.tag != latest.tag for release in releases):
            releases.append(latest)
        return tuple(releases)


@dataclass(slots=True)
class SharedReleases:
    """The one :class:`ReleasesCoordinator` and the config entries that use it."""

    coordinator: ReleasesCoordinator
    users: set[str] = field(default_factory=set)


#: ``hass.data`` key of the releases coordinator shared by all panels (GitHub allows 60
#: requests per hour per address).
DATA_RELEASES: HassKey[SharedReleases] = HassKey(DOMAIN)


def async_acquire_releases(hass: HomeAssistant, entry_id: str) -> ReleasesCoordinator:
    """The one shared :class:`ReleasesCoordinator` (created on first use) for ``entry_id``."""
    shared = hass.data.get(DATA_RELEASES)
    if shared is None:
        shared = hass.data[DATA_RELEASES] = SharedReleases(ReleasesCoordinator(hass))
    shared.users.add(entry_id)
    return shared.coordinator


async def async_release_releases(hass: HomeAssistant, entry_id: str) -> None:
    """Drop ``entry_id``'s use of the shared coordinator; stop it with the last user."""
    shared = hass.data.get(DATA_RELEASES)
    if shared is None:
        return
    shared.users.discard(entry_id)
    if not shared.users:
        del hass.data[DATA_RELEASES]
        await shared.coordinator.async_shutdown()


@callback
def async_delete_issues(hass: HomeAssistant, entry_id: str, *, only: tuple[str, ...] = ()) -> None:
    """Delete the repair issues of one panel (all of them, or those named in ``only``).

    Issue ids are ``<key>_<config entry id>``. Without this, an issue outlives the entry it is
    about and offers a fix that can never run.
    """
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain != DOMAIN or not issue_id.endswith(f"_{entry_id}"):
            continue
        if only and issue_id.removesuffix(f"_{entry_id}") not in only:
            continue
        ir.async_delete_issue(hass, DOMAIN, issue_id)


class BrilliantKioskCoordinator(DataUpdateCoordinator[KioskData]):
    """Polls panel health, settings, drift and setup status every 60 s with ONE SSH command."""

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
            password=entry.data.get(CONF_PASSWORD),
            private_key=entry.data.get(CONF_PRIVATE_KEY),
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
            host_key=entry.data[CONF_HOST_KEY],
        )
        self.releases = async_acquire_releases(hass, entry.entry_id)
        self.mqtt_provider: str = entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN)
        self.mqtt_device_id: str = entry.options.get(CONF_MQTT_DEVICE_ID, "")
        self.manage_installs: bool = entry.options.get(CONF_MANAGE_INSTALLS, True)
        #: A version 1 entry may carry a host key fingerprint as unique id; see
        #: :meth:`_async_migrate_identity`.
        self._identity_pending = is_fingerprint(entry.unique_id)

    async def _async_update_data(self) -> KioskData:
        try:
            snapshot = await self.panel.poll()
        except AuthFailed as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except HostKeyMismatch as err:
            # The reauth flow shows the new key and pins it only after the user confirms it.
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="host_key_mismatch",
                translation_placeholders={"error": str(err)},
            ) from err
        except PanelError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        data = KioskData(
            health=snapshot.health,
            env=snapshot.settings,
            setup=snapshot.setup,
            drift=snapshot.drift,
            unsafe=snapshot.unsafe,
        )
        if self._identity_pending:
            await self._async_migrate_identity(first=self.data is None)
        self._sync_drift_issue(data.drift, data.unsafe)
        self._sync_mqtt_provider(data.env)
        self._link_mqtt_device(data.env)
        self._sync_device_version(data.health.version)
        return data

    # -- identity -----------------------------------------------------------------------

    async def _async_migrate_identity(self, *, first: bool) -> None:
        """Replace a host key fingerprint used as unique id by the node of the device id.

        A fingerprint changes when the panel is re-flashed, so it cannot identify the panel. The
        id is read from the stock firmware (:meth:`Panel.device_id`); when that fails the entry
        keeps its old id and the next poll tries again. The device and the entities follow the
        new id (their identifiers and unique ids are built from it); a reload picks it up
        unless this is the very first refresh, before any entity exists. If another entry
        already has that node (the panel was added twice), nothing is changed and the user is
        told once.
        """
        entry = self.config_entry
        old = str(entry.unique_id)
        try:
            node = node_of(await self.panel.device_id())
        except PanelError as err:
            _LOGGER.debug("Cannot read the Brilliant device id of %s yet: %s", self.panel.host, err)
            return
        self._identity_pending = False
        other = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, node)
        if other is not None and other.entry_id != entry.entry_id:
            _LOGGER.warning(
                "The panel %s is configured twice (%s and %s); remove one of the entries",
                self.panel.host,
                entry.title,
                other.title,
            )
            return
        devices = dr.async_get(self.hass)
        device = devices.async_get_device(identifiers={(DOMAIN, old)})
        if device is not None and devices.async_get_device(identifiers={(DOMAIN, node)}) is None:
            devices.async_update_device(device.id, new_identifiers={(DOMAIN, node)})

        @callback
        def _entity_id(entity: er.RegistryEntry) -> dict[str, str] | None:
            prefix = f"{old}_"
            if entity.unique_id.startswith(prefix):
                return {"new_unique_id": f"{node}_{entity.unique_id.removeprefix(prefix)}"}
            return None

        await er.async_migrate_entries(self.hass, entry.entry_id, _entity_id)
        self.hass.config_entries.async_update_entry(
            entry, unique_id=node, data={**entry.data, CONF_NODE: node}
        )
        _LOGGER.info(
            "The unique id of %s is now %s (was a host key fingerprint)", entry.title, node
        )
        if not first:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

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

    @callback
    def _sync_device_version(self, version: str | None) -> None:
        """Keep the device's software version current (it is set once, when the device is made)."""
        registry = dr.async_get(self.hass)
        own = registry.async_get_device(identifiers={(DOMAIN, str(self.config_entry.unique_id))})
        if own and version and own.sw_version != version:
            registry.async_update_device(own.id, sw_version=version)

    # -- repairs ------------------------------------------------------------------------

    @callback
    def _sync_drift_issue(self, drift: tuple[str, ...], unsafe: tuple[str, ...]) -> None:
        """Repair issue while the running and the saved copy of kiosk.env differ.

        ``ha-kiosk-setup`` rebuilds the running copy from the saved one on install and reapply, so
        settings applied only to the running copy would be dropped then. When a running value
        cannot be written safely the fix would always be refused (kiosk.env is sourced by root
        shell scripts), so that issue is not fixable from Home Assistant: it says which keys to
        fix on the panel and links to the troubleshooting section.
        """
        issue_id = f"{ISSUE_ENV_DRIFT}_{self.config_entry.entry_id}"
        if drift:
            placeholders = {
                "host": self.config_entry.data[CONF_HOST],
                "keys": ", ".join(drift),
                "unsafe": ", ".join(unsafe),
            }
            if unsafe:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    learn_more_url=TROUBLESHOOTING_URL,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_ENV_DRIFT_UNSAFE,
                    translation_placeholders=placeholders,
                )
            else:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    data={"entry_id": self.config_entry.entry_id},
                    is_fixable=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_ENV_DRIFT,
                    translation_placeholders=placeholders,
                )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def _sync_mqtt_provider(self, env: Mapping[str, str]) -> None:
        """Follow the provider the panel runs, and compare it with the one chosen in the options.

        The effective provider (:attr:`mqtt_provider`, which decides where the panel's MQTT device
        is looked for) is what the panel's own ``KIOSK_MQTT`` says, so an option the panel did not
        accept never points the integration at a device that does not exist. When the panel
        disagrees with the options (a failed apply, or a hand edit on the panel) the repair issue
        that offers to apply the option again is raised; it is deleted once they agree. Entries
        without the option (made before it existed) follow the panel and have nothing to compare.
        """
        actual = provider_of(env)
        self.mqtt_provider = actual
        options = self.config_entry.options
        wanted = options.get(CONF_MQTT_PROVIDER)
        if wanted is None:
            return
        wanted_id = str(options.get(CONF_MQTT_DEVICE_ID, ""))
        if actual != wanted:
            self._raise_mqtt_issue(
                f"the panel runs the {actual} provider, the options say {wanted}"
            )
        elif wanted == PROVIDER_BRILLIANT_MQTT and env.get("MQTT_DEVICE_ID", "") != wanted_id:
            self._raise_mqtt_issue(
                f"the panel's MQTT_DEVICE_ID is {env.get('MQTT_DEVICE_ID', '')!r}, "
                f"the options say {wanted_id!r}"
            )
        else:
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_MQTT_PROVIDER}_{self.config_entry.entry_id}"
            )

    @callback
    def _raise_mqtt_issue(self, error: str) -> None:
        """The (fixable) repair issue: the chosen MQTT provider is not what the panel runs."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{ISSUE_MQTT_PROVIDER}_{self.config_entry.entry_id}",
            data={"entry_id": self.config_entry.entry_id},
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MQTT_PROVIDER,
            translation_placeholders={"host": self.config_entry.data[CONF_HOST], "error": error},
        )

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

    @property
    def nightly_channel(self) -> bool:
        """The user chose the nightly channel (the only way a nightly may be installed)."""
        return str(self.config_entry.options.get(CONF_CHANNEL, CHANNEL_STABLE)) == CHANNEL_NIGHTLY

    def ensure_installed(self) -> None:
        """Refuse an update or version install on a panel that has no kiosk.

        Such an install would be a first install, which needs the Home Assistant URL, a token and
        the MQTT settings (``env`` and ``token``): without them it fails slowly and the panel is
        put back on Brilliant's own screen. First installs happen only in the config flow's
        install step.
        """
        if not self.data.setup.installed:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_installed",
                translation_placeholders={
                    "error": "an update or a version install needs an installed kiosk."
                },
            )

    async def async_install(self, release: Release) -> None:
        """Download a release, verify it (see :mod:`.install`) and install it on the panel.

        Only for a panel that has the kiosk (:meth:`ensure_installed`).
        """
        self.ensure_installed()
        with panel_errors():
            bundle, sha256, size = await async_fetch_bundle(
                self.releases.client, release, nightly_allowed=self.nightly_channel
            )
            await self.panel.install(bundle, sha256, size=size)
        await self.async_request_refresh()

    async def async_apply_mqtt_provider(self, provider: str, device_id: str) -> None:
        """Set KIOSK_MQTT (and MQTT_DEVICE_ID) on the panel when it differs; never switch off.

        Called from the options update listener, not from the options flow, and by the repair
        flow that re-applies it. A failure raises a (fixable) repair issue instead of blocking the
        options dialog, and is re-raised as a translated :class:`HomeAssistantError` so the
        caller can show the reason.

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
            with panel_errors():
                if (changes or delete) and await self.panel.set_env(changes, delete):
                    await self.panel.restart("ha-kiosk-bridge")
        except HomeAssistantError as err:
            _LOGGER.warning("Could not apply the MQTT provider to %s: %s", self.panel.host, err)
            self._raise_mqtt_issue(str(err))
            raise
        self.mqtt_provider = provider
        self.mqtt_device_id = device_id
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        # The next poll compares the panel with the options: let it see what was just written.
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Stop timers, close the SSH connection and drop the shared releases coordinator."""
        await super().async_shutdown()
        await async_release_releases(self.hass, self.config_entry.entry_id)
        await self.panel.close()
