"""Buttons: restart units, reboot, and (when installs are managed) reapply and rollback."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pybrilliantkiosk import Panel

from . import BrilliantKioskConfigEntry
from .coordinator import BrilliantKioskCoordinator
from .entity import BrilliantKioskEntity
from .errors import panel_errors

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class BrilliantButtonDescription(ButtonEntityDescription):
    """Button with the panel call it performs."""

    press_fn: Callable[[Panel], Awaitable[object]]
    #: Needs install management (ha-kiosk-setup lifecycle commands).
    managed: bool = False


BUTTONS: tuple[BrilliantButtonDescription, ...] = (
    BrilliantButtonDescription(
        key="restart_kiosk",
        translation_key="restart_kiosk",
        device_class=ButtonDeviceClass.RESTART,
        press_fn=lambda p: p.restart("ha-kiosk"),
    ),
    BrilliantButtonDescription(
        key="restart_voice",
        translation_key="restart_voice",
        device_class=ButtonDeviceClass.RESTART,
        entity_registry_enabled_default=False,
        press_fn=lambda p: p.restart("ha-kiosk-voice"),
    ),
    BrilliantButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda p: p.reboot(),
    ),
    BrilliantButtonDescription(
        key="reapply",
        translation_key="reapply",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        managed=True,
        press_fn=lambda p: p.setup_command("reapply"),
    ),
    BrilliantButtonDescription(
        key="rollback",
        translation_key="rollback",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        managed=True,
        press_fn=lambda p: p.setup_command("rollback"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BrilliantKioskConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        BrilliantButton(coordinator, description)
        for description in BUTTONS
        if coordinator.manage_installs or not description.managed
    )


class BrilliantButton(BrilliantKioskEntity, ButtonEntity):
    """A panel action."""

    entity_description: BrilliantButtonDescription

    def __init__(
        self, coordinator: BrilliantKioskCoordinator, description: BrilliantButtonDescription
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Perform the action."""
        with panel_errors():
            await self.entity_description.press_fn(self.coordinator.panel)
        await self.coordinator.async_request_refresh()
