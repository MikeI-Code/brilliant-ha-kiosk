"""Setup, unload, coordinator failures, repairs and device registry."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.brilliant_kiosk.const import (
    CONF_MQTT_PROVIDER,
    DOMAIN,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
)
from custom_components.brilliant_kiosk.coordinator import DATA_RELEASES
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
    SetupStatus,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import (
    HEALTH,
    HOST,
    NIGHTLY,
    NODE,
    SETTINGS,
    STABLE,
    UNIQUE_ID,
    entity_id,
    make_entry,
    setup_entry,
)


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
        (CommandFailed("c", 1, "x"), ConfigEntryState.SETUP_RETRY),
        (AuthFailed("nope"), ConfigEntryState.SETUP_ERROR),
        (HostKeyMismatch("changed", "SHA256:x"), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_first_refresh_failures(
    hass, panel: MagicMock, releases: MagicMock, error: Exception, state: ConfigEntryState
) -> None:
    panel.poll_error = error
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

    panel.poll_error = CannotConnect("down")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "unavailable"

    panel.poll_error = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=130))
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "v1.4.0"


async def test_missing_env_and_setup_tool_are_tolerated(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """A poll whose settings are empty and whose setup tool failed still gives a working panel."""
    panel.get_settings.return_value = {}
    panel.setup_status.return_value = SetupStatus(state="unknown")
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.runtime_data.data.env == {}
    assert entry.runtime_data.data.setup.state == "unknown"


async def test_one_poll_is_one_library_call(hass, panel: MagicMock, releases: MagicMock) -> None:
    """The coordinator reads everything with Panel.poll() (one SSH exec), not four reads."""
    entry = make_entry()
    await setup_entry(hass, entry)
    for name in ("health", "get_settings", "env_drift", "setup_status"):
        getattr(panel, name).reset_mock()
    panel.poll.reset_mock()
    await entry.runtime_data.async_refresh()
    assert panel.poll.await_count == 1
    for name in ("health", "get_settings", "env_drift", "setup_status"):
        getattr(panel, name).assert_not_called()
    assert entry.runtime_data.update_interval == timedelta(seconds=60)


async def test_the_newest_stable_release_is_found_when_the_first_page_has_none(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """Thirty nightlies push the stable release off the list: /releases/latest still finds it."""
    releases.list = AsyncMock(return_value=(NIGHTLY,))
    releases.latest_stable = AsyncMock(return_value=STABLE)
    entry = make_entry()
    await setup_entry(hass, entry)
    assert [r.tag for r in entry.runtime_data.releases.data] == [NIGHTLY.tag, STABLE.tag]

    # A stable release that is on the list is not added twice; none at all adds nothing.
    releases.list = AsyncMock(return_value=(NIGHTLY, STABLE))
    await entry.runtime_data.releases.async_refresh()
    assert [r.tag for r in entry.runtime_data.releases.data] == [NIGHTLY.tag, STABLE.tag]
    releases.latest_stable = AsyncMock(return_value=None)
    await entry.runtime_data.releases.async_refresh()
    assert [r.tag for r in entry.runtime_data.releases.data] == [NIGHTLY.tag, STABLE.tag]

    # Either request failing is a failed refresh, reported once by the coordinator.
    releases.latest_stable = AsyncMock(side_effect=ReleaseError("rate limited"))
    await entry.runtime_data.releases.async_refresh()
    assert entry.runtime_data.releases.last_update_success is False


async def test_release_failure_is_not_fatal(hass, panel: MagicMock, releases: MagicMock) -> None:
    releases.list.side_effect = ReleaseError("github down")
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.releases.last_update_success is False


async def test_other_integrations_config_entries_are_never_read(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """No Wyoming lookup and no voice repair: a panel with the voice satellite on raises nothing."""
    panel.get_settings.return_value = {"KIOSK_MQTT": "1", "KIOSK_VOICE": "1"}
    wyoming = MockConfigEntry(domain="wyoming", data={"host": "10.9.9.9", "port": 10700})
    wyoming.add_to_hass(hass)
    entry = make_entry()
    asked: list[str | None] = []
    real = hass.config_entries.async_entries

    def spy(domain: str | None = None, *args, **kwargs):
        asked.append(domain)
        return real(domain, *args, **kwargs)

    hass.config_entries.async_entries = spy  # type: ignore[method-assign]
    await setup_entry(hass, entry)
    await entry.runtime_data.async_refresh()
    assert "wyoming" not in asked
    assert not [
        issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith("voice_not_in_wyoming")
    ]


async def test_host_key_change_starts_reauth_not_a_repair_issue(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    panel.poll_error = HostKeyMismatch("changed", "SHA256:newkey")
    await entry.runtime_data.async_refresh()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert not [i for (d, i) in ir.async_get(hass).issues if d == DOMAIN and "host_key" in i]
    assert not entry.runtime_data.last_update_success


async def test_unreachable_panel_does_not_start_reauth(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.poll_error = CannotConnect("down")
    await entry.runtime_data.async_refresh()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


# -- device software version ---------------------------------------------------------------


async def test_device_sw_version_follows_the_panel(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert _our_device(hass).sw_version == "v1.4.0"

    panel.health.return_value = replace(HEALTH, version="v1.5.0")
    await entry.runtime_data.async_refresh()
    assert _our_device(hass).sw_version == "v1.5.0"
    assert entry.runtime_data is not None  # no reload needed

    # An unreadable version (None) does not wipe what is known.
    panel.health.return_value = replace(HEALTH, version=None)
    await entry.runtime_data.async_refresh()
    assert _our_device(hass).sw_version == "v1.5.0"


# -- repair issues are removed with their entry ----------------------------------------------


def _issues(hass, entry_id: str) -> set[str]:
    return {
        issue_id
        for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.endswith(entry_id)
    }


def _raise_all_issues(hass, entry) -> set[str]:
    registry = ir.async_get(hass)
    ids = {
        f"{key}_{entry.entry_id}"
        for key in ("env_drift", "mqtt_provider_not_applied", "host_key_changed", "voice_x")
    }
    for issue_id in ids:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="env_drift",
        )
    assert registry.async_get_issue(DOMAIN, f"env_drift_{entry.entry_id}")
    return ids


async def test_removing_an_entry_deletes_all_of_its_issues_and_only_its_own(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    first = make_entry()
    await setup_entry(hass, first)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second",
        unique_id="brilliant_second",
        data={**first.data, "host": "192.168.1.51", "node": "brilliant_second"},
        options=dict(first.options),
    )
    await setup_entry(hass, second)
    mine = _raise_all_issues(hass, first)
    theirs = _raise_all_issues(hass, second)
    ir.async_create_issue(
        hass,
        "other_domain",
        f"env_drift_{first.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="x",
    )
    assert _issues(hass, first.entry_id) == mine

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()
    assert _issues(hass, first.entry_id) == set()
    assert _issues(hass, second.entry_id) == theirs
    # Another integration's issue with a similar id is not ours to delete.
    assert ir.async_get(hass).async_get_issue("other_domain", f"env_drift_{first.entry_id}")


async def test_unloading_drops_the_drift_issue_but_keeps_the_provider_issue(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.env_drift.return_value = ("DAY_LEVEL",)
    entry = make_entry()
    await setup_entry(hass, entry)
    drift = f"env_drift_{entry.entry_id}"
    provider = f"mqtt_provider_not_applied_{entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        provider,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="mqtt_provider_not_applied",
        translation_placeholders={"host": HOST, "error": "x"},
    )
    assert {drift, provider} <= _issues(hass, entry.entry_id)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _issues(hass, entry.entry_id) == {provider}  # the options listener raises that one

    # A reload brings the drift issue back, because the copies still differ.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert drift in _issues(hass, entry.entry_id)


async def test_a_failed_unload_keeps_the_issues(
    hass, panel: MagicMock, releases: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel.env_drift.return_value = ("DAY_LEVEL",)
    entry = make_entry()
    await setup_entry(hass, entry)

    async def refuse(*args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", refuse)
    from custom_components.brilliant_kiosk import async_unload_entry

    assert await async_unload_entry(hass, entry) is False
    assert f"env_drift_{entry.entry_id}" in _issues(hass, entry.entry_id)


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
    panel.get_settings.return_value = {**SETTINGS, "KIOSK_MQTT": "kiosk"}  # no MQTT_DEVICE_ID
    await setup_entry(
        hass,
        make_entry(
            **{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, "mqtt_device_id": "from_options"}
        ),
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
    assert DATA_RELEASES in hass.data  # still needed by the remaining panel

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert DATA_RELEASES not in hass.data  # the last panel stopped it


async def test_release_helpers_tolerate_unknown_entries(hass) -> None:
    from custom_components.brilliant_kiosk.coordinator import async_release_releases

    await async_release_releases(hass, "never-acquired")  # nothing to release: no error


# -- options listener applies the MQTT provider ---------------------------------------------


async def test_provider_apply_failure_raises_a_repair_issue_then_clears(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
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
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert "job running" in issue.translation_placeholders["error"]
    assert entry.state is ConfigEntryState.LOADED  # the options dialog was not blocked either
    assert entry.runtime_data is coordinator  # and the entry was not reloaded for it

    panel.set_env.side_effect = None
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, "mqtt_device_id": ""}
    )
    await hass.async_block_till_done()
    await coordinator.async_refresh()  # the next poll sees that panel and options agree again
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_provider_the_panel_did_not_accept_stays_ineffective(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """After a failed apply the old provider decides where the MQTT device is, even on reload."""
    builtin_device = _add_mqtt_device(hass, NODE)
    _add_mqtt_device(hass, "d")  # what the refused brilliant-mqtt provider would have made
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
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
    assert entry.runtime_data is coordinator  # not reloaded for a provider that was refused
    assert coordinator.mqtt_provider == PROVIDER_BUILTIN  # still the one the panel runs
    assert coordinator.mqtt_device_ids() == [NODE]
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_provider_not_applied_{entry.entry_id}")

    # A reload for something else does not make the refused option effective either.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "manage_installs": False}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data is not coordinator
    assert entry.runtime_data.mqtt_provider == PROVIDER_BUILTIN  # the first poll follows the panel
    assert entry.runtime_data.find_mqtt_device().id == builtin_device.id
    assert _our_device(hass).via_device_id == builtin_device.id
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_provider_not_applied_{entry.entry_id}")


async def test_every_poll_compares_the_panel_with_the_provider_option(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, "mqtt_device_id": "d"})
    await setup_entry(hass, entry)  # the panel runs the built-in mode: it disagrees
    issues = ir.async_get(hass)
    issue_id = f"mqtt_provider_not_applied_{entry.entry_id}"
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert (
        "the panel runs the builtin provider, the options say brilliant_mqtt"
        in (issue.translation_placeholders["error"])
    )

    coordinator = entry.runtime_data
    panel.get_settings.return_value = {**SETTINGS, "KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "x"}
    await coordinator.async_refresh()  # the provider agrees now, the device id does not
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert "MQTT_DEVICE_ID is 'x'" in issue.translation_placeholders["error"]
    assert coordinator.mqtt_provider == PROVIDER_BRILLIANT_MQTT

    panel.get_settings.return_value = {**SETTINGS, "KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "d"}
    await coordinator.async_refresh()
    assert issues.async_get_issue(DOMAIN, issue_id) is None  # all three agree

    panel.get_settings.return_value = dict(SETTINGS)  # someone switched it back on the panel
    await coordinator.async_refresh()
    assert issues.async_get_issue(DOMAIN, issue_id) is not None
    assert coordinator.mqtt_provider == PROVIDER_BUILTIN


async def test_an_entry_without_the_provider_option_follows_the_panel_without_an_issue(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    template = make_entry()
    entry = MockConfigEntry(  # an entry made before the provider was an option
        domain=DOMAIN,
        title=template.title,
        unique_id=template.unique_id,
        version=2,
        data=template.data,
    )
    panel.get_settings.return_value = {**SETTINGS, "KIOSK_MQTT": "kiosk"}
    await setup_entry(hass, entry)
    assert entry.runtime_data.mqtt_provider == PROVIDER_BRILLIANT_MQTT
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"mqtt_provider_not_applied_{entry.entry_id}")
        is None
    )


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
