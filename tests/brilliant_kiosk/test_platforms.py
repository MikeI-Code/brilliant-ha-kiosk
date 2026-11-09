"""Every platform: sensors, binary sensors, buttons, numbers, select, update."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.brilliant_kiosk.const import DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pybrilliantkiosk import CannotConnect, InvalidArgument, SettingsRejected

from .conftest import HEALTH, NIGHTLY, OLD, STABLE, entity_id, make_entry, setup_entry


async def _enable_all(hass: HomeAssistant, entry) -> None:
    """Enable every disabled-by-default entity and reload so they are added."""
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.disabled_by:
            registry.async_update_entity(item.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    eid = entity_id(hass, platform, key)
    assert eid, f"{platform}.{key} is not registered"
    state = hass.states.get(eid)
    assert state is not None, f"{eid} has no state (disabled?)"
    return state.state


async def test_sensors(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert _state(hass, "sensor", "version") == "v1.4.0"
    assert _state(hass, "sensor", "setup_state") == "active"
    assert _state(hass, "sensor", "memory_used") == "60.0"
    # Noisy sensors start disabled.
    assert hass.states.get(entity_id(hass, "sensor", "load")) is None
    await _enable_all(hass, entry)
    assert float(_state(hass, "sensor", "load")) == 0.5


async def test_setup_state_unknown_value(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    from pybrilliantkiosk import SetupStatus

    panel.setup_status.return_value = SetupStatus(state="weird")
    await setup_entry(hass, make_entry())
    assert _state(hass, "sensor", "setup_state") == "unknown"


async def test_unit_binary_sensors(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert _state(hass, "binary_sensor", "ha_kiosk") == STATE_ON
    assert _state(hass, "binary_sensor", "ha_kiosk_bridge") == STATE_ON
    assert hass.states.get(entity_id(hass, "binary_sensor", "ha_kiosk_voice")) is None
    await _enable_all(hass, entry)
    assert _state(hass, "binary_sensor", "ha_kiosk_voice") == STATE_OFF
    assert _state(hass, "binary_sensor", "ha_kiosk_swap") == STATE_ON


async def test_buttons(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    await _enable_all(hass, entry)

    async def press(key: str) -> None:
        await hass.services.async_call(
            "button", "press", {ATTR_ENTITY_ID: entity_id(hass, "button", key)}, blocking=True
        )

    await press("restart_kiosk")
    panel.restart.assert_awaited_with("ha-kiosk")
    await press("restart_voice")
    panel.restart.assert_awaited_with("ha-kiosk-voice")
    await press("reboot")
    panel.reboot.assert_awaited_once()
    await press("reapply")
    panel.setup_command.assert_awaited_with("reapply")
    await press("rollback")
    panel.setup_command.assert_awaited_with("rollback")

    panel.restart.side_effect = CannotConnect("down")
    with pytest.raises(HomeAssistantError, match="Cannot connect"):
        await press("restart_kiosk")


async def test_managed_buttons_absent_when_installs_not_managed(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    await setup_entry(hass, make_entry(manage_installs=False))
    assert entity_id(hass, "button", "rollback") is None
    assert entity_id(hass, "button", "reboot") is not None


async def test_numbers(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    await setup_entry(hass, make_entry())
    assert _state(hass, "number", "dim_level") == "4.0"
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: entity_id(hass, "number", "min_level"), "value": 7},
        blocking=True,
    )
    panel.set_env.assert_awaited_with({"MIN_LEVEL": "7"})
    panel.restart.assert_awaited_with("ha-kiosk-display")


async def test_number_without_change_does_not_restart(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    await setup_entry(hass, make_entry())
    panel.set_env.return_value = ()
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: entity_id(hass, "number", "dim_level"), "value": 4},
        blocking=True,
    )
    panel.restart.assert_not_awaited()


async def test_number_errors_and_odd_values(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "1", "DIM_LEVEL": "x"}
    await setup_entry(hass, make_entry())
    assert _state(hass, "number", "dim_level") == "unknown"
    assert _state(hass, "number", "min_level") == "unknown"
    panel.set_env.side_effect = InvalidArgument("bad")
    with pytest.raises(ServiceValidationError, match="Invalid value"):
        await hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: entity_id(hass, "number", "dim_level"), "value": 3},
            blocking=True,
        )
    panel.set_env.side_effect = SettingsRejected("no")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number",
            "set_value",
            {ATTR_ENTITY_ID: entity_id(hass, "number", "dim_level"), "value": 3},
            blocking=True,
        )


async def test_channel_select_and_update(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    select = entity_id(hass, "select", "channel")
    update = entity_id(hass, "update", "kiosk")
    assert _state(hass, "select", "channel") == "stable"
    state = hass.states.get(update)
    assert state.state == STATE_ON
    assert state.attributes["installed_version"] == "v1.4.0"
    assert state.attributes["latest_version"] == STABLE.tag
    assert state.attributes["release_url"].endswith(STABLE.tag)
    assert len(state.attributes["release_summary"]) == 255

    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: select, "option": "nightly"}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options["channel"] == "nightly"
    assert hass.states.get(update).attributes["latest_version"] == NIGHTLY.version
    assert hass.states.get(update).attributes["release_summary"] is None


async def test_update_install_latest_and_specific(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    await setup_entry(hass, make_entry())
    update = entity_id(hass, "update", "kiosk")
    await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: update}, blocking=True)
    # A stable release is verified against the table pinned in the integration.
    releases.download.assert_awaited_with(STABLE, sha256="a" * 64, size=6)
    panel.install.assert_awaited_with(b"bundle", "a" * 64, size=6)

    # A nightly is refused while the stable channel is selected ...
    releases.download.reset_mock()
    with pytest.raises(HomeAssistantError, match="nightly"):
        await hass.services.async_call(
            "update", "install", {ATTR_ENTITY_ID: update, "version": NIGHTLY.tag}, blocking=True
        )
    releases.download.assert_not_awaited()

    # ... and installed, checked against its own .sha256 asset only, on the nightly channel.
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: entity_id(hass, "select", "channel"), "option": "nightly"},
        blocking=True,
    )
    await hass.services.async_call(
        "update", "install", {ATTR_ENTITY_ID: update, "version": NIGHTLY.tag}, blocking=True
    )
    releases.download.assert_awaited_with(NIGHTLY)
    panel.install.assert_awaited_with(b"bundle", "a" * 64, size=6)  # len(b"bundle")

    # The bundle version works as well as the tag.
    await hass.services.async_call(
        "update", "install", {ATTR_ENTITY_ID: update, "version": OLD.version}, blocking=True
    )
    releases.download.assert_awaited_with(OLD, sha256="b" * 64, size=6)

    with pytest.raises(HomeAssistantError, match="No kiosk release"):
        await hass.services.async_call(
            "update", "install", {ATTR_ENTITY_ID: update, "version": "v0"}, blocking=True
        )


async def test_update_up_to_date_and_no_releases(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    releases.list = AsyncMock(return_value=())
    releases.latest_stable = AsyncMock(return_value=None)
    await setup_entry(hass, make_entry())
    update = entity_id(hass, "update", "kiosk")
    state = hass.states.get(update)
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == "v1.4.0"
    assert state.attributes["release_url"] is None
    with pytest.raises(HomeAssistantError, match="No update available"):
        await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: update}, blocking=True)


async def test_update_version_prefix_is_ignored(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    from dataclasses import replace

    from .conftest import HEALTH

    panel.health.return_value = replace(HEALTH, version=STABLE.tag.removeprefix("v"))
    await setup_entry(hass, make_entry())
    assert hass.states.get(entity_id(hass, "update", "kiosk")).state == STATE_OFF


async def test_update_release_notes_and_no_install_feature(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, hass_ws_client
) -> None:
    await setup_entry(hass, make_entry(manage_installs=False))
    update = entity_id(hass, "update", "kiosk")
    assert hass.states.get(update).attributes["supported_features"] == 16  # RELEASE_NOTES only
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "update/release_notes", "entity_id": update})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"].startswith("Stable notes")


async def test_release_notes_empty(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, hass_ws_client
) -> None:
    releases.list = AsyncMock(return_value=())
    releases.latest_stable = AsyncMock(return_value=None)
    await setup_entry(hass, make_entry())
    update = entity_id(hass, "update", "kiosk")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "update/release_notes", "entity_id": update})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] is None


# -- update entity: versions come from the bundle, never offer a downgrade ----------------------

NIGHTLY_INSTALLED = "v1.5.0-nightly.20261108.g89ec239"  # exactly what nightly.yml writes to VERSION


async def _update_state(hass, panel, installed: str, *, channel: str = "stable", **kwargs):
    from dataclasses import replace

    panel.health.return_value = replace(HEALTH, version=installed)
    await setup_entry(hass, make_entry(channel=channel))
    return hass.states.get(entity_id(hass, "update", "kiosk"))


async def test_installed_nightly_equals_its_release_despite_the_different_tag(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    """Tag nightly-20261108 vs VERSION v1.5.0-nightly.20261108.g...: same build, no update."""
    state = await _update_state(hass, panel, NIGHTLY_INSTALLED, channel="nightly")
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == NIGHTLY_INSTALLED
    assert state.attributes["release_url"] is None


async def test_stable_channel_never_downgrades_a_nightly(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    # Installed nightly is newer than the newest stable (v1.5.0) it was built on.
    state = await _update_state(hass, panel, NIGHTLY_INSTALLED, channel="stable")
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == NIGHTLY_INSTALLED
    update = entity_id(hass, "update", "kiosk")
    with pytest.raises(HomeAssistantError, match="No update available"):
        await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: update}, blocking=True)
    releases.download.assert_not_awaited()


async def test_stable_channel_offers_stable_not_nightly(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    state = await _update_state(hass, panel, "v1.4.0", channel="stable")
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == STABLE.version


async def test_nightly_channel_offers_the_newest_build_of_either_kind(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    # A nightly built after v1.5.0 is newer than v1.5.0 itself.
    state = await _update_state(hass, panel, "v1.4.0", channel="nightly")
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == NIGHTLY.version
    update = entity_id(hass, "update", "kiosk")
    await hass.services.async_call("update", "install", {ATTR_ENTITY_ID: update}, blocking=True)
    releases.download.assert_awaited_with(NIGHTLY)


async def test_older_nightly_updates_to_a_newer_nightly_by_date(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    state = await _update_state(hass, panel, "v1.5.0-nightly.20261101.gabc1234", channel="nightly")
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == NIGHTLY.version


async def test_stable_release_after_a_nightly_is_an_update_for_both_channels(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    state = await _update_state(hass, panel, "v1.4.0-nightly.20261020.gabc1234", channel="stable")
    assert state.state == STATE_ON  # v1.5.0 is newer than any v1.4.0 nightly
    assert state.attributes["latest_version"] == STABLE.version


async def test_unknown_installed_version_is_never_updated_blindly(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    state = await _update_state(hass, panel, "weird-build")
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == "weird-build"


async def test_releases_without_a_usable_version_are_skipped(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    from dataclasses import replace

    unusable = replace(NIGHTLY, version=None)  # tag nightly-... is not a version either
    plain_tag = replace(STABLE, version=None)  # but a plain vX.Y.Z tag is
    releases.list = AsyncMock(return_value=(unusable, plain_tag, OLD))
    releases.latest_stable = AsyncMock(return_value=None)
    state = await _update_state(hass, panel, "v1.4.0", channel="nightly")
    assert state.attributes["latest_version"] == "v1.5.0"  # from plain_tag's tag

    releases.list = AsyncMock(return_value=(unusable,))
    hass.config_entries.async_entries(DOMAIN)  # same entry; refresh the shared list
    await hass.config_entries.async_reload(hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, "update", "kiosk"))
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == "v1.4.0"
