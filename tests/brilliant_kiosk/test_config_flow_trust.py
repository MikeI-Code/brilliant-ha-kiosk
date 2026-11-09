"""Trust in the panel and in what gets installed: a changed host key (reauth and reconfigure),
private-key login, pinned releases and the install form's input rules."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from custom_components.brilliant_kiosk.const import (
    CONF_HA_URL,
    CONF_HOST_KEY,
    CONF_INSTALL,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_USER,
    CONF_NEW_HOST_KEY,
    CONF_NODE,
    CONF_PRIVATE_KEY,
    CONF_TOKEN,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pybrilliantkiosk import CannotConnect, DeviceIdUnavailable, HostKeyInfo, SetupStatus

from .conftest import FINGERPRINT, HOST, NIGHTLY, STABLE, make_entry, setup_entry
from .test_config_flow import INSTALL_INPUT, USER_INPUT, connect, install, start, to_install_step

NEW_KEY = "SHA256:newkeynewkey"
NEW_INFO = HostKeyInfo(NEW_KEY, "ssh-ed25519")
PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
KEY_INPUT = {CONF_HOST: HOST, CONF_USERNAME: "root", CONF_PRIVATE_KEY: PEM}


def _defaults(result) -> dict[str, object]:
    return {
        str(k): k.default()
        for k in result["data_schema"].schema
        if getattr(k, "default", vol.UNDEFINED) is not vol.UNDEFINED
    }


@pytest.fixture
def bare_panel(panel: MagicMock, releases: MagicMock, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    panel.setup_status.return_value = SetupStatus(state="not_installed")
    monkeypatch.setattr(
        "custom_components.brilliant_kiosk.config_flow.kiosk_env_from_bundle",
        lambda bundle, overrides: "".join(f'{k}="{v}"\n' for k, v in overrides.items()),
    )
    return panel


# -- reauth: a changed host key is shown and pinned only after explicit confirmation --------------


async def test_reauth_shows_a_changed_host_key_and_pins_it_only_when_confirmed(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.flow_panel_cls.reset_mock()
    panel.fetch_host_key.return_value = NEW_INFO

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_host_key"
    placeholders = result["description_placeholders"]
    assert placeholders["host"] == HOST
    # Both fingerprints in the same form (SHA256:..., no key type in front); the type apart.
    assert placeholders["old_fingerprint"] == FINGERPRINT
    assert placeholders["fingerprint"] == NEW_KEY
    assert placeholders["key_type"] == "ED25519"
    panel.flow_panel_cls.assert_not_called()  # no credentials went anywhere yet

    # Not confirmed: stays on the form, still nothing sent, nothing pinned.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: False}
    )
    assert result["step_id"] == "reauth_host_key"
    assert result["errors"] == {"base": "host_key_not_confirmed"}
    panel.flow_panel_cls.assert_not_called()
    assert entry.data[CONF_HOST_KEY] == FINGERPRINT

    # Confirmed: the password is asked for, and used only with the new key pinned.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: True}
    )
    assert result["step_id"] == "reauth_confirm"
    panel.flow_panel_cls.assert_not_called()
    panel.connect.return_value = NEW_KEY
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "newpw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    kwargs = panel.flow_panel_cls.call_args.kwargs
    assert kwargs["host_key"] == NEW_KEY
    assert kwargs["password"] == "newpw"
    assert entry.data[CONF_HOST_KEY] == NEW_KEY
    assert entry.data[CONF_PASSWORD] == "newpw"
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_refuses_another_panel_behind_the_new_key(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.fetch_host_key.return_value = NEW_INFO
    panel.device_id.return_value = "ffffffffffffffffffff"
    panel.connect.return_value = NEW_KEY
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "newpw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_HOST_KEY] == FINGERPRINT  # nothing was pinned
    assert entry.data[CONF_PASSWORD] == "pw"


async def test_reauth_with_a_matching_key_goes_straight_to_the_password(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"


async def test_reauth_when_the_panel_is_unreachable_reports_it_at_the_login(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.fetch_host_key.side_effect = CannotConnect("down")
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    panel.connect.side_effect = CannotConnect("down")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "pw2"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert panel.flow_panel_cls.call_args.kwargs["host_key"] == FINGERPRINT  # the pinned key


async def test_reauth_needs_a_password_or_a_key_and_replaces_the_old_credentials(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "auth_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PRIVATE_KEY: f"  {PEM}  \n\n"}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PRIVATE_KEY] == PEM + "\n"
    assert CONF_PASSWORD not in entry.data  # the stale password does not linger
    assert panel.flow_panel_cls.call_args.kwargs["private_key"] == PEM + "\n"
    assert panel.flow_panel_cls.call_args.kwargs["password"] is None


# -- reconfigure: the same confirmation instead of an abort ------------------------------------


async def test_reconfigure_to_a_host_with_another_key_asks_for_confirmation(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.flow_panel_cls.reset_mock()
    panel.fetch_host_key.return_value = NEW_INFO
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_host_key"
    assert result["description_placeholders"] == {
        "host": "10.0.0.9",
        "old_fingerprint": FINGERPRINT,
        "fingerprint": NEW_KEY,
        "key_type": "ED25519",
    }
    panel.flow_panel_cls.assert_not_called()  # the stored password stays home

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: False}
    )
    assert result["errors"] == {"base": "host_key_not_confirmed"}
    panel.flow_panel_cls.assert_not_called()
    assert entry.data[CONF_HOST] == HOST

    panel.connect.side_effect = CannotConnect("down")  # confirmed, but the login fails
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: True}
    )
    assert result["step_id"] == "reconfigure_host_key"
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_HOST_KEY] == FINGERPRINT

    panel.connect.side_effect = None
    panel.connect.return_value = NEW_KEY
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert panel.flow_panel_cls.call_args.kwargs["host_key"] == NEW_KEY
    assert entry.data[CONF_HOST] == "10.0.0.9"
    assert entry.data[CONF_HOST_KEY] == NEW_KEY
    assert entry.data[CONF_PASSWORD] == "pw"


async def test_reconfigure_confirmed_key_of_another_panel_is_refused(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.fetch_host_key.return_value = NEW_INFO
    panel.device_id.return_value = "ffffffffffffffffffff"
    panel.connect.return_value = NEW_KEY
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_HOST] == HOST
    assert entry.data[CONF_HOST_KEY] == FINGERPRINT


async def test_reconfigure_of_an_entry_without_a_node_compares_nothing_it_does_not_know(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """A version 1 entry whose unique id is still a fingerprint has no Brilliant id to compare.

    Refusing it (as a "different panel") after a re-flash was the bug: a fingerprint changes
    exactly then. The ids are compared only when both are known.
    """
    panel.device_id.side_effect = DeviceIdUnavailable("not yet")  # the poll cannot convert it
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_NODE: None}, unique_id=FINGERPRINT
    )
    await setup_entry(hass, entry)
    panel.fetch_host_key.return_value = NEW_INFO
    panel.device_id.side_effect = None  # the panel answers at the new address
    panel.connect.return_value = NEW_KEY
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST_KEY] == NEW_KEY


async def test_reauth_and_reconfigure_do_not_abort_when_the_device_id_cannot_be_read_now(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """After an uninstall or a re-flash the bus may be restarting: that proves nothing."""
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.device_id.side_effect = DeviceIdUnavailable("restarting")
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "newpw"}
    )
    assert result["reason"] == "reauth_successful"
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.9"


async def test_reauth_with_the_same_key_but_another_panel_is_refused(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.device_id.return_value = "ffffffffffffffffffff"
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "newpw"}
    )
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_PASSWORD] == "pw"


async def test_reconfigure_unexpected_scan_error(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.fetch_host_key.side_effect = RuntimeError("weird")
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_reconfigure_unexpected_login_error_after_a_key_confirmation(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.fetch_host_key.return_value = NEW_INFO
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    panel.connect.side_effect = RuntimeError("weird")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NEW_HOST_KEY: True}
    )
    assert result["step_id"] == "reconfigure_host_key"
    assert result["errors"] == {"base": "unknown"}


# -- private key login --------------------------------------------------------------------------


async def test_user_flow_with_a_private_key_and_no_password(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await connect(hass, {**KEY_INPUT, CONF_PRIVATE_KEY: f"\n  {PEM}\n\n"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRIVATE_KEY] == PEM + "\n"  # normalised, one trailing newline
    assert CONF_PASSWORD not in result["data"]
    kwargs = panel.flow_panel_cls.call_args.kwargs
    assert kwargs["private_key"] == PEM + "\n"
    assert kwargs["password"] is None
    assert kwargs["host_key"] == FINGERPRINT


async def test_user_flow_with_password_and_key_stores_both(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await connect(hass, {**KEY_INPUT, CONF_PASSWORD: "pw"})
    assert result["data"][CONF_PASSWORD] == "pw"
    assert result["data"][CONF_PRIVATE_KEY] == PEM + "\n"


async def test_user_flow_needs_a_password_or_a_key_before_anything_is_sent(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_USERNAME: "root", CONF_PRIVATE_KEY: "  \n"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "auth_required"}
    panel.fetch_host_key.assert_not_awaited()  # not even a connection
    panel.flow_panel_cls.assert_not_called()


async def test_a_private_key_is_not_echoed_back_into_the_form(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.fetch_host_key.side_effect = CannotConnect("down")
    result = await connect(hass, {**KEY_INPUT, CONF_PASSWORD: "pw"})
    assert result["errors"] == {"base": "cannot_connect"}
    suggested = {
        str(k): k.description["suggested_value"]
        for k in result["data_schema"].schema
        if k.description and "suggested_value" in k.description
    }
    assert CONF_PRIVATE_KEY not in suggested
    assert CONF_PASSWORD not in suggested
    assert suggested[CONF_HOST] == HOST


async def test_the_coordinator_logs_in_with_the_stored_key(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    data = {k: v for k, v in entry.data.items() if k != CONF_PASSWORD}
    hass.config_entries.async_update_entry(entry, data={**data, CONF_PRIVATE_KEY: PEM + "\n"})
    await setup_entry(hass, entry)
    kwargs = panel.coordinator_panel_cls.call_args.kwargs
    assert kwargs["private_key"] == PEM + "\n"
    assert kwargs["password"] is None
    assert kwargs["host_key"] == FINGERPRINT


# -- the install form: input rules and pinned releases ---------------------------------------


@pytest.mark.parametrize("bad", ["it's", 'say "hi"', "cost$5", "back`tick", "back\\slash"])
@pytest.mark.parametrize("field", [CONF_MQTT_PASSWORD, CONF_MQTT_USER, CONF_MQTT_HOST])
async def test_install_form_rejects_characters_the_panel_would_refuse(
    hass, bare_panel, releases, field: str, bad: str
) -> None:
    result = await to_install_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**INSTALL_INPUT, field: bad}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "install"
    assert result["errors"] == {field: "invalid_characters"}
    # Caught before anything is downloaded or sent, and what was typed is kept.
    releases.latest_stable.assert_not_awaited()
    bare_panel.install.assert_not_awaited()
    assert _defaults(result)[CONF_HA_URL] == "http://ha.local:8123"
    assert CONF_MQTT_PASSWORD not in _defaults(result)


async def test_install_form_accepts_the_characters_that_are_inert_in_quotes(
    hass, bare_panel, releases
) -> None:
    result = await to_install_step(hass)
    password = "p@ss w0rd;|&>()#%^*[]{}~!?,.:=+-_/<"
    result = await install(hass, result["flow_id"], {**INSTALL_INPUT, CONF_MQTT_PASSWORD: password})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert f'MQTT_PASSWORD="{password}"\n' in bare_panel.install.await_args.kwargs["env"]


@pytest.mark.parametrize("url", ["ha.local:8123", "ftp://ha.local", "http://", ""])
async def test_install_form_needs_an_http_url(hass, bare_panel, releases, url: str) -> None:
    result = await to_install_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**INSTALL_INPUT, CONF_HA_URL: url}
    )
    assert result["errors"] == {CONF_HA_URL: "invalid_url"}
    bare_panel.install.assert_not_awaited()


async def test_install_refuses_a_stable_release_missing_from_the_pinned_table(
    hass, bare_panel, releases, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.brilliant_kiosk.install.PINNED_RELEASES", {})
    result = await to_install_step(hass)
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "release_not_pinned"}
    assert STABLE.tag in result["description_placeholders"]["error"]
    releases.download.assert_not_awaited()  # not downloaded, let alone sent to the panel
    bare_panel.install.assert_not_awaited()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_never_installs_a_nightly_even_if_it_is_the_latest(
    hass, bare_panel, releases
) -> None:
    releases.latest_stable.return_value = NIGHTLY
    result = await to_install_step(hass)
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["errors"] == {"base": "nightly_not_allowed"}
    releases.download.assert_not_awaited()
    bare_panel.install.assert_not_awaited()


async def test_install_without_a_download_still_lets_the_user_add_the_panel(
    hass, bare_panel, releases, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.brilliant_kiosk.install.PINNED_RELEASES", {})
    result = await to_install_step(hass)
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["errors"] == {"base": "release_not_pinned"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**INSTALL_INPUT, CONF_INSTALL: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_token_is_not_subject_to_the_settings_charset(hass, bare_panel, releases) -> None:
    """The token goes to its own file (not into kiosk.env), so any characters are fine."""
    result = await to_install_step(hass)
    odd = r"tok$en`with'quotes\and" + '"all'
    result = await install(hass, result["flow_id"], {**INSTALL_INPUT, CONF_TOKEN: f" {odd} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert bare_panel.install.await_args.kwargs["token"] == odd
    assert odd not in bare_panel.install.await_args.kwargs["env"]


async def test_user_step_is_still_the_same_form_after_the_credentials_change(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await start(hass)
    names = {str(k) for k in result["data_schema"].schema}
    assert {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, CONF_PRIVATE_KEY} <= names
    required = {str(k) for k in result["data_schema"].schema if isinstance(k, vol.Required)}
    assert CONF_PASSWORD not in required  # optional when a key is given
    assert USER_INPUT[CONF_HOST] == HOST
