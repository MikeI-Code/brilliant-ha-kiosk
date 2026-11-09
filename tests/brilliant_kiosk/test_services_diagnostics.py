"""Actions and diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.brilliant_kiosk.const import DOMAIN
from homeassistant.auth.models import User
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pybrilliantkiosk import (
    BusVariable,
    CannotConnect,
    HostKeyMismatch,
    InvalidArgument,
    SetupBusy,
    SetupStatus,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from .conftest import (
    FINGERPRINT,
    HOST,
    NIGHTLY,
    NODE,
    OLD,
    STABLE,
    UNIQUE_ID,
    make_entry,
    setup_entry,
)


async def call(hass: HomeAssistant, name: str, data: dict, response: bool = False):
    return await hass.services.async_call(
        DOMAIN, name, data, blocking=True, return_response=response
    )


async def loaded(hass, panel, releases, **options) -> MockConfigEntry:
    entry = make_entry(**options)
    await setup_entry(hass, entry)
    return entry


async def test_set_env(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    entry = await loaded(hass, panel, releases)
    await call(
        hass,
        "set_env",
        {
            "config_entry_id": entry.entry_id,
            "values": {"DAY_LEVEL": 9, "SCREENSAVER": False, "SMART_DIM": True, "HA_ZOOM": 1.5},
            "delete": ["PHOTO_DIRS"],
            "restart": True,
        },
    )
    panel.set_env.assert_awaited_with(
        {"DAY_LEVEL": "9", "SCREENSAVER": "0", "SMART_DIM": "1", "HA_ZOOM": "1.5"},
        ["PHOTO_DIRS"],
        restart=True,
    )


async def test_set_env_with_an_unsafe_running_value_is_a_translated_error_naming_the_keys(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    """The library refuses to revert a hand-set value; the action says which keys and why."""
    entry = await loaded(hass, panel, releases)
    panel.set_env.side_effect = InvalidArgument(
        "the running kiosk.env has values that cannot be written safely (HA_URL); "
        "nothing was changed. Fix them on the panel."
    )
    with pytest.raises(ServiceValidationError, match=r"cannot be written safely.*HA_URL") as err:
        await call(hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"DAY_LEVEL": 9}})
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "invalid_argument"


async def test_set_env_refreshes_only_when_something_changed(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    refresh = AsyncMock()
    entry.runtime_data.async_request_refresh = refresh

    panel.set_env.return_value = ()
    await call(hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"DAY_LEVEL": 9}})
    panel.set_env.assert_awaited_once()
    refresh.assert_not_awaited()

    panel.set_env.return_value = ("DAY_LEVEL",)
    await call(hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"DAY_LEVEL": 8}})
    refresh.assert_awaited_once()


async def test_busy_panel_is_reported_as_a_translated_error(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    panel.set_env.side_effect = SetupBusy("a job is running")
    with pytest.raises(HomeAssistantError, match="busy running ha-kiosk-setup: a job is running"):
        await call(hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"DAY_LEVEL": 9}})


@pytest.mark.parametrize("key", ["KIOSK_MQTT", "MQTT_DEVICE_ID"])
@pytest.mark.parametrize("value", ["0", "off", "1", "kiosk", ""])
async def test_set_env_refuses_the_mqtt_provider_keys(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, key: str, value: str
) -> None:
    """The provider is an option: set_env would leave the options and the panel disagreeing."""
    entry = await loaded(hass, panel, releases)
    with pytest.raises(ServiceValidationError, match="Settings > Devices & services") as err:
        await call(hass, "set_env", {"config_entry_id": entry.entry_id, "values": {key: value}})
    assert err.value.translation_key == "mqtt_set_in_options"
    panel.set_env.assert_not_awaited()


@pytest.mark.parametrize("key", ["KIOSK_MQTT", "MQTT_DEVICE_ID"])
async def test_set_env_refuses_deleting_the_mqtt_provider_keys_and_empty_calls(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, key: str
) -> None:
    entry = await loaded(hass, panel, releases)
    with pytest.raises(ServiceValidationError, match="Configure"):
        await call(hass, "set_env", {"config_entry_id": entry.entry_id, "delete": [key]})
    with pytest.raises(ServiceValidationError, match="at least one"):
        await call(hass, "set_env", {"config_entry_id": entry.entry_id})
    panel.set_env.assert_not_awaited()


async def test_set_env_still_writes_other_settings_next_to_the_mqtt_ones(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    await call(hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"DIM_LEVEL": 3}})
    panel.set_env.assert_awaited_with({"DIM_LEVEL": "3"}, [], restart=False)


async def test_get_env(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    entry = await loaded(hass, panel, releases)
    result = await call(hass, "get_env", {"config_entry_id": entry.entry_id}, response=True)
    assert result["env"]["DIM_LEVEL"] == "4"
    assert result["env"]["MQTT_PASSWORD"] == "********"


async def test_bus_get_and_set(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    entry = await loaded(hass, panel, releases)
    panel.bus_get.return_value = (BusVariable("a", "1", True),)
    result = await call(
        hass,
        "bus_get",
        {"config_entry_id": entry.entry_id, "peripheral": "p"},
        response=True,
    )
    assert result == {"variables": [{"name": "a", "value": "1", "read_only": True}]}
    await call(
        hass,
        "bus_set",
        {"config_entry_id": entry.entry_id, "peripheral": "p", "values": {"a": 5, "b": True}},
    )
    panel.bus_set.assert_awaited_with("p", {"a": 5, "b": True})


async def test_logs(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    entry = await loaded(hass, panel, releases)
    result = await call(
        hass,
        "logs",
        {"config_entry_id": entry.entry_id, "unit": "ha-kiosk.service", "lines": 5},
        response=True,
    )
    assert result == {"logs": "line1\nline2"}
    panel.logs.assert_awaited_with("ha-kiosk.service", 5)
    with pytest.raises(Exception, match="value must be one of"):
        await call(
            hass,
            "logs",
            {"config_entry_id": entry.entry_id, "unit": "sshd.service"},
            response=True,
        )


async def test_install_version(hass: HomeAssistant, panel: MagicMock, releases: MagicMock) -> None:
    entry = await loaded(hass, panel, releases)
    await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": STABLE.tag})
    releases.download.assert_awaited_with(STABLE, sha256="a" * 64, size=6)
    panel.install.assert_awaited_with(b"bundle", "a" * 64, size=6)
    with pytest.raises(ServiceValidationError, match="No kiosk release"):
        await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": "nope"})


async def test_install_version_accepts_the_tag_or_the_bundle_version(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    for name in (OLD.tag, OLD.version):
        releases.download.reset_mock()
        await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": name})
        releases.download.assert_awaited_once_with(OLD, sha256="b" * 64, size=6)


async def test_install_version_refuses_a_stable_release_it_cannot_verify(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("custom_components.brilliant_kiosk.install.PINNED_RELEASES", {})
    entry = await loaded(hass, panel, releases)
    with pytest.raises(ServiceValidationError, match="list of verified releases") as err:
        await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": STABLE.tag})
    assert err.value.translation_key == "release_not_pinned"
    releases.download.assert_not_awaited()  # nothing was even downloaded
    panel.install.assert_not_awaited()


async def test_install_version_nightly_needs_the_nightly_channel(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    with pytest.raises(ServiceValidationError, match="nightly") as err:
        await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": NIGHTLY.tag})
    assert err.value.translation_key == "nightly_not_allowed"
    releases.download.assert_not_awaited()

    await hass.config_entries.async_remove(entry.entry_id)  # same panel: one entry at a time
    nightly_entry = await loaded(hass, panel, releases, channel="nightly")
    await call(
        hass, "install_version", {"config_entry_id": nightly_entry.entry_id, "tag": NIGHTLY.tag}
    )
    releases.download.assert_awaited_once_with(NIGHTLY)  # its own .sha256 only: no pinned hash
    panel.install.assert_awaited_with(b"bundle", "a" * 64, size=6)


def test_logs_service_offers_exactly_the_panel_units() -> None:
    """The unit field is a select of the allow-listed units (no free text to get wrong)."""
    import yaml
    from custom_components import brilliant_kiosk
    from pybrilliantkiosk import UNITS

    path = Path(brilliant_kiosk.__file__).parent / "services.yaml"
    services = yaml.safe_load(path.read_text(encoding="utf-8"))
    selector = services["logs"]["fields"]["unit"]["selector"]
    assert selector == {"select": {"options": list(UNITS)}}


async def test_install_version_requires_managed_installs(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases, manage_installs=False)
    with pytest.raises(ServiceValidationError, match="Manage installs"):
        await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": STABLE.tag})


async def test_action_errors_are_translated(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    panel.bus_set.side_effect = CannotConnect("gone")
    with pytest.raises(HomeAssistantError, match="Cannot connect to the panel: gone"):
        await call(
            hass,
            "bus_set",
            {"config_entry_id": entry.entry_id, "peripheral": "p", "values": {"a": 1}},
        )
    panel.get_settings.side_effect = HostKeyMismatch("x")
    with pytest.raises(HomeAssistantError, match="host key"):
        await call(hass, "get_env", {"config_entry_id": entry.entry_id}, response=True)
    panel.install.side_effect = ValueError  # not a PanelError: not translated
    with pytest.raises(ValueError):
        await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": STABLE.tag})


async def test_unknown_and_unloaded_entries(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    with pytest.raises(ServiceValidationError, match="No Brilliant Kiosk panel"):
        await call(hass, "get_env", {"config_entry_id": "nope"}, response=True)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    with pytest.raises(ServiceValidationError, match="No Brilliant Kiosk panel"):
        await call(hass, "get_env", {"config_entry_id": other.entry_id}, response=True)
    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError, match="is not loaded"):
        await call(hass, "get_env", {"config_entry_id": entry.entry_id}, response=True)


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, hass_client
) -> None:
    panel.get_settings.return_value = {
        "KIOSK_MQTT": "kiosk",
        "MQTT_DEVICE_ID": "secretdev",
        "MQTT_PASSWORD": "hunter2",
        "HA_URL": "http://ha.local:8123",
        "DIM_LEVEL": "4",
    }
    entry = await loaded(hass, panel, releases)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["entry"]["password"] == "**REDACTED**"
    assert diag["entry"]["host_key"] == "**REDACTED**"
    assert diag["entry"]["host"] == "**REDACTED**"
    assert diag["entry"]["node"] == "**REDACTED**"
    assert diag["settings"]["MQTT_PASSWORD"] == "**REDACTED**"
    assert diag["settings"]["MQTT_DEVICE_ID"] == "**REDACTED**"
    assert diag["settings"]["HA_URL"] == "**REDACTED**"
    assert diag["settings"]["DIM_LEVEL"] == "4"
    assert diag["options"]["mqtt_provider"] == "builtin"
    assert diag["health"]["version"] == "v1.4.0"
    assert diag["setup"]["state"] == "active"
    assert diag["releases"] == ["nightly-20261108", "v1.5.0", "v1.4.0"]
    assert "hunter2" not in str(diag)


BROKER = "mqtt-broker.example.net"
BROKER_USER = "broker-login"


async def test_diagnostics_contain_nothing_that_identifies_the_panel_or_its_broker(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, hass_client
) -> None:
    panel.get_settings.return_value = {
        "KIOSK_MQTT": "1",
        "MQTT_HOST": BROKER,
        "MQTT_USER": BROKER_USER,
        "MQTT_PASSWORD": "********",
        "MQTT_BASE": "brilliant_kiosk",
        "MQTT_DEVICE_ID": "secretdev1",
        "HA_URL": "http://10.1.2.3:8123",
        "VOICE_ALLOW": "10.1.2.4,10.1.2.5",
        "CAPTURE_ALLOW": "10.1.2.6",
        "VOICE_BIND": "0.0.0.0",  # noqa: S104
        "ROOM_LIGHTS": "light.kitchen_ceiling",
        "DIM_LEVEL": "4",
        "SLEEP_START": "21:00",
    }
    panel.setup_status.return_value = SetupStatus(
        state="failed",
        version="v1.4.0",
        mqtt_mode="1",
        mqtt_base=f"{NODE}/base",
        last_result={
            "message": f"cannot reach {BROKER} from {HOST} (node {NODE}) as {BROKER_USER}",
            "ok": False,
        },
    )
    entry = await loaded(hass, panel, releases)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(diag)
    for private in (
        HOST,
        NODE,
        UNIQUE_ID,
        FINGERPRINT,
        BROKER,
        BROKER_USER,
        "secretdev1",
        "10.1.2.3",
        "10.1.2.4",
        "10.1.2.6",
        "light.kitchen_ceiling",
        "root",
        "pw",
    ):
        assert private not in text, private
    # What is useful for support stays.
    settings = diag["settings"]
    assert settings["DIM_LEVEL"] == "4"
    assert settings["SLEEP_START"] == "21:00"
    assert settings["KIOSK_MQTT"] == "1"
    for key in ("MQTT_HOST", "MQTT_USER", "MQTT_BASE", "VOICE_ALLOW", "CAPTURE_ALLOW", "HA_URL"):
        assert settings[key] == "**REDACTED**"
    assert diag["setup"]["state"] == "failed"
    assert diag["setup"]["mqtt_base"] == "**REDACTED**"
    assert diag["setup"]["last_result"]["message"] == "**REDACTED**"
    assert diag["setup"]["last_result"]["ok"] is False


async def test_diagnostics_scrub_identifiers_out_of_free_text(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, hass_client
) -> None:
    """A message in a field that is not redacted by name is scrubbed by value."""
    panel.setup_status.return_value = SetupStatus(
        state="failed",
        firmware=f"fw for {HOST} and 172.16.5.9 or fe80::1:2:3:4 or 2001:db8:0:0:0:0:0:1 at 12:30:45",
    )
    entry = await loaded(hass, panel, releases)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    firmware = diag["setup"]["firmware"]
    assert HOST not in firmware
    assert "172.16.5.9" not in firmware
    assert "fe80" not in firmware
    assert "2001:db8" not in firmware
    assert firmware.endswith(" at 12:30:45")  # a time of day is not an address
    assert firmware.startswith("fw for ")


async def test_diagnostics_scrub_only_real_identifiers_not_common_words(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, hass_client
) -> None:
    """A login called "admin" or a base topic "brilliant_kiosk" must not eat those words."""
    panel.get_settings.return_value = {
        "MQTT_USER": "admin",
        "MQTT_BASE": "brilliant_kiosk",
        "MQTT_HOST": "mqtt",
        "MQTT_DEVICE_ID": "abc",  # shorter than any identifier worth scrubbing
    }
    panel.setup_status.return_value = SetupStatus(
        state="active", firmware="admin console, mqtt and brilliant_kiosk with abc"
    )
    entry = await loaded(hass, panel, releases)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["setup"]["firmware"] == "admin console, mqtt and brilliant_kiosk with abc"
    for key in ("MQTT_USER", "MQTT_BASE", "MQTT_HOST", "MQTT_DEVICE_ID"):
        assert diag["settings"][key] == "**REDACTED**"  # by name, whatever the value


async def test_diagnostics_scrub_whole_tokens_only(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, hass_client
) -> None:
    panel.get_settings.return_value = {"MQTT_USER": "login42", "MQTT_HOST": "broker-7.example.net"}
    panel.setup_status.return_value = SetupStatus(
        state="failed",
        firmware="login42 failed; login425 and mylogin42x are other words; (login42), broker-7.example.net.",
    )
    entry = await loaded(hass, panel, releases)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["setup"]["firmware"] == (
        "**REDACTED** failed; login425 and mylogin42x are other words; (**REDACTED**), "
        "**REDACTED**."
    )


# -- a first install happens only in the config flow ---------------------------------------------


async def test_install_version_refuses_a_panel_without_the_kiosk(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    """Without env and token a first install fails slowly and restores the stock UI."""
    panel.setup_status.return_value = SetupStatus(state="not_installed")
    entry = await loaded(hass, panel, releases)
    for tag in (STABLE.tag, "no-such-release"):  # the refusal comes before any lookup
        with pytest.raises(ServiceValidationError, match="not installed on this panel") as err:
            await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": tag})
        assert err.value.translation_key == "not_installed"
    releases.download.assert_not_awaited()
    panel.install.assert_not_awaited()


async def test_install_version_still_works_once_the_kiosk_is_there(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    panel.setup_status.return_value = SetupStatus(state="not_installed")
    entry = await loaded(hass, panel, releases)
    panel.setup_status.return_value = SetupStatus(state="unmanaged")  # installed by hand
    await entry.runtime_data.async_refresh()
    await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": STABLE.tag})
    panel.install.assert_awaited_once()


# -- the logs action scrubs what the panel's journal says ----------------------------------------

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJhYmMxMjMifQ.c2lnbmF0dXJlc2lnbmF0dXJl"


async def test_logs_are_scrubbed_like_the_diagnostics_and_of_tokens(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    panel.logs.return_value = "\n".join(
        [
            f"connecting to {HOST} as {NODE}",  # the panel's own address and node
            "peer 10.1.2.3 and fe80::1:2:3 seen",  # any address
            f"Authorization: Bearer {TOKEN}",
            f"retry with token={TOKEN} now",
            "mqtt password: hunter2hunter2 rejected",
            "api_key = abcd1234",
            "key 0123456789abcdef0123456789abcdef0123456789abcdef used",
            "token expired, ordinary words stay",
            "12:30:45 DIM_LEVEL=4 ok",
        ]
    )
    result = await call(
        hass, "logs", {"config_entry_id": entry.entry_id, "unit": "ha-kiosk.service"}, response=True
    )
    text = result["logs"]
    for secret in (HOST, NODE, "10.1.2.3", "fe80::1:2:3", TOKEN, "hunter2hunter2", "abcd1234"):
        assert secret not in text
    assert "0123456789abcdef0123456789abcdef" not in text
    assert "Authorization: **REDACTED**" in text  # the name stays, the value goes
    assert "token expired, ordinary words stay" in text
    assert "12:30:45 DIM_LEVEL=4 ok" in text
    assert text.count("**REDACTED**") >= 8


async def test_logs_scrub_the_identifiers_of_the_panels_own_settings(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    panel.get_settings.return_value = {"MQTT_HOST": "broker.lan", "MQTT_USER": "mqttuser1"}
    entry = await loaded(hass, panel, releases)
    panel.logs.return_value = "connect broker.lan as mqttuser1 failed"
    result = await call(
        hass, "logs", {"config_entry_id": entry.entry_id, "unit": "ha-kiosk.service"}, response=True
    )
    assert result == {"logs": "connect **REDACTED** as **REDACTED** failed"}


# -- the actions are for administrators -----------------------------------------------------------

ACTIONS = {
    "set_env": {"values": {"DAY_LEVEL": 9}},
    "get_env": {},
    "bus_get": {"peripheral": "p"},
    "bus_set": {"peripheral": "p", "values": {"a": 1}},
    "logs": {"unit": "ha-kiosk.service"},
    "install_version": {"tag": "v1.5.0"},
}


@pytest.mark.parametrize("name", sorted(ACTIONS))
async def test_a_user_who_is_not_an_administrator_cannot_call_the_actions(
    hass: HomeAssistant,
    panel: MagicMock,
    releases: MagicMock,
    hass_read_only_user: User,
    hass_admin_user: User,
    name: str,
) -> None:
    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    entry = await loaded(hass, panel, releases)
    data = {"config_entry_id": entry.entry_id, **ACTIONS[name]}
    response = name in {"get_env", "bus_get", "logs"}
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            name,
            data,
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
            return_response=response,
        )
    panel.set_env.assert_not_awaited()
    panel.install.assert_not_awaited()
    panel.bus_set.assert_not_awaited()
    panel.logs.assert_not_awaited()
    # An administrator, and an automation (no user), are let through.
    for context in (Context(user_id=hass_admin_user.id), Context()):
        await hass.services.async_call(
            DOMAIN, name, data, blocking=True, context=context, return_response=response
        )
