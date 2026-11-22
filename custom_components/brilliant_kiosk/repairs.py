"""Fix flows for the repair issues that can be fixed from Home Assistant."""

from __future__ import annotations

from abc import ABC, abstractmethod

import probatio
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import BrilliantKioskConfigEntry
from .const import (
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    DOMAIN,
    ISSUE_ENV_DRIFT,
    ISSUE_INTEGRATION_UPDATE,
    ISSUE_MQTT_PROVIDER,
    PROVIDER_BUILTIN,
)


class _PanelFixFlow(RepairsFlow, ABC):
    """Common part: find the loaded config entry the issue belongs to, then confirm and fix."""

    #: Key of the form error shown when the fix fails (see strings.json ``fix_flow.error``).
    error_key = "apply_failed"

    def __init__(self, entry_id: str) -> None:
        """Remember which panel the issue is about."""
        self._entry_id = entry_id

    def _entry(self) -> BrilliantKioskConfigEntry | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            return None
        return entry

    @abstractmethod
    def _placeholders(self, entry: BrilliantKioskConfigEntry) -> dict[str, str]:
        """Texts for the confirmation form."""

    @abstractmethod
    async def _fix(self, entry: BrilliantKioskConfigEntry) -> str | None:
        """Do the fix; returns an error text, or None when it worked."""

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Start with the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Explain, and fix on submit; stay on the form (with the error) when it fails."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        placeholders = self._placeholders(entry)
        if user_input is not None:
            error = await self._fix(entry)
            if error is None:
                return self.async_create_entry(data={})
            errors["base"] = self.error_key
            placeholders["error"] = error
        return self.async_show_form(
            step_id="confirm",
            data_schema=probatio.Schema({}),
            description_placeholders=placeholders,
            errors=errors,
        )


class ReapplyMqttProviderFlow(_PanelFixFlow):
    """Re-apply the MQTT provider chosen in the options (saving unchanged options does not)."""

    def _placeholders(self, entry: BrilliantKioskConfigEntry) -> dict[str, str]:
        return {
            "host": str(entry.data[CONF_HOST]),
            "provider": str(entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN)),
            "error": "",
        }

    async def _fix(self, entry: BrilliantKioskConfigEntry) -> str | None:
        try:
            await entry.runtime_data.async_apply_mqtt_provider(
                entry.options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN),
                entry.options.get(CONF_MQTT_DEVICE_ID, ""),
            )
        except HomeAssistantError as err:
            return str(err)  # the translated reason, shown on the form
        return None


class ReconcileEnvFlow(_PanelFixFlow):
    """Write the running settings to both copies of kiosk.env.

    Only offered for a drift without unsafe values (those issues are not fixable, see
    ``BrilliantKioskCoordinator._sync_drift_issue``). Should one appear between the issue and the
    submit, the library's translated error (such a line is never rewritten) is shown on the form.
    """

    error_key = "reconcile_failed"

    def _placeholders(self, entry: BrilliantKioskConfigEntry) -> dict[str, str]:
        data = entry.runtime_data.data
        return {
            "host": str(entry.data[CONF_HOST]),
            "keys": ", ".join(data.drift),
            "error": "",
        }

    async def _fix(self, entry: BrilliantKioskConfigEntry) -> str | None:
        try:
            await entry.runtime_data.async_reconcile_env()
        except HomeAssistantError as err:
            return str(err)
        return None


#: The integration's own entry in the Marketplace / HACS: its default entity id, and its name.
INTEGRATION_UPDATE_ENTITY_ID = f"update.{DOMAIN}"
INTEGRATION_NAME = "Brilliant Kiosk"


def find_integration_update_entity(hass: HomeAssistant) -> str | None:
    """The entity id of the update entity that updates *this integration*, if there is one.

    The Marketplace (and HACS before it) creates an ``update`` entity per installed repository, on
    its own platform. It is not linked to our domain in the registry, so it is recognised by:

    * domain ``update``, **platform not** ``brilliant_kiosk`` (the panels' own update entities,
      which update kiosk software, are never matched), and not disabled; and
    * the entity id ``update.brilliant_kiosk`` (what the Marketplace derives from the repository
      name; tried first), **or** a display name equal to the manifest name "Brilliant Kiosk": the
      entity's own name or original name, or, for an entity named after its device (the usual
      case), the device's name.

    With several matches the exact entity id wins, else the first by entity id.
    """
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    matches: list[str] = []
    for entry in entities.entities.values():
        if entry.domain != "update" or entry.platform == DOMAIN or entry.disabled:
            continue
        names = {entry.name, entry.original_name}
        if entry.device_id and (device := devices.async_get(entry.device_id)) is not None:
            names |= {device.name_by_user, device.name}
        if entry.entity_id == INTEGRATION_UPDATE_ENTITY_ID or INTEGRATION_NAME in names:
            matches.append(entry.entity_id)
    if INTEGRATION_UPDATE_ENTITY_ID in matches:
        return INTEGRATION_UPDATE_ENTITY_ID
    return min(matches, default=None)


class IntegrationUpdateFlow(RepairsFlow):
    """Update this integration through its Marketplace update entity.

    The kiosk release the issue names is not in the pinned table of the installed version, so the
    integration itself has to be updated (and Home Assistant restarted). The flow only offers that
    when the Marketplace actually has an update; otherwise it says so and points at where to look.
    """

    def __init__(self, tag: str) -> None:
        """Remember the release the issue is about."""
        self._tag = tag

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Start with the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show the versions; install the integration update on submit."""
        entity_id = find_integration_update_entity(self.hass)
        state = self.hass.states.get(entity_id) if entity_id else None
        if entity_id is None or state is None or state.state != STATE_ON:
            return self.async_abort(reason="no_update", description_placeholders={"tag": self._tag})
        errors: dict[str, str] = {}
        placeholders = {
            "tag": self._tag,
            "installed": str(state.attributes.get("installed_version")),
            "latest": str(state.attributes.get("latest_version")),
            "error": "",
        }
        if user_input is not None:
            try:
                await self.hass.services.async_call(
                    "update", "install", {ATTR_ENTITY_ID: entity_id}, blocking=True
                )
            except HomeAssistantError as err:
                errors["base"] = "install_failed"
                placeholders["error"] = str(err)
            else:
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=probatio.Schema({}),
            description_placeholders=placeholders,
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a fixable issue of this integration."""
    if issue_id == ISSUE_INTEGRATION_UPDATE:
        return IntegrationUpdateFlow(str((data or {}).get("tag", "")))
    entry_id = str((data or {}).get("entry_id", ""))
    if issue_id.startswith(f"{ISSUE_MQTT_PROVIDER}_"):
        return ReapplyMqttProviderFlow(entry_id)
    if issue_id.startswith(f"{ISSUE_ENV_DRIFT}_"):
        return ReconcileEnvFlow(entry_id)
    raise ValueError(f"no fix flow for issue {issue_id}")
