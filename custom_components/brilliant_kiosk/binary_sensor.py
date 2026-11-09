"""Binary sensors: one per kiosk systemd unit."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pybrilliantkiosk import UNITS

from . import BrilliantKioskConfigEntry
from .coordinator import BrilliantKioskCoordinator
from .entity import BrilliantKioskEntity

PARALLEL_UPDATES = 0

#: Units most panels run; the optional ones (voice, intercom, capture, swap) start disabled.
DEFAULT_ENABLED = {"ha-kiosk.service", "ha-kiosk-display.service", "ha-kiosk-bridge.service"}


def _key(unit: str) -> str:
    return unit.removesuffix(".service").replace("-", "_")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrilliantKioskConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one binary sensor per unit."""
    coordinator = entry.runtime_data
    async_add_entities(UnitBinarySensor(coordinator, unit) for unit in UNITS)


class UnitBinarySensor(BrilliantKioskEntity, BinarySensorEntity):
    """Whether a kiosk unit is running."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BrilliantKioskCoordinator, unit: str) -> None:
        """Initialise."""
        super().__init__(coordinator, _key(unit))
        self._unit = unit
        self._attr_entity_registry_enabled_default = unit in DEFAULT_ENABLED

    @property
    def is_on(self) -> bool:
        """The unit is active."""
        return self.coordinator.data.health.unit_active(self._unit)
