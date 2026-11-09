"""Release channel select (stored in the config entry options)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrilliantKioskConfigEntry
from .const import CHANNEL_STABLE, CHANNELS, CONF_CHANNEL
from .coordinator import BrilliantKioskCoordinator
from .entity import BrilliantKioskEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrilliantKioskConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the channel select."""
    async_add_entities([ChannelSelect(entry.runtime_data)])


class ChannelSelect(BrilliantKioskEntity, SelectEntity):
    """Stable or nightly releases for the update entity."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BrilliantKioskCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "channel")
        self._attr_options = list(CHANNELS)

    @property
    def current_option(self) -> str:
        """The selected channel."""
        return str(self.coordinator.config_entry.options.get(CONF_CHANNEL, CHANNEL_STABLE))

    async def async_select_option(self, option: str) -> None:
        """Store the channel and re-evaluate the update entity."""
        entry = self.coordinator.config_entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_CHANNEL: option}
        )
        self.coordinator.async_update_listeners()
