"""Config, reauth, reconfigure, options and discovery flows."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from custom_components.brilliant_kiosk.const import (
    CONF_EXPECTED_FINGERPRINT,
    CONF_HA_URL,
    CONF_HOST_KEY,
    CONF_INSTALL,
    CONF_MANAGE_INSTALLS,
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PROVIDER,
    CONF_MQTT_USER,
    CONF_NODE,
    CONF_TOKEN,
    DOMAIN,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
)
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from pybrilliantkiosk import (
    AuthFailed,
    CannotConnect,
    HostKeyMismatch,
    InvalidArgument,
    PanelError,
    ReleaseError,
    SettingsRejected,
    SetupError,
    SetupStatus,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    FINGERPRINT,
    HOST,
    MD5_FINGERPRINT,
    NODE,
    STABLE,
    UNIQUE_ID,
    make_entry,
    setup_entry,
)


def _defaults(result) -> dict[str, object]:
    return {
        str(k): k.default()
        for k in result["data_schema"].schema
        if getattr(k, "default", vol.UNDEFINED) is not vol.UNDEFINED
    }


def _suggested(result) -> dict[str, object]:
    return {
        str(k): k.description["suggested_value"]
        for k in result["data_schema"].schema
        if k.description and "suggested_value" in k.description
    }


USER_INPUT = {CONF_HOST: HOST, CONF_USERNAME: "root", CONF_PASSWORD: "pw"}


async def start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def submit(hass: HomeAssistant, flow_id: str, user_input=USER_INPUT):
    """Submit the user form and confirm the host key; returns the result after the login."""
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    if result["type"] is FlowResultType.FORM and result["step_id"] == "host_key":
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    return result


async def connect(hass: HomeAssistant, user_input=USER_INPUT):
    return await submit(hass, (await start(hass))["flow_id"], user_input)


# -- user flow: host key first (trust on first use) --------------------------------------------


async def test_user_flow_confirms_host_key_before_using_the_password(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert CONF_EXPECTED_FINGERPRINT in {str(k) for k in result["data_schema"].schema}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "host_key"
    assert result["description_placeholders"] == {"host": HOST, "fingerprint": FINGERPRINT}
    panel.fetch_host_key.assert_awaited_once_with(HOST)
    # Nothing credentialed happened yet: no Panel (which holds the password) was even built.
    panel.flow_panel_cls.assert_not_called()
    panel.connect.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Brilliant panel {HOST}"
    assert result["data"] == {**USER_INPUT, CONF_HOST_KEY: FINGERPRINT, CONF_NODE: NODE}
    assert result["result"].unique_id == UNIQUE_ID
    assert result["options"] == {
        CONF_MQTT_PROVIDER: PROVIDER_BUILTIN,
        CONF_MQTT_DEVICE_ID: "",
        CONF_MANAGE_INSTALLS: True,
    }
    # The password was only used with the confirmed key pinned.
    assert panel.flow_panel_cls.call_args.kwargs["host_key"] == FINGERPRINT
    assert panel.flow_panel_cls.call_args.kwargs["password"] == "pw"
    panel.close.assert_awaited()


@pytest.mark.parametrize(
    "expected",
    [FINGERPRINT, f" ssh-ed25519 256 {FINGERPRINT} root@panel ", MD5_FINGERPRINT.upper()],
)
async def test_matching_expected_fingerprint_skips_the_confirm_step(
    hass, panel: MagicMock, releases: MagicMock, expected: str
) -> None:
    result = await hass.config_entries.flow.async_configure(
        (await start(hass))["flow_id"], {**USER_INPUT, CONF_EXPECTED_FINGERPRINT: expected}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST_KEY] == FINGERPRINT  # the scanned SHA256 is what gets pinned


async def test_wrong_expected_fingerprint_never_reaches_the_password(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_configure(
        (await start(hass))["flow_id"],
        {**USER_INPUT, CONF_EXPECTED_FINGERPRINT: "SHA256:somethingelse"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "host_key_mismatch"}
    panel.flow_panel_cls.assert_not_called()
    panel.connect.assert_not_awaited()
    # The password is not echoed back into the form.
    assert CONF_PASSWORD not in _suggested(result)
    assert _suggested(result)[CONF_HOST] == HOST


async def test_user_flow_detects_brilliant_mqtt(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "bm1"}
    result = await connect(hass)
    assert result["options"] == {
        CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
        CONF_MQTT_DEVICE_ID: "bm1",
        CONF_MANAGE_INSTALLS: False,
    }


async def test_unique_id_falls_back_to_fingerprint(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.bus_peripherals.side_effect = SetupError("no bus")
    result = await connect(hass)
    assert result["result"].unique_id == FINGERPRINT
    assert result["data"][CONF_NODE] is None


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (CannotConnect("x"), "cannot_connect"),
        (AuthFailed("x"), "invalid_auth"),
        (HostKeyMismatch("x"), "host_key_mismatch"),
        (PanelError("x"), "unknown"),
        (RuntimeError("x"), "unknown"),
    ],
)
async def test_login_errors_return_to_the_form_then_recover(
    hass, panel: MagicMock, releases: MagicMock, error: Exception, key: str
) -> None:
    panel.connect.side_effect = error
    result = await connect(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": key}
    assert CONF_PASSWORD not in _suggested(result)
    panel.connect.side_effect = None
    result = await submit(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (CannotConnect("x"), "cannot_connect"),
        (RuntimeError("x"), "unknown"),
    ],
)
async def test_host_key_scan_errors(
    hass, panel: MagicMock, releases: MagicMock, error: Exception, key: str
) -> None:
    panel.fetch_host_key.side_effect = error
    result = await connect(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    panel.flow_panel_cls.assert_not_called()


async def test_duplicate_aborts_and_updates_host(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await connect(hass, {**USER_INPUT, CONF_HOST: "192.168.1.99"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.99"


# -- first install ------------------------------------------------------------------------


@pytest.fixture
def bare_panel(panel: MagicMock, releases: MagicMock, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    panel.setup_status.return_value = SetupStatus(state="not_installed")
    monkeypatch.setattr(
        "custom_components.brilliant_kiosk.config_flow.kiosk_env_from_bundle",
        lambda bundle, overrides: "".join(f'{k}="{v}"\n' for k, v in overrides.items()),
    )
    return panel


INSTALL_INPUT = {
    CONF_INSTALL: True,
    CONF_HA_URL: "http://ha.local:8123",
    CONF_TOKEN: " " + "t" * 40 + " ",
    CONF_MQTT_HOST: "broker.local",
    CONF_MQTT_USER: "mqttuser",
    CONF_MQTT_PASSWORD: "mqttpw",
}


async def install(hass: HomeAssistant, flow_id: str, user_input):
    """Submit the install form and wait for the background install, like the frontend does."""
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    if result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
    return result


async def to_install_step(hass: HomeAssistant):
    result = await connect(hass)
    assert result["step_id"] == "install"
    return result


async def test_install_step_installs_and_creates_entry(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    releases.list.assert_any_await(include_prereleases=False)
    releases.download.assert_awaited_with(STABLE)
    args, kwargs = bare_panel.install.await_args
    assert args == (b"bundle", "a" * 64)
    assert kwargs["token"] == "t" * 40
    env = kwargs["env"]
    assert 'HA_URL="http://ha.local:8123"\n' in env
    assert 'KIOSK_MQTT="1"\n' in env
    assert 'MQTT_HOST="broker.local"\n' in env
    assert 'MQTT_USER="mqttuser"\n' in env
    assert 'MQTT_PASSWORD="mqttpw"\n' in env
    assert result["options"][CONF_MQTT_PROVIDER] == PROVIDER_BUILTIN
    bare_panel.close.assert_awaited()


async def test_install_shows_progress_while_downloading_and_installing(
    hass, bare_panel, releases
) -> None:
    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_install(*args, **kwargs):
        started.set()
        await gate.wait()
        return SetupStatus(state="active")

    bare_panel.install.side_effect = slow_install
    result = await to_install_step(hass)
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, INSTALL_INPUT)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "installing"
    assert result["progress_action"] == "installing"
    await asyncio.wait_for(started.wait(), 5)
    assert not hass.config_entries.async_entries(DOMAIN)  # no entry before the install finished

    gate.set()
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_MQTT_PROVIDER] == PROVIDER_BUILTIN
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_install_failure_after_progress_returns_to_the_form(
    hass, bare_panel, releases
) -> None:
    gate = asyncio.Event()

    async def failing_install(*args, **kwargs):
        await gate.wait()
        raise SetupError("activation failed")

    bare_panel.install.side_effect = failing_install
    result = await to_install_step(hass)
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, INSTALL_INPUT)
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    gate.set()
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "install"
    assert result["errors"] == {"base": "install_failed"}
    assert result["description_placeholders"] == {"error": "activation failed"}
    # What the user typed is kept (except secrets); retrying works.
    assert _defaults(result)[CONF_HA_URL] == "http://ha.local:8123"
    assert _defaults(result)[CONF_MQTT_HOST] == "broker.local"
    assert CONF_MQTT_PASSWORD not in _defaults(result)
    assert not hass.config_entries.async_entries(DOMAIN)
    bare_panel.install.side_effect = None
    result = await install(hass, flow_id, INSTALL_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_aborting_the_flow_cancels_a_running_install(hass, bare_panel, releases) -> None:
    gate = asyncio.Event()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging_install(*args, **kwargs):
        started.set()
        try:
            await gate.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    bare_panel.install.side_effect = hanging_install
    result = await to_install_step(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await asyncio.wait_for(started.wait(), 5)
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    assert cancelled.is_set()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_step_minimal_mqtt_credentials(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    minimal = {
        k: v for k, v in INSTALL_INPUT.items() if k not in (CONF_MQTT_USER, CONF_MQTT_PASSWORD)
    }
    result = await install(hass, result["flow_id"], minimal)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    env = bare_panel.install.await_args.kwargs["env"]
    assert "MQTT_USER" not in env
    assert "MQTT_PASSWORD" not in env


async def test_install_form_does_not_prefill_the_mqtt_password(hass, bare_panel, releases) -> None:
    MockConfigEntry(
        domain="mqtt",
        data={"broker": "core-mosquitto", "username": "u", "password": "brokerpw"},
    ).add_to_hass(hass)
    result = await to_install_step(hass)
    defaults = _defaults(result)
    assert defaults[CONF_MQTT_HOST] == "core-mosquitto"
    assert defaults[CONF_MQTT_USER] == "u"
    assert "brokerpw" not in str(result["data_schema"].schema) + str(defaults) + str(
        _suggested(result)
    )
    assert CONF_MQTT_PASSWORD not in defaults


async def test_install_reuses_the_brokers_password_server_side(hass, bare_panel, releases) -> None:
    MockConfigEntry(
        domain="mqtt",
        data={"broker": "core-mosquitto", "username": "u", "password": "brokerpw"},
    ).add_to_hass(hass)
    result = await to_install_step(hass)
    submitted = {
        k: v for k, v in INSTALL_INPUT.items() if k not in (CONF_MQTT_USER, CONF_MQTT_PASSWORD)
    }
    submitted |= {CONF_MQTT_HOST: "core-mosquitto", CONF_MQTT_USER: "u"}
    result = await install(hass, result["flow_id"], submitted)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert 'MQTT_PASSWORD="brokerpw"\n' in bare_panel.install.await_args.kwargs["env"]


@pytest.mark.parametrize(
    ("host", "user"),
    [("other.broker", "u"), ("core-mosquitto", "someone-else"), ("core-mosquitto", "")],
)
async def test_broker_password_is_not_sent_to_a_different_broker_or_user(
    hass, bare_panel, releases, host: str, user: str
) -> None:
    MockConfigEntry(
        domain="mqtt",
        data={"broker": "core-mosquitto", "username": "u", "password": "brokerpw"},
    ).add_to_hass(hass)
    result = await to_install_step(hass)
    submitted = {
        k: v for k, v in INSTALL_INPUT.items() if k not in (CONF_MQTT_USER, CONF_MQTT_PASSWORD)
    }
    submitted |= {CONF_MQTT_HOST: host, CONF_MQTT_USER: user}
    await install(hass, result["flow_id"], submitted)
    assert "brokerpw" not in bare_panel.install.await_args.kwargs["env"]


async def test_install_step_skip(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**INSTALL_INPUT, CONF_INSTALL: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    bare_panel.install.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (InvalidArgument("quote"), "invalid_value"),
        (SettingsRejected("bad"), "settings_rejected"),
        (SetupError("boom"), "install_failed"),
        (CannotConnect("gone"), "cannot_connect"),
        (AuthFailed("x"), "invalid_auth"),
        (RuntimeError("weird"), "unknown"),
    ],
)
async def test_install_step_errors(hass, bare_panel, releases, error: Exception, key: str) -> None:
    result = await to_install_step(hass)
    bare_panel.install.side_effect = error
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    # The URL typed by the user is kept when the form is shown again.
    assert _defaults(result)[CONF_HA_URL] == "http://ha.local:8123"
    bare_panel.install.side_effect = None
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_install_step_no_stable_release(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    releases.list = AsyncMock(return_value=())
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["errors"] == {"base": "install_failed"}
    assert "no stable release" in result["description_placeholders"]["error"]


async def test_install_step_release_download_error(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    releases.download.side_effect = ReleaseError("404")
    result = await install(hass, result["flow_id"], INSTALL_INPUT)
    assert result["errors"] == {"base": "unknown"}
    assert result["description_placeholders"] == {"error": "404"}


async def test_internal_url_missing_is_blank(
    hass, bare_panel, releases, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_url(*args: object, **kwargs: object) -> str:
        raise NoURLAvailableError

    monkeypatch.setattr("custom_components.brilliant_kiosk.config_flow.get_url", no_url)
    result = await to_install_step(hass)
    assert _defaults(result)[CONF_HA_URL] == ""


# -- reauth -------------------------------------------------------------------------------


async def test_reauth_flow(hass, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    panel.connect.side_effect = AuthFailed("no")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    panel.connect.side_effect = RuntimeError("weird")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "bad"}
    )
    assert result["errors"] == {"base": "unknown"}

    panel.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.state is ConfigEntryState.LOADED
    # Reauth only ever talks to the pinned key.
    assert panel.flow_panel_cls.call_args.kwargs["host_key"] == FINGERPRINT


# -- reconfigure --------------------------------------------------------------------------


async def test_reconfigure_flow(hass, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    panel.connect.side_effect = HostKeyMismatch("changed")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["errors"] == {"base": "host_key_mismatch"}

    panel.connect.side_effect = RuntimeError("weird")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["errors"] == {"base": "unknown"}

    panel.fetch_host_key.side_effect = CannotConnect("down")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    panel.fetch_host_key.side_effect = None
    panel.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.9"
    assert entry.data[CONF_HOST_KEY] == FINGERPRINT


async def test_reconfigure_aborts_when_the_host_key_differs(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    from pybrilliantkiosk import HostKeyInfo

    entry = make_entry()
    await setup_entry(hass, entry)
    panel.flow_panel_cls.reset_mock()
    panel.fetch_host_key.return_value = HostKeyInfo("SHA256:attacker", "MD5:00")
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "host_key_changed"
    assert entry.data[CONF_HOST] == HOST
    assert entry.data[CONF_HOST_KEY] == FINGERPRINT
    # The stored password was never sent to the host presenting the other key.
    panel.flow_panel_cls.assert_not_called()


async def test_reconfigure_rejects_a_different_panel(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.bus_peripherals.return_value = type(panel.bus_peripherals.return_value)(
        "ffffffffffffffffffff", ()
    )
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_HOST] == HOST


# -- discovery ----------------------------------------------------------------------------


def mqtt_info(topic: str) -> MqttServiceInfo:
    return MqttServiceInfo(
        topic=topic, payload="active", qos=0, retain=True, subscribed_topic="x", timestamp=0.0
    )


async def test_mqtt_discovery_goes_to_user_step(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=mqtt_info(f"brilliant_kiosk/{NODE}/kiosk_state"),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await submit(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == NODE


async def test_mqtt_discovery_ignores_configured_and_bad_topics(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=mqtt_info(f"brilliant_kiosk/{NODE}/kiosk_state"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_MQTT}, data=mqtt_info("kiosk_state")
    )
    assert result["reason"] == "unknown"


# -- options (the flow only stores; the update listener writes to the panel) -------------------


async def submit_options(hass: HomeAssistant, entry: MockConfigEntry, user_input: dict):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


async def test_options_flow_validates_the_device_id(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    result = await submit_options(
        hass,
        entry,
        {
            CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
            CONF_MQTT_DEVICE_ID: "",
            CONF_MANAGE_INSTALLS: False,
        },
    )
    assert result["errors"] == {CONF_MQTT_DEVICE_ID: "device_id_required"}
    panel.set_env.assert_not_awaited()
    assert entry.options[CONF_MQTT_PROVIDER] == PROVIDER_BUILTIN


async def test_options_flow_does_not_touch_the_panel_itself(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    """The SSH write must not run inside the flow (it would block the dialog)."""
    entry = make_entry()
    await setup_entry(hass, entry)
    started = asyncio.Event()
    gate = asyncio.Event()

    async def slow_set_env(*args, **kwargs):
        started.set()
        await gate.wait()
        return ("KIOSK_MQTT",)

    panel.set_env.side_effect = slow_set_env
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
            CONF_MQTT_DEVICE_ID: "bm1",
            CONF_MANAGE_INSTALLS: False,
        },
    )
    # The flow already finished and stored the options while the panel write is still pending.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MQTT_PROVIDER] == PROVIDER_BRILLIANT_MQTT
    await asyncio.wait_for(started.wait(), 5)
    gate.set()
    await hass.async_block_till_done()
    panel.restart.assert_awaited_with("ha-kiosk-bridge")


async def test_options_switch_to_brilliant_mqtt(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    first = entry.runtime_data
    result = await submit_options(
        hass,
        entry,
        {
            CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
            CONF_MQTT_DEVICE_ID: " bm1 ",
            CONF_MANAGE_INSTALLS: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    panel.set_env.assert_awaited_with({"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "bm1"}, ())
    panel.restart.assert_awaited_with("ha-kiosk-bridge")
    assert entry.options[CONF_MQTT_PROVIDER] == PROVIDER_BRILLIANT_MQTT
    assert entry.options[CONF_MQTT_DEVICE_ID] == "bm1"
    assert entry.options[CONF_MANAGE_INSTALLS] is False
    assert entry.runtime_data is not first  # reloaded with the new provider
    assert entry.runtime_data.mqtt_provider == PROVIDER_BRILLIANT_MQTT


async def test_options_back_to_builtin_never_writes_off(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "bm1"}
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, CONF_MQTT_DEVICE_ID: "bm1"})
    await setup_entry(hass, entry)
    result = await submit_options(
        hass,
        entry,
        {CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, CONF_MQTT_DEVICE_ID: "", CONF_MANAGE_INSTALLS: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    panel.set_env.assert_awaited_with({"KIOSK_MQTT": "1"}, ("MQTT_DEVICE_ID",))


async def test_options_unchanged_provider_does_not_touch_the_panel(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    first = entry.runtime_data
    await submit_options(
        hass,
        entry,
        {CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, CONF_MQTT_DEVICE_ID: "", CONF_MANAGE_INSTALLS: True},
    )
    panel.set_env.assert_not_awaited()
    assert entry.runtime_data is first


async def test_options_builtin_without_device_id_and_no_change_on_panel(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, CONF_MQTT_DEVICE_ID: "bm1"})
    await setup_entry(hass, entry)
    panel.set_env.return_value = ()  # the panel already has these settings
    await submit_options(
        hass,
        entry,
        {CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, CONF_MQTT_DEVICE_ID: "", CONF_MANAGE_INSTALLS: True},
    )
    panel.set_env.assert_awaited_with({"KIOSK_MQTT": "1"}, ())
    panel.restart.assert_not_awaited()


async def test_options_abort_when_not_loaded(hass, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    assert entry.state is ConfigEntryState.NOT_LOADED
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"
