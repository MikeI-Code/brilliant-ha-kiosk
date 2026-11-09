"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NODE, DOMAIN, MQTT_DOMAIN
from .coordinator import BrilliantKioskCoordinator


def mqtt_identifiers(coordinator: BrilliantKioskCoordinator) -> list[str]:
    """Candidate identifiers of the panel's MQTT device (brilliant-mqtt's id first)."""
    env = coordinator.data.env if coordinator.data else {}
    found = [env.get("MQTT_DEVICE_ID"), coordinator.config_entry.data.get(CONF_NODE)]
    return [str(ident) for ident in found if ident]


def build_device_info(coordinator: BrilliantKioskCoordinator) -> DeviceInfo:
    """Join the panel's MQTT device when it exists (both MQTT providers), else own a device."""
    entry = coordinator.config_entry
    registry = dr.async_get(coordinator.hass)
    for ident in mqtt_identifiers(coordinator):
        if registry.async_get_device(identifiers={(MQTT_DOMAIN, ident)}):
            return DeviceInfo(identifiers={(MQTT_DOMAIN, ident)})
    health = coordinator.data.health if coordinator.data else None
    return DeviceInfo(
        identifiers={(DOMAIN, str(entry.unique_id))},
        name=entry.title,
        manufacturer="Brilliant",
        model="Brilliant Control (HA kiosk)",
        sw_version=health.version if health else None,
    )


class BrilliantKioskEntity(CoordinatorEntity[BrilliantKioskCoordinator]):
    """Base class: translated names, one device per panel."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BrilliantKioskCoordinator, key: str) -> None:
        """Initialise."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = build_device_info(coordinator)
