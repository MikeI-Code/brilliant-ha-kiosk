"""The identity of a panel: Brilliant's device id, never the host key; and entries made before.

Version 1 entries could carry the host key fingerprint as unique id (when the id could not be
read). A fingerprint changes when the panel is re-flashed, so such an entry is converted to the
device id on the first successful poll, together with its device and entities.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from custom_components.brilliant_kiosk.const import (
    CHANNEL_NIGHTLY,
    CHANNEL_STABLE,
    CONF_CHANNEL,
    CONF_NODE,
    DOMAIN,
    is_fingerprint,
    node_of,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pybrilliantkiosk import DeviceIdUnavailable
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import DEVICE_ID, FINGERPRINT, HEALTH, NODE, entity_id, make_entry, setup_entry

NIGHTLY_VERSION = "v1.4.0-nightly.20261107.g287e70e"


def legacy_entry(hass: HomeAssistant, **options: object) -> MockConfigEntry:
    """An entry made by version 1 without a readable device id: the fingerprint is its id."""
    entry = make_entry(**options)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_NODE: None}, unique_id=FINGERPRINT, version=1
    )
    return entry


def _identifiers(hass: HomeAssistant) -> set[str]:
    return {
        ident
        for device in dr.async_get(hass).devices.values()
        for domain, ident in device.identifiers
        if domain == DOMAIN
    }


async def _poll(hass: HomeAssistant, seconds: int) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def test_the_node_is_the_device_id_prefix_and_a_fingerprint_is_recognised() -> None:
    assert node_of(DEVICE_ID) == NODE == "brilliant_" + DEVICE_ID[:12]
    assert is_fingerprint("SHA256:abc")
    assert not is_fingerprint(NODE)
    assert not is_fingerprint(None)
    assert not is_fingerprint("")


async def test_a_fingerprint_entry_is_converted_on_the_first_poll_with_device_and_entities(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = legacy_entry(hass)
    # What the version 1 entry had created: a device and entities named after the fingerprint.
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, FINGERPRINT)}
    )
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{FINGERPRINT}_version", config_entry=entry, device_id=device.id
    )
    unrelated = registry.async_get_or_create("sensor", DOMAIN, "something_else", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == NODE
    assert entry.data[CONF_NODE] == NODE
    assert entry.version == 2
    assert _identifiers(hass) == {NODE}  # the same device, renamed: not a second one
    assert dr.async_get(hass).async_get(device.id) is not None
    migrated = registry.async_get(old.entity_id)
    assert migrated is not None
    assert migrated.unique_id == f"{NODE}_version"  # the entity keeps its id and history
    assert registry.async_get(unrelated.entity_id).unique_id == "something_else"
    assert hass.states.get(old.entity_id).state == "v1.4.0"
    panel.device_id.assert_awaited_once()
    # Converted at the very first refresh: no entity existed yet, so no reload was needed.
    coordinator = entry.runtime_data
    await _poll(hass, 61)
    assert entry.runtime_data is coordinator
    panel.device_id.assert_awaited_once()  # and never asked again


async def test_conversion_waits_for_a_poll_that_can_read_the_id_then_reloads(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    panel.device_id.side_effect = DeviceIdUnavailable("bus restarting")
    entry = legacy_entry(hass)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED  # an unreadable id does not break the panel
    assert entry.unique_id == FINGERPRINT
    assert _identifiers(hass) == {FINGERPRINT}
    first = entry.runtime_data

    await _poll(hass, 61)  # still unreadable: tried again, nothing changed
    assert panel.device_id.await_count == 2
    assert entry.unique_id == FINGERPRINT

    panel.device_id.side_effect = None
    await _poll(hass, 122)
    assert entry.unique_id == NODE
    assert _identifiers(hass) == {NODE}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not first  # reloaded: the entities now use the new id
    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in registry.entities.values() if e.config_entry_id == entry.entry_id
    }
    assert unique_ids
    assert all(u.startswith(f"{NODE}_") for u in unique_ids)
    assert panel.device_id.await_count == 3


async def test_two_entries_for_one_panel_are_reported_and_left_alone(
    hass: HomeAssistant,
    panel: MagicMock,
    releases: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    twin = make_entry()  # already has the node as unique id
    twin.add_to_hass(hass)
    entry = legacy_entry(hass)
    with caplog.at_level("WARNING"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.unique_id == FINGERPRINT  # nothing was merged or renamed
    assert "configured twice" in caplog.text
    await _poll(hass, 61)
    panel.device_id.assert_awaited_once()  # and the user is not nagged every minute


async def test_conversion_leaves_an_existing_device_of_the_new_id_alone(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = legacy_entry(hass)
    devices = dr.async_get(hass)
    devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, FINGERPRINT)})
    devices.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, NODE)})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == NODE
    assert _identifiers(hass) == {FINGERPRINT, NODE}  # not merged by guesswork


async def test_a_current_entry_never_asks_for_the_device_id(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    await _poll(hass, 61)
    panel.device_id.assert_not_called()


# -- config entry versions ----------------------------------------------------------------------


async def test_a_version_1_entry_is_migrated_to_version_2(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, version=1)
    await setup_entry(hass, entry)
    assert entry.version == 2
    assert entry.unique_id == NODE  # a node id needs nothing else
    assert entry.state is ConfigEntryState.LOADED


async def test_an_entry_of_a_newer_version_is_refused(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, version=3)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 3


async def test_the_config_flow_makes_version_2_entries() -> None:
    from custom_components.brilliant_kiosk.config_flow import BrilliantKioskConfigFlow

    assert BrilliantKioskConfigFlow.VERSION == 2


# -- the release channel follows what is installed ---------------------------------------------


@pytest.mark.parametrize(
    ("version", "channel"),
    [
        (NIGHTLY_VERSION, CHANNEL_NIGHTLY),
        ("v1.4.0", CHANNEL_STABLE),
        ("dev-build", CHANNEL_STABLE),
        (None, CHANNEL_STABLE),
    ],
)
async def test_the_channel_is_taken_from_the_installed_version_on_first_setup(
    hass: HomeAssistant,
    panel: MagicMock,
    releases: MagicMock,
    version: str | None,
    channel: str,
) -> None:
    panel.health.return_value = replace(HEALTH, version=version)
    entry = make_entry()
    assert CONF_CHANNEL not in entry.options
    await setup_entry(hass, entry)
    assert entry.options[CONF_CHANNEL] == channel
    select = entity_id(hass, "select", "channel")
    assert select is not None
    assert hass.states.get(select).state == channel


async def test_a_channel_the_user_chose_is_never_overwritten(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    panel.health.return_value = replace(HEALTH, version=NIGHTLY_VERSION)
    entry = make_entry(**{CONF_CHANNEL: CHANNEL_STABLE})
    await setup_entry(hass, entry)
    assert entry.options[CONF_CHANNEL] == CHANNEL_STABLE


async def test_defaulting_the_channel_does_not_reload_the_entry(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    panel.health.return_value = replace(HEALTH, version=NIGHTLY_VERSION)
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    assert panel.coordinator_panel_cls.call_count == 1
