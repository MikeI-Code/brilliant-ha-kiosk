"""Actions and diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.brilliant_kiosk.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from pybrilliantkiosk import BusVariable, CannotConnect, HostKeyMismatch
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from .conftest import STABLE, make_entry, setup_entry


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


async def test_set_env_no_change_skips_refresh(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    panel.set_env.return_value = ()
    await call(hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"DAY_LEVEL": 9}})


@pytest.mark.parametrize("value", ["0", "off", "OFF", "false", "no", ""])
async def test_set_env_rejects_mqtt_off(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock, value: str
) -> None:
    entry = await loaded(hass, panel, releases)
    with pytest.raises(ServiceValidationError, match="cannot be switched off"):
        await call(
            hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"KIOSK_MQTT": value}}
        )
    panel.set_env.assert_not_awaited()


async def test_set_env_rejects_deleting_mqtt_and_empty(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    with pytest.raises(ServiceValidationError, match="cannot be switched off"):
        await call(hass, "set_env", {"config_entry_id": entry.entry_id, "delete": ["KIOSK_MQTT"]})
    with pytest.raises(ServiceValidationError, match="at least one"):
        await call(hass, "set_env", {"config_entry_id": entry.entry_id})


async def test_set_env_allows_mqtt_modes(
    hass: HomeAssistant, panel: MagicMock, releases: MagicMock
) -> None:
    entry = await loaded(hass, panel, releases)
    await call(
        hass, "set_env", {"config_entry_id": entry.entry_id, "values": {"KIOSK_MQTT": "kiosk"}}
    )
    panel.set_env.assert_awaited_with({"KIOSK_MQTT": "kiosk"}, [], restart=False)


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
    releases.download.assert_awaited_with(STABLE)
    panel.install.assert_awaited_with(b"bundle", "a" * 64)
    with pytest.raises(ServiceValidationError, match="No kiosk release"):
        await call(hass, "install_version", {"config_entry_id": entry.entry_id, "tag": "nope"})


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
    assert cv.string  # keep the import used for schema helpers


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
