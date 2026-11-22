"""Unpinned stable releases: not offered by the panel's update entity, a repair issue instead."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.brilliant_kiosk.const import (
    DOMAIN,
    INTEGRATION_RELEASES_URL,
    ISSUE_INTEGRATION_UPDATE,
)
from custom_components.brilliant_kiosk.coordinator import DATA_RELEASES
from custom_components.brilliant_kiosk.install import (
    is_installable,
    newest_unverifiable_stable,
)
from custom_components.brilliant_kiosk.repairs import (
    async_create_fix_flow,
    find_integration_update_entity,
)
from homeassistant.components.repairs.issue_handler import RepairsFlowManager
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from .conftest import NIGHTLY, OLD, PINNED, STABLE, entity_id, make_entry, setup_entry

V16 = replace(STABLE, tag="v1.6.0", version="v1.6.0")
V17 = replace(STABLE, tag="v1.7.0", version="v1.7.0")
V145 = replace(STABLE, tag="v1.4.5", version="v1.4.5")  # between the pins, never pinned
WEIRD = replace(STABLE, tag="latest-build", version=None)


def _serve(releases: MagicMock, *items) -> None:
    releases.list = AsyncMock(return_value=tuple(items))
    releases.latest_stable = AsyncMock(return_value=None)


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_INTEGRATION_UPDATE)


async def _refresh(hass: HomeAssistant) -> None:
    await hass.data[DATA_RELEASES].coordinator.async_refresh()
    await hass.async_block_till_done()


# -- the update entity offers only what it can install ---------------------------------------------


async def test_unpinned_newer_stable_is_skipped_for_the_newest_pinned_newer_one(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, V16, STABLE, OLD)  # v1.6.0 is not pinned, v1.5.0 is
    await setup_entry(hass, make_entry())
    state = hass.states.get(entity_id(hass, "update", "kiosk"))
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == "v1.5.0"
    assert state.attributes["release_url"].endswith("v1.5.0")


async def test_nothing_is_offered_when_only_unpinned_stables_are_newer(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, V16, V145, OLD)  # installed v1.4.0; v1.4.5 and v1.6.0 are unpinned
    await setup_entry(hass, make_entry())
    state = hass.states.get(entity_id(hass, "update", "kiosk"))
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == "v1.4.0"
    assert state.attributes["release_url"] is None


async def test_nightly_channel_still_offers_a_nightly_but_not_an_unpinned_stable(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, V16, NIGHTLY, OLD)
    await setup_entry(hass, make_entry(channel="nightly"))
    state = hass.states.get(entity_id(hass, "update", "kiosk"))
    assert state.attributes["latest_version"] == NIGHTLY.version  # verified by its .sha256

    _serve(releases, V16, OLD)  # no nightly left: the unpinned stable is not a fallback
    await _refresh(hass)
    state = hass.states.get(entity_id(hass, "update", "kiosk"))
    assert state.state == STATE_OFF
    assert state.attributes["latest_version"] == "v1.4.0"


async def test_explicit_install_of_an_unpinned_tag_is_still_refused_with_the_way_out(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, V16, STABLE, OLD)
    await setup_entry(hass, make_entry())
    with pytest.raises(HomeAssistantError, match="Settings") as err:
        await hass.services.async_call(
            "update",
            "install",
            {ATTR_ENTITY_ID: entity_id(hass, "update", "kiosk"), "version": "v1.6.0"},
            blocking=True,
        )
    assert err.value.translation_key == "release_not_pinned"
    assert "Updates" in str(err.value)
    releases.download.assert_not_awaited()


def test_installable_and_unverifiable_helpers() -> None:
    with patch("custom_components.brilliant_kiosk.install.PINNED_RELEASES", PINNED):
        assert is_installable(STABLE) and is_installable(NIGHTLY)
        assert not is_installable(V16)
        assert newest_unverifiable_stable([NIGHTLY, STABLE, OLD]) is None
        assert newest_unverifiable_stable([V145, V16, V17, WEIRD, NIGHTLY]) is V17
        # Older than a pin and never pinned: not a reason to update the integration.
        old_unpinned = replace(OLD, tag="v1.3.0", version="v1.3.0")
        assert newest_unverifiable_stable([old_unpinned, V145]) is None
    with patch(
        "custom_components.brilliant_kiosk.install.PINNED_RELEASES", {"dev": PINNED["v1.5.0"]}
    ):
        assert newest_unverifiable_stable([V16]) is V16  # a non-version pin is ignored


# -- the repair issue ------------------------------------------------------------------------------


async def test_issue_is_created_updated_and_deleted(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, STABLE, OLD)
    await setup_entry(hass, make_entry())
    assert _issue(hass) is None

    _serve(releases, V16, STABLE, OLD)
    await _refresh(hass)
    issue = _issue(hass)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_INTEGRATION_UPDATE
    assert issue.translation_placeholders == {"tag": "v1.6.0"}
    assert issue.data == {"tag": "v1.6.0"}
    assert issue.learn_more_url == INTEGRATION_RELEASES_URL

    _serve(releases, V17, V16, STABLE, OLD)  # a newer one appears: the same issue, updated
    await _refresh(hass)
    assert _issue(hass).translation_placeholders == {"tag": "v1.7.0"}
    assert len([k for k in ir.async_get(hass).issues if k[0] == DOMAIN]) == 1

    with patch.dict(PINNED, {"v1.6.0": PINNED["v1.5.0"], "v1.7.0": PINNED["v1.5.0"]}):
        await _refresh(hass)  # the updated integration pins them
        assert _issue(hass) is None


async def test_nightlies_and_unparseable_tags_raise_no_issue(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, NIGHTLY, WEIRD, STABLE, OLD)
    await setup_entry(hass, make_entry())
    assert _issue(hass) is None


async def test_two_panels_share_one_issue_and_it_goes_with_the_last(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, V16, STABLE, OLD)
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
    ours = [
        k for k in ir.async_get(hass).issues if k[0] == DOMAIN and k[1] == ISSUE_INTEGRATION_UPDATE
    ]
    assert ours == [(DOMAIN, ISSUE_INTEGRATION_UPDATE)]

    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass) is not None  # a panel still uses the shared releases

    assert await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass) is None


# -- finding the integration's own update entity ---------------------------------------------------


def _fake_update(hass: HomeAssistant, *, platform="marketplace", object_id="brilliant_kiosk", **kw):
    """A registered update entity on another platform, with a state."""
    state = kw.pop("state", STATE_ON)
    name = kw.pop("name", None)
    attributes = kw.pop("attributes", {"installed_version": "v0.3.0", "latest_version": "v0.4.0"})
    registered = er.async_get(hass).async_get_or_create(
        "update", platform, f"uid-{platform}-{object_id}", suggested_object_id=object_id, **kw
    )
    if name:
        registered = er.async_get(hass).async_update_entity(registered.entity_id, name=name)
    hass.states.async_set(registered.entity_id, state, attributes)
    return registered


async def _start(hass: HomeAssistant, tag: str = "v1.6.0"):
    assert await async_setup_component(hass, "repairs", {})
    flow = await async_create_fix_flow(hass, ISSUE_INTEGRATION_UPDATE, {"tag": tag})
    flow.hass = hass
    flow.handler = DOMAIN
    flow.flow_id = "test"
    return flow


async def test_fix_flow_installs_the_integration_update_on_the_right_entity(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, V16, STABLE, OLD)
    await setup_entry(hass, make_entry())
    _fake_update(hass, object_id="something_else")  # not ours: other name, other id
    mine = _fake_update(hass)
    calls = async_mock_service(hass, "update", "install")

    assert _issue(hass) is not None
    assert await async_setup_component(hass, "repairs", {})
    manager = RepairsFlowManager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_INTEGRATION_UPDATE})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {
        "tag": "v1.6.0",
        "installed": "v0.3.0",
        "latest": "v0.4.0",
        "error": "",
    }
    assert not calls

    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [c.data for c in calls] == [{ATTR_ENTITY_ID: mine.entity_id}]
    assert mine.entity_id == "update.brilliant_kiosk"


async def test_install_failure_keeps_the_form_with_the_reason(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _fake_update(hass)
    flow = await _start(hass)
    async_mock_service(hass, "update", "install", raise_exception=HomeAssistantError("disk full"))
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "install_failed"}
    assert result["description_placeholders"]["error"] == "disk full"


async def test_no_update_entity_aborts_with_a_reason(hass: HomeAssistant) -> None:
    flow = await _start(hass)
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_update"
    assert result["description_placeholders"] == {"tag": "v1.6.0"}


@pytest.mark.parametrize("state", [STATE_OFF, None])
async def test_no_update_available_yet_aborts(hass: HomeAssistant, state: str | None) -> None:
    registered = _fake_update(hass, state=STATE_OFF if state else STATE_ON)
    if state is None:
        hass.states.async_remove(registered.entity_id)  # registered but without a state
    calls = async_mock_service(hass, "update", "install")
    flow = await _start(hass)
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_update"
    assert not calls


async def test_panels_own_update_entities_are_never_matched(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    _serve(releases, V16, STABLE, OLD)
    await setup_entry(hass, make_entry(title="Brilliant Kiosk"))
    own = entity_id(hass, "update", "kiosk")
    assert hass.states.get(own).state == STATE_ON  # an update is available on the panel
    # Even with the integration's id and name, an entity of this platform is a panel's.
    er.async_get(hass).async_update_entity(own, name="Brilliant Kiosk")
    assert find_integration_update_entity(hass) is None
    flow = await _start(hass)
    assert (await flow.async_step_init())["type"] is FlowResultType.ABORT


async def test_matching_rules(hass: HomeAssistant) -> None:
    other = MockConfigEntry(domain="hacs", title="HACS")
    other.add_to_hass(hass)
    devices = dr.async_get(hass)
    assert find_integration_update_entity(hass) is None

    _fake_update(hass, platform="a", object_id="unrelated")
    _fake_update(hass, platform="b", object_id="zz_named", original_name="Brilliant Kiosk")
    assert find_integration_update_entity(hass) == "update.zz_named"
    _fake_update(hass, platform="c", object_id="aa_named", name="Brilliant Kiosk")
    assert find_integration_update_entity(hass) == "update.aa_named"  # first by entity id
    disabled = _fake_update(hass, platform="d", object_id="0_disabled", name="Brilliant Kiosk")
    er.async_get(hass).async_update_entity(
        disabled.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    assert find_integration_update_entity(hass) == "update.aa_named"
    _fake_update(hass, platform="e", object_id="brilliant_kiosk")
    assert find_integration_update_entity(hass) == "update.brilliant_kiosk"  # exact id wins

    # Named after its device (the usual case for a has_entity_name entity).
    for key in list(er.async_get(hass).entities):
        er.async_get(hass).async_remove(key)
    device = devices.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("hacs", "1")}, name="Brilliant Kiosk"
    )
    er.async_get(hass).async_get_or_create(
        "update", "hacs", "repo-1", suggested_object_id="repo_update", device_id=device.id
    )
    assert find_integration_update_entity(hass) == "update.repo_update"
    devices.async_update_device(device.id, name="Something else")
    assert find_integration_update_entity(hass) is None
    devices.async_update_device(device.id, name_by_user="Brilliant Kiosk")
    assert find_integration_update_entity(hass) == "update.repo_update"
