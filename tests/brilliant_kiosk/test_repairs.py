"""Fixable repairs, panels without KIOSK_MQTT, divergent kiosk.env copies and the host key text."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from custom_components.brilliant_kiosk.const import (
    CONF_MANAGE_INSTALLS,
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    DOMAIN,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
    provider_of,
)
from custom_components.brilliant_kiosk.repairs import async_create_fix_flow
from homeassistant import config_entries
from homeassistant.components.repairs.issue_handler import RepairsFlowManager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pybrilliantkiosk import CommandFailed, HostKeyInfo, InvalidArgument, SetupBusy
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from .conftest import FINGERPRINT, HOST, make_entry, setup_entry
from .test_config_flow import USER_INPUT, connect, mqtt_info, start, submit_options

# -- F: KIOSK_MQTT absent means built-in ---------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "provider"),
    [
        ({}, PROVIDER_BUILTIN),
        ({"DIM_LEVEL": "4"}, PROVIDER_BUILTIN),
        ({"KIOSK_MQTT": ""}, PROVIDER_BUILTIN),
        ({"KIOSK_MQTT": "1"}, PROVIDER_BUILTIN),
        ({"KIOSK_MQTT": "0"}, PROVIDER_BUILTIN),
        ({"KIOSK_MQTT": "kiosk"}, PROVIDER_BRILLIANT_MQTT),
        ({"KIOSK_MQTT": "KIOSK"}, PROVIDER_BRILLIANT_MQTT),
    ],
)
def test_provider_of(settings: dict[str, str], provider: str) -> None:
    assert provider_of(settings) == provider


async def test_user_flow_without_kiosk_mqtt_is_builtin(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"DIM_LEVEL": "4"}  # what both real panels report
    result = await connect(hass)
    assert result["options"] == {
        CONF_MQTT_PROVIDER: PROVIDER_BUILTIN,
        CONF_MQTT_DEVICE_ID: "",
        CONF_MANAGE_INSTALLS: True,
    }


async def test_mqtt_discovery_of_a_panel_without_kiosk_mqtt_is_builtin(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {}
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=mqtt_info("brilliant_kiosk/brilliant_0123456789ab/kiosk_state"),
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_MQTT_PROVIDER] == PROVIDER_BUILTIN
    assert result["options"][CONF_MANAGE_INSTALLS] is True


def _bare_entry() -> MockConfigEntry:
    """An entry as an older version stored it: no MQTT options at all."""
    template = make_entry()
    return MockConfigEntry(
        domain=DOMAIN, title=template.title, unique_id=template.unique_id, data=template.data
    )


@pytest.mark.parametrize(
    ("settings", "preselected"),
    [
        ({"DIM_LEVEL": "4"}, PROVIDER_BUILTIN),
        ({"KIOSK_MQTT": "kiosk"}, PROVIDER_BRILLIANT_MQTT),
    ],
)
async def test_options_preselect_follows_the_panel_when_no_option_is_stored(
    hass, panel: MagicMock, releases: MagicMock, settings: dict[str, str], preselected: str
) -> None:
    panel.get_settings.return_value = settings
    entry = _bare_entry()
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema if k.default is not vol.UNDEFINED
    }
    assert defaults[CONF_MQTT_PROVIDER] == preselected
    assert defaults[CONF_MANAGE_INSTALLS] is (preselected == PROVIDER_BUILTIN)
    assert entry.runtime_data.mqtt_provider == PROVIDER_BUILTIN  # nothing stored: built-in


async def test_builtin_provider_without_kiosk_mqtt_writes_nothing(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"DIM_LEVEL": "4"}
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, CONF_MQTT_DEVICE_ID: "d"})
    await setup_entry(hass, entry)
    panel.set_env.reset_mock()
    # Back to built-in: the panel has no KIOSK_MQTT (so it is already built-in) and no device id.
    result = await submit_options(
        hass,
        entry,
        {CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, CONF_MQTT_DEVICE_ID: "", CONF_MANAGE_INSTALLS: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    panel.set_env.assert_not_awaited()
    panel.restart.assert_not_awaited()
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"mqtt_provider_not_applied_{entry.entry_id}"
    )


async def test_builtin_without_kiosk_mqtt_still_removes_a_stale_device_id(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"MQTT_DEVICE_ID": "old"}
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, CONF_MQTT_DEVICE_ID: "old"})
    await setup_entry(hass, entry)
    await entry.runtime_data.async_apply_mqtt_provider(PROVIDER_BUILTIN, "")
    panel.set_env.assert_awaited_with({}, ("MQTT_DEVICE_ID",))


async def test_mqtt_device_of_a_panel_without_kiosk_mqtt_is_the_builtin_one(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {}
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    assert coordinator.mqtt_provider == PROVIDER_BUILTIN
    assert coordinator.mqtt_device_ids({}) == [entry.data["node"]]


# -- host key text -------------------------------------------------------------------------------


async def test_host_key_step_shows_the_key_type(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.fetch_host_key.return_value = HostKeyInfo(FINGERPRINT, "ssh-ed25519")
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["step_id"] == "host_key"
    assert result["description_placeholders"] == {
        "host": HOST,
        "fingerprint": FINGERPRINT,
        "key_type": "ED25519",
    }
    # The pin that gets stored is still the bare SHA256 fingerprint.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["data"]["host_key"] == FINGERPRINT
    assert panel.flow_panel_cls.call_args.kwargs["host_key"] == FINGERPRINT


# -- G: the MQTT provider repair re-applies --------------------------------------------------------


async def start_fix(hass: HomeAssistant, issue_id: str):
    assert await async_setup_component(hass, "repairs", {})
    manager = RepairsFlowManager(hass)
    return manager, await manager.async_init(DOMAIN, data={"issue_id": issue_id})


async def failed_provider_entry(hass: HomeAssistant, panel: MagicMock) -> tuple:
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"mqtt_provider_not_applied_{entry.entry_id}"
    panel.set_env.side_effect = SetupBusy("job running")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
            "mqtt_device_id": "d",
        },
    )
    await hass.async_block_till_done()
    return entry, issue_id


async def test_provider_issue_is_fixable_and_the_fix_reapplies_without_saving_options(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry, issue_id = await failed_provider_entry(hass, panel)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.data == {"entry_id": entry.entry_id}

    panel.set_env.side_effect = None
    panel.set_env.reset_mock()
    manager, result = await start_fix(hass, issue_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["host"] == HOST
    assert result["description_placeholders"]["provider"] == PROVIDER_BRILLIANT_MQTT
    panel.set_env.assert_not_awaited()  # nothing is written before the user submits

    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    panel.set_env.assert_awaited_with({"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "d"}, ())
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_provider_fix_that_fails_again_stays_on_the_form(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    _, issue_id = await failed_provider_entry(hass, panel)  # set_env still raises SetupBusy
    manager, result = await start_fix(hass, issue_id)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "apply_failed"}
    shown = result["description_placeholders"]["error"]
    assert "job running" in shown  # the real, translated reason ...
    assert "see the log" not in shown  # ... not a pointer to the log
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    panel.set_env.side_effect = None
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_fix_flow_aborts_when_the_panel_is_not_loaded(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry, issue_id = await failed_provider_entry(hass, panel)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    _, result = await start_fix(hass, issue_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_fix_flow_for_an_unknown_issue_is_refused(hass: HomeAssistant) -> None:
    with pytest.raises(ValueError, match="no fix flow"):
        await async_create_fix_flow(hass, "something_else_1", {"entry_id": "x"})
    with pytest.raises(ValueError, match="no fix flow"):
        await async_create_fix_flow(hass, "something_else_1", None)


# -- C: drift between the running and the saved copy ------------------------------------------------


async def test_drift_raises_a_fixable_repair_and_the_fix_reconciles(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.env_drift.return_value = ("HA_ZOOM", "WEATHER_FX")
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"env_drift_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.translation_placeholders == {
        "host": HOST,
        "keys": "HA_ZOOM, WEATHER_FX",
        "unsafe": "",
    }
    assert issue.translation_key == "env_drift"
    assert entry.runtime_data.data.drift == ("HA_ZOOM", "WEATHER_FX")

    manager, result = await start_fix(hass, issue_id)
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["keys"] == "HA_ZOOM, WEATHER_FX"
    panel.reconcile_env.assert_not_awaited()

    panel.env_drift.return_value = ()  # what the panel reports after the write
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    panel.reconcile_env.assert_awaited_once_with()
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_drift_fix_failure_shows_the_error(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.env_drift.return_value = ("HA_ZOOM",)
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.reconcile_env.side_effect = InvalidArgument("cannot reconcile unsafe values for HA_URL")
    manager, result = await start_fix(hass, f"env_drift_{entry.entry_id}")
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "reconcile_failed"}
    # The reason is the library's error, translated, not "see the log".
    assert "HA_URL" in result["description_placeholders"]["error"]


async def test_unsafe_running_values_raise_a_drift_issue_that_says_so(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """A hand-set unsafe running value: never rewritten, named in the issue and in the fix."""
    panel.env_drift.return_value = ("HA_URL", "DAY_LEVEL")
    panel.unsafe = ("HA_URL",)
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"env_drift_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "env_drift_unsafe"
    assert issue.is_fixable is True
    assert issue.translation_placeholders["unsafe"] == "HA_URL"
    assert issue.translation_placeholders["keys"] == "HA_URL, DAY_LEVEL"
    assert entry.runtime_data.data.unsafe == ("HA_URL",)

    panel.reconcile_env.side_effect = InvalidArgument(
        "the running kiosk.env has values that cannot be written safely (HA_URL)"
    )
    manager, result = await start_fix(hass, issue_id)
    assert result["description_placeholders"]["unsafe"] == "HA_URL"
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "reconcile_failed"}
    assert "cannot be written safely (HA_URL)" in result["description_placeholders"]["error"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    # Fixed on the panel meanwhile: the next poll turns it into the plain drift issue, then none.
    panel.unsafe = ()
    await entry.runtime_data.async_refresh()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "env_drift"
    panel.env_drift.return_value = ()
    await entry.runtime_data.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_no_drift_means_no_issue_and_a_broken_read_is_not_drift(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"env_drift_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    panel.poll_error = CommandFailed("read", 1, "garbled")
    await entry.runtime_data.async_refresh()
    assert not entry.runtime_data.last_update_success  # a broken read is a failed update ...
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None  # ... not drift


async def test_drift_is_in_the_diagnostics(
    hass, panel: MagicMock, releases: MagicMock, hass_client
) -> None:
    panel.env_drift.return_value = ("HA_ZOOM",)
    entry = make_entry()
    await setup_entry(hass, entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["env_drift"] == ["HA_ZOOM"]


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_fixable_issues_have_fix_flow_texts(name: str) -> None:
    import json
    from pathlib import Path

    import custom_components.brilliant_kiosk as package

    texts = json.loads((Path(package.__file__).parent / name).read_text(encoding="utf-8"))
    for key, error in (
        ("mqtt_provider_not_applied", "apply_failed"),
        ("env_drift", "reconcile_failed"),
        ("env_drift_unsafe", "reconcile_failed"),
    ):
        fix_flow = texts["issues"][key]["fix_flow"]
        assert "description" in fix_flow["step"]["confirm"]
        assert error in fix_flow["error"]
        assert "not_loaded" in fix_flow["abort"]
        assert "save the options again" not in json.dumps(texts["issues"][key]).lower()
