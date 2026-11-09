"""Setup, unload, coordinator failures, repairs and device registry."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from custom_components.brilliant_kiosk.const import (
    CONF_MQTT_PROVIDER,
    DOMAIN,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pybrilliantkiosk import (
    AuthFailed,
    CannotConnect,
    CommandFailed,
    HostKeyMismatch,
    ReleaseError,
    SetupBusy,
    SetupError,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import HOST, NODE, UNIQUE_ID, entity_id, make_entry, setup_entry


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


async def test_voice_issue_resolves_hostnames_before_flagging(
    hass, panel: MagicMock, releases: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Wyoming entry that names the panel by hostname is the same panel, not a missing one."""
    addresses = {"192.168.1.50": {"192.168.1.50"}, "kiosk.local": {"192.168.1.50"}}

    async def fake_resolve(hass_, host: str) -> set[str]:
        return addresses.get(host, set())

    monkeypatch.setattr("custom_components.brilliant_kiosk.coordinator._resolve", fake_resolve)
    panel.get_settings.return_value = {"KIOSK_MQTT": "1", "KIOSK_VOICE": "1"}
    MockConfigEntry(domain="wyoming", data={"host": "kiosk.local", "port": 10700}).add_to_hass(hass)
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"voice_not_in_wyoming_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    # A Wyoming entry for another machine does not count.
    addresses["kiosk.local"] = {"192.168.1.77"}
    await entry.runtime_data.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_resolve_helper(hass) -> None:
    from custom_components.brilliant_kiosk.coordinator import _resolve

    assert await _resolve(hass, "127.0.0.1") == {"127.0.0.1"}
    assert await _resolve(hass, "host.invalid.") == set()  # does not resolve: empty, no error


async def test_host_key_change_raises_a_repair_issue_until_it_matches_again(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"host_key_changed_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    panel.health.side_effect = HostKeyMismatch("changed", "SHA256:newkey")
    await entry.runtime_data.async_refresh()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"host": HOST, "fingerprint": "SHA256:newkey"}
    assert issue.severity is ir.IssueSeverity.ERROR

    panel.health.side_effect = HostKeyMismatch("changed")  # no fingerprint known
    await entry.runtime_data.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id).translation_placeholders[
        "fingerprint"
    ] == "unknown"

    panel.health.side_effect = None
    await entry.runtime_data.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_unreachable_panel_does_not_raise_the_host_key_issue(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.health.side_effect = CannotConnect("down")
    await entry.runtime_data.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"host_key_changed_{entry.entry_id}") is None


# -- device registry -----------------------------------------------------------------------


def _add_mqtt_device(hass, identifier: str):
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", identifier)}, name="MQTT panel"
    )


def _our_device(hass):
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UNIQUE_ID)})


async def test_device_has_own_identifiers_and_never_claims_mqtt_ones(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    await setup_entry(hass, make_entry())
    device = _our_device(hass)
    assert device is not None
    assert device.identifiers == {(DOMAIN, UNIQUE_ID)}
    assert device.manufacturer == "Brilliant"
    assert device.sw_version == "v1.4.0"
    assert device.via_device_id is None  # no MQTT device exists
    assert dr.async_get(hass).async_get_device(identifiers={("mqtt", NODE)}) is None
    eid = entity_id(hass, "sensor", "version")
    assert er.async_get(hass).async_get(eid).device_id == device.id


async def test_builtin_mqtt_device_is_linked_via_device(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    mqtt_device = _add_mqtt_device(hass, NODE)  # built-in: brilliant_<devid12>
    await setup_entry(hass, make_entry())
    device = _our_device(hass)
    assert device.via_device_id == mqtt_device.id
    assert device.id != mqtt_device.id
    assert ("mqtt", NODE) not in device.identifiers
    eid = entity_id(hass, "sensor", "version")
    assert er.async_get(hass).async_get(eid).device_id == device.id


async def test_brilliant_mqtt_device_is_linked_via_device(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "bm_dev"}
    mqtt_device = _add_mqtt_device(hass, "bm_dev")
    decoy = _add_mqtt_device(hass, NODE)  # the built-in id must not win in brilliant-mqtt mode
    await setup_entry(hass, make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT}))
    assert _our_device(hass).via_device_id == mqtt_device.id
    assert _our_device(hass).via_device_id != decoy.id


async def test_brilliant_mqtt_device_id_from_options_when_env_lacks_it(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    mqtt_device = _add_mqtt_device(hass, "from_options")
    await setup_entry(
        hass,
        make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, "mqtt_device_id": "from_options"}),
    )
    assert _our_device(hass).via_device_id == mqtt_device.id


async def test_mqtt_device_appearing_later_is_linked_without_a_reload(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    assert _our_device(hass).via_device_id is None

    mqtt_device = _add_mqtt_device(hass, NODE)  # MQTT discovery finished after our setup
    assert _our_device(hass).via_device_id is None  # nothing yet: no update has run
    await coordinator.async_refresh()
    assert _our_device(hass).via_device_id == mqtt_device.id
    assert entry.runtime_data is coordinator  # no reload happened

    # Idempotent: a further update changes nothing.
    await coordinator.async_refresh()
    assert _our_device(hass).via_device_id == mqtt_device.id


# -- releases are shared between panels -----------------------------------------------------


async def test_one_releases_coordinator_serves_all_panels(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    first = make_entry()
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second",
        unique_id="brilliant_second",
        data={**first.data, "host": "192.168.1.51", "node": "brilliant_second"},
        options=dict(first.options),
    )
    await setup_entry(hass, first)
    await setup_entry(hass, second)
    assert first.runtime_data.releases is second.runtime_data.releases
    assert releases.list.await_count == 1  # one GitHub request for both panels (60/h limit)

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert "releases" in hass.data[DOMAIN]  # still needed by the remaining panel

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert "releases" not in hass.data.get(DOMAIN, {})  # the last panel stopped it


async def test_release_helpers_tolerate_unknown_entries(hass) -> None:
    from custom_components.brilliant_kiosk.coordinator import async_release_releases

    await async_release_releases(hass, "never-acquired")  # nothing to release: no error


# -- options listener applies the MQTT provider ---------------------------------------------


async def test_provider_apply_failure_raises_a_repair_issue_then_clears(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    issue_id = f"mqtt_provider_not_applied_{entry.entry_id}"
    panel.set_env.side_effect = SetupBusy("job running")
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, "mqtt_device_id": "d"},
    )
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert "job running" in issue.translation_placeholders["error"]
    assert entry.state is ConfigEntryState.LOADED  # the options dialog was not blocked either

    panel.set_env.side_effect = None
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, "mqtt_device_id": ""}
    )
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


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
