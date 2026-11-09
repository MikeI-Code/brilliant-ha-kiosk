"""Setup, unload, coordinator failures, repairs and device registry."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from custom_components.brilliant_kiosk.const import (
    CONF_MQTT_PROVIDER,
    DOMAIN,
    PROVIDER_BRILLIANT_MQTT,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pybrilliantkiosk import (
    AuthFailed,
    CannotConnect,
    CommandFailed,
    HostKeyMismatch,
    ReleaseError,
    SetupError,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import NODE, UNIQUE_ID, entity_id, make_entry, setup_entry


async def test_setup_and_unload(hass, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.data.health.version == "v1.4.0"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    panel.close.assert_awaited()


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (CannotConnect("down"), ConfigEntryState.SETUP_RETRY),
        (HostKeyMismatch("changed", "SHA256:x"), ConfigEntryState.SETUP_RETRY),
        (CommandFailed("c", 1, "x"), ConfigEntryState.SETUP_RETRY),
        (AuthFailed("nope"), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_first_refresh_failures(
    hass, panel: MagicMock, releases: MagicMock, error: Exception, state: ConfigEntryState
) -> None:
    panel.health.side_effect = error
    entry = make_entry()
    await setup_entry_no_assert(hass, entry)
    assert entry.state is state
    if state is ConfigEntryState.SETUP_ERROR:
        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def setup_entry_no_assert(hass, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_entities_unavailable_then_recover(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    await setup_entry(hass, make_entry())
    eid = entity_id(hass, "sensor", "version")
    assert eid and hass.states.get(eid).state == "v1.4.0"

    panel.health.side_effect = CannotConnect("down")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "unavailable"

    panel.health.side_effect = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=130))
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "v1.4.0"


async def test_missing_env_and_setup_tool_are_tolerated(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.side_effect = CommandFailed("cat", 1, "no file")
    panel.setup_status.side_effect = SetupError("odd")
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.runtime_data.data.env == {}
    assert entry.runtime_data.data.setup.state == "unknown"


async def test_release_failure_is_not_fatal(hass, panel: MagicMock, releases: MagicMock) -> None:
    releases.list.side_effect = ReleaseError("github down")
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.releases.last_update_success is False


async def test_voice_issue_created_and_cleared(hass, panel: MagicMock, releases: MagicMock) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "1", "KIOSK_VOICE": "1"}
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"voice_not_in_wyoming_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    wyoming = MockConfigEntry(domain="wyoming", data={"host": "192.168.1.50", "port": 10700})
    wyoming.add_to_hass(hass)
    await entry.runtime_data.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_device_joins_mqtt_device(hass, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    mqtt_device = registry.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", NODE)}, name="Panel"
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    eid = entity_id(hass, "sensor", "version")
    from homeassistant.helpers import entity_registry as er

    assert er.async_get(hass).async_get(eid).device_id == mqtt_device.id


async def test_device_uses_brilliant_mqtt_device_id(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "bm_dev"}
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT})
    entry.add_to_hass(hass)
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", "bm_dev")}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    from homeassistant.helpers import entity_registry as er

    assert er.async_get(hass).async_get(entity_id(hass, "sensor", "version")).device_id == device.id


async def test_own_device_when_no_mqtt_device(hass, panel: MagicMock, releases: MagicMock) -> None:
    await setup_entry(hass, make_entry())
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UNIQUE_ID)})
    assert device is not None
    assert device.manufacturer == "Brilliant"
    assert device.sw_version == "v1.4.0"


async def test_options_change_reloads_only_for_real_changes(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    first = entry.runtime_data
    hass.config_entries.async_update_entry(entry, options={**entry.options, "channel": "nightly"})
    await hass.async_block_till_done()
    assert entry.runtime_data is first  # a channel change does not reload

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "manage_installs": False}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data is not first
