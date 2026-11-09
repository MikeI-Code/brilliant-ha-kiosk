"""Fix flows for the repair issues that can be fixed from Home Assistant."""

from __future__ import annotations

from abc import ABC, abstractmethod

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from . import BrilliantKioskConfigEntry
from .const import (
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    ISSUE_ENV_DRIFT,
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

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Start with the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
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
            data_schema=vol.Schema({}),
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


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a fixable issue of this integration."""
    entry_id = str((data or {}).get("entry_id", ""))
    if issue_id.startswith(f"{ISSUE_MQTT_PROVIDER}_"):
        return ReapplyMqttProviderFlow(entry_id)
    if issue_id.startswith(f"{ISSUE_ENV_DRIFT}_"):
        return ReconcileEnvFlow(entry_id)
    raise ValueError(f"no fix flow for issue {issue_id}")
