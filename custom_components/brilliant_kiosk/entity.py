"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MQTT_DOMAIN
from .coordinator import BrilliantKioskCoordinator


def build_device_info(coordinator: BrilliantKioskCoordinator) -> DeviceInfo:
    """The panel's own device, linked to its MQTT device (``via_device``) when that exists.

    The identifier is always ``(DOMAIN, unique_id)``: this integration never claims the MQTT
    integration's identifiers. When the MQTT device appears later, the coordinator links it on
    its next update.
    """
    entry = coordinator.config_entry
    health = coordinator.data.health if coordinator.data else None
    info = DeviceInfo(
        identifiers={(DOMAIN, str(entry.unique_id))},
        name=entry.title,
        manufacturer="Brilliant",
        model="Brilliant Control (HA kiosk)",
        sw_version=health.version if health else None,
    )
    mqtt_device = coordinator.find_mqtt_device()
    if mqtt_device is not None:
        mqtt_id = next(ident for domain, ident in mqtt_device.identifiers if domain == MQTT_DOMAIN)
        info["via_device"] = (MQTT_DOMAIN, mqtt_id)
    return info


class BrilliantKioskEntity(CoordinatorEntity[BrilliantKioskCoordinator]):
    """Base class: translated names, one device per panel."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BrilliantKioskCoordinator, key: str) -> None:
        """Initialise."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = build_device_info(coordinator)
