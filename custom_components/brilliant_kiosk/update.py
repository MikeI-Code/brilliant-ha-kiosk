"""Update entity: kiosk releases from GitHub, installed with ha-kiosk-setup."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pybrilliantkiosk import Release, is_newer, parse_version

from . import BrilliantKioskConfigEntry
from .const import CHANNEL_NIGHTLY, CHANNEL_STABLE, CONF_CHANNEL, DOMAIN
from .coordinator import BrilliantKioskCoordinator
from .entity import BrilliantKioskEntity

PARALLEL_UPDATES = 1
RELEASE_URL = "https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/{tag}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrilliantKioskConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the update entity."""
    async_add_entities([KioskUpdate(entry.runtime_data)])


def _version_of(release: Release) -> str | None:
    """The bundle version of a release: its asset name, else a plain ``vX.Y.Z`` tag.

    A nightly's tag (``nightly-YYYYMMDD``) is not its version; the bundle VERSION
    (``v1.4.0-nightly.YYYYMMDD.g<sha>``) comes from the asset name.
    """
    if release.version:
        return release.version
    return release.tag if parse_version(release.tag) else None


class KioskUpdate(BrilliantKioskEntity, UpdateEntity):
    """The kiosk software on the panel."""

    def __init__(self, coordinator: BrilliantKioskCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "kiosk")
        features = UpdateEntityFeature.RELEASE_NOTES
        if coordinator.manage_installs:
            features |= UpdateEntityFeature.INSTALL | UpdateEntityFeature.SPECIFIC_VERSION
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        """Also refresh when the releases list changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.releases.async_add_listener(self.async_write_ha_state)
        )

    @property
    def _channel(self) -> str:
        return str(self.coordinator.config_entry.options.get(CONF_CHANNEL, CHANNEL_STABLE))

    @property
    def _latest(self) -> Release | None:
        """The newest release of the channel, only if it is newer than the installed version.

        Never a downgrade: a nightly is newer than the stable it was built on, so a stable
        channel does not offer an older stable over an installed nightly. Unknown installed or
        release versions are never offered.
        """
        installed = self.installed_version
        candidates = [
            (key, release)
            for release in self.coordinator.releases.data or ()
            if (self._channel == CHANNEL_NIGHTLY or not release.prerelease)
            and (key := parse_version(_version_of(release))) is not None
        ]
        if not candidates:
            return None
        _, best = max(candidates, key=lambda pair: pair[0])
        return best if is_newer(_version_of(best), installed) else None

    @property
    def installed_version(self) -> str | None:
        """The VERSION on the panel."""
        return self.coordinator.data.health.version

    @property
    def latest_version(self) -> str | None:
        """The offered release's version, or the installed one when nothing newer exists."""
        latest = self._latest
        return _version_of(latest) if latest else self.installed_version

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Use the kiosk's own ordering (nightly dates), not HA's generic version compare.

        The generic compare ranks ``...g89ec239`` below ``...gabc1234`` by their commit hashes.
        """
        return is_newer(latest_version, installed_version)

    @property
    def release_url(self) -> str | None:
        """Link to the latest release."""
        latest = self._latest
        return RELEASE_URL.format(tag=latest.tag) if latest else None

    @property
    def release_summary(self) -> str | None:
        """Short notes of the latest release."""
        latest = self._latest
        return latest.body[:255] if latest and latest.body else None

    async def async_release_notes(self) -> str | None:
        """Full notes of the latest release."""
        latest = self._latest
        return latest.body if latest and latest.body else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Download and install a release (never a first install: see ``ensure_installed``)."""
        self.coordinator.ensure_installed()
        releases = self.coordinator.releases.data or ()
        target = (
            next((r for r in releases if version in (r.tag, r.version)), None)
            if version
            else self._latest
        )
        if target is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="release_not_found",
                translation_placeholders={"tag": version or self._channel},
            )
        await self.coordinator.async_install(target)
