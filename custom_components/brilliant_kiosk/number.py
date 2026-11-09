"""Numbers backed by kiosk.env (brightness levels not covered by the MQTT entities)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrilliantKioskConfigEntry
from .coordinator import BrilliantKioskCoordinator
from .entity import BrilliantKioskEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class BrilliantNumberDescription(NumberEntityDescription):
    """Number tied to one kiosk.env key."""

    env_key: str


NUMBERS: tuple[BrilliantNumberDescription, ...] = tuple(
    BrilliantNumberDescription(
        key=key,
        translation_key=key,
        env_key=env_key,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=15,
        native_step=1,
        mode=NumberMode.SLIDER,
    )
    for key, env_key in (("dim_level", "DIM_LEVEL"), ("min_level", "MIN_LEVEL"))
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrilliantKioskConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the numbers."""
    coordinator = entry.runtime_data
    async_add_entities(BrilliantNumber(coordinator, description) for description in NUMBERS)


class BrilliantNumber(BrilliantKioskEntity, NumberEntity):
    """A kiosk.env level (0-15)."""

    entity_description: BrilliantNumberDescription

    def __init__(
        self, coordinator: BrilliantKioskCoordinator, description: BrilliantNumberDescription
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """The configured level, if set and numeric."""
        raw = self.coordinator.data.env.get(self.entity_description.env_key)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write the level and restart the display service."""
        await self.coordinator.async_set_settings(
            {self.entity_description.env_key: str(int(value))}
        )
