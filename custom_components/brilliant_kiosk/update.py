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
from pybrilliantkiosk import Release

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


def _norm(version: str | None) -> str | None:
    return version.removeprefix("v") if version else None


class KioskUpdate(BrilliantKioskEntity, UpdateEntity):
    """The kiosk software on the panel."""

    _attr_release_url = None

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
        releases = self.coordinator.releases.data or ()
        if self._channel == CHANNEL_NIGHTLY:
            return releases[0] if releases else None
        return next((r for r in releases if not r.prerelease), None)

    @property
    def installed_version(self) -> str | None:
        """The VERSION on the panel."""
        return self.coordinator.data.health.version

    @property
    def latest_version(self) -> str | None:
        """The newest release of the channel (the installed one when none is newer)."""
        latest = self._latest
        if latest is None or _norm(latest.tag) == _norm(self.installed_version):
            return self.installed_version
        return latest.tag

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
        """Download and install a release."""
        releases = self.coordinator.releases.data or ()
        target = next((r for r in releases if r.tag == version), None) if version else self._latest
        if target is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="release_not_found",
                translation_placeholders={"tag": version or self._channel},
            )
        await self.coordinator.async_install(target)
