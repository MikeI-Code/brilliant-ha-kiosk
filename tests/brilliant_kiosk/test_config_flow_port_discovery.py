"""The SSH port, MQTT discovery of running kiosks only, and the install task's life."""

from __future__ import annotations

import asyncio
import gc
import logging
from unittest.mock import MagicMock

import pytest
from custom_components.brilliant_kiosk.config_flow import BrilliantKioskConfigFlow
from custom_components.brilliant_kiosk.const import CONF_NODE, DOMAIN
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from pybrilliantkiosk import SetupError, SetupStatus

from .conftest import HOST, NODE, make_entry, setup_entry
from .test_config_flow import (
    INSTALL_INPUT,
    USER_INPUT,
    connect,
    install,
    start,
    submit,
    to_install_step,
)
from .test_config_flow_trust import NEW_INFO, NEW_KEY

# -- the SSH port -------------------------------------------------------------------------------


async def test_the_user_form_asks_for_the_port_with_22_as_default(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await start(hass)
    ports = [k for k in result["data_schema"].schema if str(k) == CONF_PORT]
    assert len(ports) == 1
    assert ports[0].default() == 22


async def test_a_custom_port_is_used_everywhere_and_stored(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await connect(hass, {**USER_INPUT, CONF_PORT: 2222})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == 2222
    panel.fetch_host_key.assert_awaited_once_with(HOST, port=2222)  # the host key scan
    assert panel.flow_panel_cls.call_args.kwargs["port"] == 2222  # the login of the flow
    entry = result["result"]
    await hass.async_block_till_done()
    assert panel.coordinator_panel_cls.call_args.kwargs["port"] == 2222  # the coordinator's
    assert entry.data[CONF_PORT] == 2222


async def test_the_default_port_is_stored_too(hass, panel: MagicMock, releases: MagicMock) -> None:
    result = await connect(hass)
    assert result["data"][CONF_PORT] == 22
    assert panel.flow_panel_cls.call_args.kwargs["port"] == 22


@pytest.mark.parametrize("bad", [0, 65536, -1, "ssh"])
async def test_a_port_out_of_range_is_refused_by_the_form(
    hass, panel: MagicMock, releases: MagicMock, bad: object
) -> None:
    result = await start(hass)
    with pytest.raises(data_entry_flow.InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_PORT: bad}
        )
    panel.fetch_host_key.assert_not_awaited()


async def test_an_entry_without_a_port_connects_on_22(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """Entries made before the port existed have no CONF_PORT."""
    entry = make_entry()
    assert CONF_PORT not in entry.data
    await setup_entry(hass, entry)
    assert panel.coordinator_panel_cls.call_args.kwargs["port"] == 22


async def test_the_first_install_connects_on_the_chosen_port(
    hass, bare_panel_on_port: MagicMock, releases: MagicMock
) -> None:
    result = await connect(hass, {**USER_INPUT, CONF_PORT: 2200})
    assert result["step_id"] == "install"
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # one Panel for the login probe, one for the install: both on the chosen port
    ports = [call.kwargs["port"] for call in bare_panel_on_port.flow_panel_cls.call_args_list]
    assert ports == [2200, 2200]


@pytest.fixture
def bare_panel_on_port(
    panel: MagicMock, releases: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> MagicMock:
    panel.setup_status.return_value = SetupStatus(state="not_installed")
    monkeypatch.setattr(
        "custom_components.brilliant_kiosk.config_flow.kiosk_env_from_bundle",
        lambda bundle, overrides: "".join(f'{k}="{v}"\n' for k, v in overrides.items()),
    )
    return panel


async def test_reauth_scans_and_logs_in_on_the_stored_port(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PORT: 2222})
    await setup_entry(hass, entry)
    panel.flow_panel_cls.reset_mock()
    panel.fetch_host_key.reset_mock()
    panel.fetch_host_key.return_value = NEW_INFO
    panel.connect.return_value = NEW_KEY

    result = await entry.start_reauth_flow(hass)
    panel.fetch_host_key.assert_awaited_once_with(HOST, port=2222)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"trust_new_key": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "newpw"}
    )
    assert result["reason"] == "reauth_successful"
    assert panel.flow_panel_cls.call_args.kwargs["port"] == 2222
    assert entry.data[CONF_PORT] == 2222


async def test_reconfigure_can_change_the_port_with_the_address(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.fetch_host_key.reset_mock()
    panel.flow_panel_cls.reset_mock()
    result = await entry.start_reconfigure_flow(hass)
    port = next(k for k in result["data_schema"].schema if str(k) == CONF_PORT)
    assert port.default() == 22  # the stored one is preselected
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9", CONF_PORT: 2022}
    )
    assert result["reason"] == "reconfigure_successful"
    panel.fetch_host_key.assert_awaited_once_with("10.0.0.9", port=2022)
    assert panel.flow_panel_cls.call_args.kwargs["port"] == 2022
    assert entry.data[CONF_HOST] == "10.0.0.9"
    assert entry.data[CONF_PORT] == 2022


async def test_a_known_panel_found_again_updates_host_and_port(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    result = await connect(hass, {**USER_INPUT, CONF_HOST: "10.0.0.7", CONF_PORT: 2022})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.7"
    assert entry.data[CONF_PORT] == 2022


# -- MQTT discovery offers running kiosks only ----------------------------------------------------


def state_info(payload: str | bytes, node: str = NODE) -> MqttServiceInfo:
    return MqttServiceInfo(
        topic=f"brilliant_kiosk/{node}/kiosk_state",
        payload=payload,
        qos=0,
        retain=True,
        subscribed_topic="brilliant_kiosk/+/kiosk_state",
        timestamp=0.0,
    )


async def discover(hass: HomeAssistant, payload: str | bytes, node: str = NODE):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_MQTT}, data=state_info(payload, node)
    )


@pytest.mark.parametrize("payload", ["active", b"active", " active\n", b"active\n"])
async def test_a_running_kiosk_is_offered(
    hass, panel: MagicMock, releases: MagicMock, payload: str | bytes
) -> None:
    result = await discover(hass, payload)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.parametrize(
    "payload", ["not_installed", "disabled", "failed", "", "offline", b"disabled", b"\xff\xfe"]
)
async def test_a_kiosk_that_is_not_running_is_not_offered(
    hass, panel: MagicMock, releases: MagicMock, payload: str | bytes
) -> None:
    """ha-kiosk-setup leaves these retained on the broker: a stale message must not pop up."""
    result = await discover(hass, payload)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "kiosk_not_active"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    panel.fetch_host_key.assert_not_awaited()


async def test_a_stale_state_does_not_hide_a_later_active_one(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    assert (await discover(hass, "not_installed"))["reason"] == "kiosk_not_active"
    result = await discover(hass, "active")  # the kiosk was installed since: a fresh flow
    assert result["type"] is FlowResultType.FORM


async def test_a_configured_panel_is_not_offered_whatever_its_state(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    make_entry().add_to_hass(hass)
    for payload in ("active", "disabled"):
        assert (await discover(hass, payload))["reason"] == "already_configured"


async def test_discovery_aborts_when_the_address_typed_is_another_panel(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await discover(hass, "active")
    panel.device_id.return_value = "ffffffffffffffffffff"  # a different panel answers there
    result = await submit(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_discovery_completes_when_the_address_is_the_discovered_panel(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await discover(hass, "active")
    result = await submit(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == NODE
    assert result["data"][CONF_NODE] == NODE


async def test_a_user_flow_has_no_discovered_node_to_compare(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.device_id.return_value = "ffffffffffffffffffff"
    result = await connect(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "brilliant_ffffffffffff"


# -- the install task is owned by the flow ------------------------------------------------------


def _open_flow(hass: HomeAssistant, flow_id: str) -> BrilliantKioskConfigFlow:
    flow = hass.config_entries.flow._progress[flow_id]
    assert isinstance(flow, BrilliantKioskConfigFlow)
    return flow


async def test_closing_the_flow_cancels_the_install_it_started_and_logs_nothing_odd(
    hass, bare_panel_on_port: MagicMock, releases: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_install(*args: object, **kwargs: object) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    bare_panel_on_port.install.side_effect = hanging_install
    result = await to_install_step(hass)
    flow = _open_flow(hass, result["flow_id"])
    result = await hass.config_entries.flow.async_configure(result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    task = flow._install_task
    assert task is not None
    await asyncio.wait_for(started.wait(), 5)

    with caplog.at_level(logging.DEBUG):
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()
        assert task.cancelled()  # the flow's own reference, not only Home Assistant's progress task
        assert cancelled.is_set()
        assert flow._install_task is None
        del task, flow
        gc.collect()
        await asyncio.sleep(0)
    assert "never retrieved" not in caplog.text
    assert "failed" not in caplog.text
    assert "Task was destroyed" not in caplog.text
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_async_remove_cancels_a_running_task_even_without_a_progress_step(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    flow = BrilliantKioskConfigFlow()
    flow.hass = hass
    flow._data = {CONF_HOST: HOST}
    flow._install_task = hass.async_create_task(asyncio.Event().wait())
    flow._install_task.add_done_callback(flow._log_install_end)
    task = flow._install_task
    flow.async_remove()
    assert flow._install_task is None
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    flow.async_remove()  # idempotent: nothing left to cancel


async def test_an_install_that_fails_after_the_flow_was_closed_is_logged_not_lost(
    hass, panel: MagicMock, releases: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    flow = BrilliantKioskConfigFlow()
    flow.hass = hass
    flow._data = {CONF_HOST: HOST}

    async def failing() -> None:
        raise SetupError("activation failed")

    task = hass.async_create_task(failing())
    task.add_done_callback(flow._log_install_end)
    with caplog.at_level(logging.DEBUG):
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert f"The kiosk install on {HOST} failed: activation failed" in caplog.text
        del task
        gc.collect()
        await asyncio.sleep(0)
    assert "never retrieved" not in caplog.text


async def test_a_failing_install_in_an_open_flow_is_still_shown_on_the_form(
    hass, bare_panel_on_port: MagicMock, releases: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The done callback only reads the error: the flow still gets it and shows it."""
    bare_panel_on_port.install.side_effect = SetupError("activation failed")
    result = await to_install_step(hass)
    with caplog.at_level(logging.WARNING):
        result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "install_failed"}
    assert result["description_placeholders"] == {"error": "activation failed"}
    assert f"The kiosk install on {HOST} failed: activation failed" in caplog.text
