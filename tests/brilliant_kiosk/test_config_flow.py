"""Config, reauth, reconfigure, options and discovery flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from custom_components.brilliant_kiosk.const import (
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

from .conftest import FINGERPRINT, HOST, NODE, STABLE, UNIQUE_ID, make_entry, setup_entry


def _defaults(result) -> dict[str, object]:
    return {
        str(k): k.default()
        for k in result["data_schema"].schema
        if getattr(k, "default", vol.UNDEFINED) is not vol.UNDEFINED
    }


USER_INPUT = {CONF_HOST: HOST, CONF_USERNAME: "root", CONF_PASSWORD: "pw"}


async def start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def test_user_flow_creates_entry(hass, panel: MagicMock, releases: MagicMock) -> None:
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Brilliant panel {HOST}"
    assert result["data"] == {
        **USER_INPUT,
        CONF_HOST_KEY: FINGERPRINT,
        CONF_NODE: NODE,
    }
    assert result["result"].unique_id == UNIQUE_ID
    assert result["options"] == {
        CONF_MQTT_PROVIDER: PROVIDER_BUILTIN,
        CONF_MQTT_DEVICE_ID: "",
        CONF_MANAGE_INSTALLS: True,
    }
    panel.close.assert_awaited()


async def test_user_flow_detects_brilliant_mqtt(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "bm1"}
    result = await hass.config_entries.flow.async_configure(
        (await start(hass))["flow_id"], USER_INPUT
    )
    assert result["options"] == {
        CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
        CONF_MQTT_DEVICE_ID: "bm1",
        CONF_MANAGE_INSTALLS: False,
    }


async def test_unique_id_falls_back_to_fingerprint(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.bus_peripherals.side_effect = SetupError("no bus")
    result = await hass.config_entries.flow.async_configure(
        (await start(hass))["flow_id"], USER_INPUT
    )
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
async def test_user_flow_errors_then_recovers(
    hass, panel: MagicMock, releases: MagicMock, error: Exception, key: str
) -> None:
    panel.connect.side_effect = error
    result = await hass.config_entries.flow.async_configure(
        (await start(hass))["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    panel.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_aborts_and_updates_host(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure(
        (await start(hass))["flow_id"], {**USER_INPUT, CONF_HOST: "192.168.1.99"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.99"


# -- first install ------------------------------------------------------------------------


@pytest.fixture
def bare_panel(panel: MagicMock, releases: MagicMock, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    panel.setup_status.return_value = SetupStatus(state="not_installed")
    monkeypatch.setattr(
        "custom_components.brilliant_kiosk.config_flow.kiosk_env_from_bundle",
        lambda bundle, overrides: "".join(f"{k}={v}\n" for k, v in overrides.items()),
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


async def to_install_step(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_configure(
        (await start(hass))["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "install"
    return result


async def test_install_step_installs_and_creates_entry(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    releases.list.assert_any_await(include_prereleases=False)
    releases.download.assert_awaited_with(STABLE)
    args, kwargs = bare_panel.install.await_args
    assert args == (b"bundle", "a" * 64)
    assert kwargs["token"] == "t" * 40
    env = kwargs["env"]
    assert "HA_URL=http://ha.local:8123\n" in env
    assert "KIOSK_MQTT=1\n" in env
    assert "MQTT_HOST=broker.local\n" in env
    assert "MQTT_USER=mqttuser\n" in env
    assert "MQTT_PASSWORD=mqttpw\n" in env
    assert result["options"][CONF_MQTT_PROVIDER] == PROVIDER_BUILTIN
    bare_panel.close.assert_awaited()


async def test_install_step_minimal_mqtt_credentials(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    minimal = {
        k: v for k, v in INSTALL_INPUT.items() if k not in (CONF_MQTT_USER, CONF_MQTT_PASSWORD)
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], minimal)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    env = bare_panel.install.await_args.kwargs["env"]
    assert "MQTT_USER" not in env


async def test_install_step_prefills_from_mqtt_entry(hass, bare_panel, releases) -> None:
    MockConfigEntry(
        domain="mqtt", data={"broker": "core-mosquitto", "username": "u", "password": "p"}
    ).add_to_hass(hass)
    result = await to_install_step(hass)
    defaults = _defaults(result)
    assert defaults[CONF_MQTT_HOST] == "core-mosquitto"
    assert defaults[CONF_MQTT_USER] == "u"


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
    ],
)
async def test_install_step_errors(hass, bare_panel, releases, error: Exception, key: str) -> None:
    result = await to_install_step(hass)
    bare_panel.install.side_effect = error
    result = await hass.config_entries.flow.async_configure(result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    # The URL typed by the user is kept when the form is shown again.
    defaults = _defaults(result)
    assert defaults[CONF_HA_URL] == "http://ha.local:8123"
    bare_panel.install.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], INSTALL_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_install_step_no_stable_release(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    releases.list = AsyncMock(return_value=())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], INSTALL_INPUT)
    assert result["errors"] == {"base": "install_failed"}


async def test_install_step_release_download_error(hass, bare_panel, releases) -> None:
    result = await to_install_step(hass)
    releases.download.side_effect = ReleaseError("404")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], INSTALL_INPUT)
    assert result["errors"] == {"base": "unknown"}


async def test_internal_url_missing_is_blank(
    hass, bare_panel, releases, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_url(*args: object, **kwargs: object) -> str:
        raise NoURLAvailableError

    monkeypatch.setattr("custom_components.brilliant_kiosk.config_flow.get_url", no_url)
    result = await to_install_step(hass)
    defaults = _defaults(result)
    assert defaults[CONF_HA_URL] == ""


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

    panel.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.9"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.9"


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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
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


# -- options ------------------------------------------------------------------------------


async def test_options_switch_to_brilliant_mqtt(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
            CONF_MQTT_DEVICE_ID: "",
            CONF_MANAGE_INSTALLS: False,
        },
    )
    assert result["errors"] == {CONF_MQTT_DEVICE_ID: "device_id_required"}

    panel.set_env.side_effect = CannotConnect("down")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT,
            CONF_MQTT_DEVICE_ID: "bm1",
            CONF_MANAGE_INSTALLS: False,
        },
    )
    assert result["errors"] == {"base": "cannot_connect"}

    panel.set_env.side_effect = None
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
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


async def test_options_back_to_builtin_never_writes_off(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"KIOSK_MQTT": "kiosk", "MQTT_DEVICE_ID": "bm1"}
    entry = make_entry(**{CONF_MQTT_PROVIDER: PROVIDER_BRILLIANT_MQTT, CONF_MQTT_DEVICE_ID: "bm1"})
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, CONF_MQTT_DEVICE_ID: "", CONF_MANAGE_INSTALLS: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    panel.set_env.assert_awaited_with({"KIOSK_MQTT": "1"}, ("MQTT_DEVICE_ID",))


async def test_options_builtin_without_device_id_and_no_change(
    hass, panel: MagicMock, releases: MagicMock
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    panel.set_env.return_value = ()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MQTT_PROVIDER: PROVIDER_BUILTIN, CONF_MQTT_DEVICE_ID: "", CONF_MANAGE_INSTALLS: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    panel.set_env.assert_awaited_with({"KIOSK_MQTT": "1"}, ())
    panel.restart.assert_not_awaited()


async def test_options_abort_when_not_loaded(hass, panel: MagicMock, releases: MagicMock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"
