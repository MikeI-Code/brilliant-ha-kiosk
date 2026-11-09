"""Sensors for Brilliant Kiosk."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import BrilliantKioskConfigEntry
from .coordinator import BrilliantKioskCoordinator, KioskData
from .entity import BrilliantKioskEntity

PARALLEL_UPDATES = 0

SETUP_STATES = ["active", "disabled", "failed", "in_progress", "not_installed", "unmanaged"]


@dataclass(frozen=True, kw_only=True)
class BrilliantSensorDescription(SensorEntityDescription):
    """Sensor with a value function over the coordinator data."""

    value_fn: Callable[[KioskData], StateType]


def _setup_state(data: KioskData) -> str | None:
    return data.setup.state if data.setup.state in SETUP_STATES else None


SENSORS: tuple[BrilliantSensorDescription, ...] = (
    BrilliantSensorDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.health.version,
    ),
    BrilliantSensorDescription(
        key="setup_state",
        translation_key="setup_state",
        device_class=SensorDeviceClass.ENUM,
        options=SETUP_STATES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_setup_state,
    ),
    BrilliantSensorDescription(
        key="load",
        translation_key="load",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.health.load_1,
    ),
    BrilliantSensorDescription(
        key="memory_used",
        translation_key="memory_used",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.health.mem_used_percent,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrilliantKioskConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(BrilliantSensor(coordinator, description) for description in SENSORS)


class BrilliantSensor(BrilliantKioskEntity, SensorEntity):
    """A panel sensor."""

    entity_description: BrilliantSensorDescription

    def __init__(
        self, coordinator: BrilliantKioskCoordinator, description: BrilliantSensorDescription
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """The sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
