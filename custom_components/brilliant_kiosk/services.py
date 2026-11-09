"""Actions of the Brilliant Kiosk integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from pybrilliantkiosk import UNITS

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DELETE,
    ATTR_LINES,
    ATTR_PERIPHERAL,
    ATTR_RESTART,
    ATTR_TAG,
    ATTR_UNIT,
    ATTR_VALUES,
    DOMAIN,
    MQTT_PROVIDER_KEYS,
    SERVICE_BUS_GET,
    SERVICE_BUS_SET,
    SERVICE_GET_ENV,
    SERVICE_INSTALL_VERSION,
    SERVICE_LOGS,
    SERVICE_SET_ENV,
)
from .coordinator import BrilliantKioskCoordinator
from .errors import panel_errors
from .redact import scrub_logs

_VALUE = vol.Any(str, int, float, bool)
_VALUES = vol.Schema({cv.string: _VALUE})


def _text(value: Any) -> str:
    """Render a service value the way kiosk.env wants it (booleans as 0/1)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _coordinator(hass: HomeAssistant, entry_id: str) -> BrilliantKioskCoordinator:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"title": entry.title},
        )
    coordinator: BrilliantKioskCoordinator = entry.runtime_data
    return coordinator


async def _set_env(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    changes = {key: _text(value) for key, value in call.data.get(ATTR_VALUES, {}).items()}
    delete: list[str] = call.data.get(ATTR_DELETE, [])
    # The provider (and the brilliant-mqtt device id) is an option of the panel: writing it here
    # would leave the options and the panel disagreeing, and could switch MQTT off.
    if any(key.upper() in MQTT_PROVIDER_KEYS for key in (*changes, *delete)):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="mqtt_set_in_options"
        )
    if not changes and not delete:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nothing_to_change")
    with panel_errors():
        changed = await coordinator.panel.set_env(changes, delete, restart=call.data[ATTR_RESTART])
    if changed:
        await coordinator.async_request_refresh()


async def _get_env(call: ServiceCall) -> dict[str, Any]:
    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    with panel_errors():
        return {"env": await coordinator.panel.get_settings()}


async def _bus_get(call: ServiceCall) -> dict[str, Any]:
    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    with panel_errors():
        variables = await coordinator.panel.bus_get(call.data[ATTR_PERIPHERAL])
    return {
        "variables": [
            {"name": v.name, "value": v.value, "read_only": v.read_only} for v in variables
        ]
    }


async def _bus_set(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    with panel_errors():
        await coordinator.panel.bus_set(call.data[ATTR_PERIPHERAL], call.data[ATTR_VALUES])


async def _logs(call: ServiceCall) -> dict[str, Any]:
    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    with panel_errors():
        text = await coordinator.panel.logs(call.data[ATTR_UNIT], call.data[ATTR_LINES])
    # Journal lines are free text: they can name the panel's address and logins, or show a token.
    return {"logs": scrub_logs(text, coordinator.config_entry, coordinator.data.env)}


async def _install_version(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    if not coordinator.manage_installs:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="installs_not_managed"
        )
    coordinator.ensure_installed()
    tag = call.data[ATTR_TAG]
    # The GitHub tag (v1.4.0, nightly-20261108) or the bundle version, as the update entity does.
    release = next((r for r in coordinator.releases.data or () if tag in (r.tag, r.version)), None)
    if release is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="release_not_found",
            translation_placeholders={"tag": tag},
        )
    await coordinator.async_install(release)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions (once, from async_setup), for administrators only.

    They run commands as root on a panel (settings, logs, installs, message-bus writes), so a
    call made by a user who is not an administrator is refused. Automations, scripts and the
    like (no user) are not affected.
    """
    entry: dict[Any, Any] = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_ENV,
        _set_env,
        vol.Schema(
            {
                **entry,
                vol.Optional(ATTR_VALUES, default={}): _VALUES,
                vol.Optional(ATTR_DELETE, default=[]): [cv.string],
                vol.Optional(ATTR_RESTART, default=False): cv.boolean,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_GET_ENV,
        _get_env,
        vol.Schema(entry),
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_BUS_GET,
        _bus_get,
        vol.Schema({**entry, vol.Required(ATTR_PERIPHERAL): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_BUS_SET,
        _bus_set,
        vol.Schema(
            {**entry, vol.Required(ATTR_PERIPHERAL): cv.string, vol.Required(ATTR_VALUES): _VALUES}
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_LOGS,
        _logs,
        vol.Schema(
            {
                **entry,
                vol.Required(ATTR_UNIT): vol.In(UNITS),
                vol.Optional(ATTR_LINES, default=100): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=5000)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_INSTALL_VERSION,
        _install_version,
        vol.Schema({**entry, vol.Required(ATTR_TAG): cv.string}),
    )
