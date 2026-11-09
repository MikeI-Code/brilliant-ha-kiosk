"""MQTT discovery from the kiosk's retained ``<base>/<node>/discovery`` JSON, and room names."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from custom_components.brilliant_kiosk.const import DOMAIN
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from pybrilliantkiosk import DeviceIdUnavailable

from .conftest import DEVICE_ID, HOST, NODE, make_entry, setup_entry

PASSWORD_ONLY = {CONF_USERNAME: "root", CONF_PASSWORD: "pw"}


def info(payload: object, topic: str = f"brilliant_kiosk/{NODE}/discovery") -> MqttServiceInfo:
    text = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
    return MqttServiceInfo(
        topic=topic,
        payload=text,
        qos=0,
        retain=True,
        subscribed_topic="brilliant_kiosk/+/discovery",
        timestamp=0.0,
    )


def message(**fields: object) -> dict[str, object]:
    return {"state": "active", "host": HOST, "device_id": DEVICE_ID, "room": "Kitchen", **fields}


async def discover(hass: HomeAssistant, payload: object, topic: str | None = None):
    data = info(payload) if topic is None else info(payload, topic)
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_MQTT}, data=data
    )


def suggested(result) -> dict[str, object]:
    return {
        str(k): k.description["suggested_value"]
        for k in result["data_schema"].schema
        if k.description and "suggested_value" in k.description
    }


async def test_the_host_is_prefilled_and_the_room_is_in_the_title(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await discover(hass, message())
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert suggested(result)[CONF_HOST] == HOST
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    assert flow["context"]["title_placeholders"] == {"name": "Kitchen Brilliant panel found"}
    assert flow["context"]["unique_id"] == NODE


async def test_the_entry_is_named_after_the_room_from_the_panel(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.room = "Hallway"
    result = await discover(hass, message(room=None))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, "port": 22, **PASSWORD_ONLY}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hallway Brilliant kiosk"


async def test_without_a_room_the_title_placeholder_is_the_node(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    await discover(hass, message(room=None))
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    assert flow["context"]["title_placeholders"] == {"name": NODE}


async def test_a_panel_already_added_is_not_offered(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    make_entry().add_to_hass(hass)
    result = await discover(hass, message())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("state", ["not_installed", "disabled", "failed", "offline", ""])
async def test_only_an_active_kiosk_is_offered(
    hass, panel: MagicMock, releases: MagicMock, state: str
) -> None:
    result = await discover(hass, message(state=state, host=None))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "kiosk_not_active"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


@pytest.mark.parametrize(
    "payload",
    [
        "active",
        "not json",
        b"\xff\xfe",
        "[1, 2]",
        "null",
        {"host": HOST, "device_id": DEVICE_ID},
        {"state": 1, "device_id": DEVICE_ID},
        {"state": "active"},
        {"state": "active", "device_id": 5},
        {"state": "active", "device_id": "xyz"},
        {"state": "active", "device_id": "0123456789a"},  # 11 characters
    ],
)
async def test_a_bad_discovery_message_is_refused(
    hass, panel: MagicMock, releases: MagicMock, payload: object
) -> None:
    result = await discover(hass, payload)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unknown"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.168.4.20", "192.168.4.20"),
        ("panel-1.local", "panel-1.local"),
        ("999.1.1.1", None),
        ("1.2.3", None),
        ("-bad-.local", None),
        ("a b", None),
        ("", None),
        (42, None),
        (None, None),
    ],
)
async def test_the_host_is_validated(
    hass, panel: MagicMock, releases: MagicMock, host: object, expected: str | None
) -> None:
    result = await discover(hass, message(host=host))
    assert result["type"] is FlowResultType.FORM
    assert suggested(result).get(CONF_HOST) == expected


async def test_the_room_is_cleaned(hass, panel: MagicMock, releases: MagicMock) -> None:
    await discover(hass, message(room="Ki\x07tchen " + "x" * 100))
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    name = flow["context"]["title_placeholders"]["name"]
    assert name == f"Kitchen {'x' * 56} Brilliant panel found"
    other = message(device_id="f" * 20, room=" \x07 ")
    await discover(hass, other)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    names = {f["context"]["title_placeholders"]["name"] for f in flows}
    assert "brilliant_ffffffffffff" in names


async def test_the_old_trigger_still_works_without_a_prefill(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await discover(hass, "active", f"brilliant_kiosk/{NODE}/kiosk_state")
    assert result["type"] is FlowResultType.FORM
    assert CONF_HOST not in suggested(result)


async def test_the_json_replaces_a_flow_from_the_old_trigger(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    old = await discover(hass, "active", f"brilliant_kiosk/{NODE}/kiosk_state")
    new = await discover(hass, message())
    assert new["type"] is FlowResultType.FORM
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["flow_id"] for f in flows] == [new["flow_id"]]
    assert old["flow_id"] != new["flow_id"]


async def test_the_old_trigger_after_the_json_is_ignored(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    new = await discover(hass, message())
    old = await discover(hass, "active", f"brilliant_kiosk/{NODE}/kiosk_state")
    assert old["type"] is FlowResultType.ABORT
    assert old["reason"] == "already_in_progress"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["flow_id"] for f in flows] == [new["flow_id"]]


# -- entries named after the room ------------------------------------------------------------


async def test_an_old_default_title_becomes_the_room(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.room = "Kitchen"
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.title == "Kitchen Brilliant kiosk"
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, str(entry.unique_id))})
    assert device is not None
    assert device.name == "Kitchen Brilliant kiosk"
    panel.identity.assert_awaited_once()


async def test_the_ip_fallback_title_becomes_the_room(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.room = "Office"
    entry = make_entry(f"Brilliant kiosk {HOST}")
    await setup_entry(hass, entry)
    assert entry.title == "Office Brilliant kiosk"


async def test_an_existing_device_is_renamed_too(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.room = "Kitchen"
    entry = make_entry()
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, str(entry.unique_id))},
        name=entry.title,
    )
    assert device.name_by_user is None
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    updated = devices.async_get(device.id)
    assert updated is not None
    assert updated.name == "Kitchen Brilliant kiosk"


async def test_a_name_the_user_set_on_the_device_is_kept(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.room = "Kitchen"
    entry = make_entry()
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, str(entry.unique_id))}, name="x"
    )
    devices.async_update_device(device.id, name_by_user="Mine")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.title == "Kitchen Brilliant kiosk"
    updated = devices.async_get(device.id)
    assert updated is not None
    assert updated.name_by_user == "Mine"
    assert updated.name == "Kitchen Brilliant kiosk"


async def test_a_title_the_user_chose_is_kept_and_nothing_is_read(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.room = "Kitchen"
    entry = make_entry("My panel")
    await setup_entry(hass, entry)
    assert entry.title == "My panel"
    panel.identity.assert_not_awaited()


@pytest.mark.parametrize("error", [DeviceIdUnavailable("restarting"), RuntimeError("boom")])
async def test_an_unreadable_room_never_blocks_the_setup(
    hass, panel: MagicMock, releases: MagicMock, error: Exception
) -> None:
    panel.identity.side_effect = error
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state.name == "LOADED"
    assert entry.title == f"Brilliant panel {HOST}"


async def test_no_room_keeps_the_title(hass, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.title == f"Brilliant panel {HOST}"
