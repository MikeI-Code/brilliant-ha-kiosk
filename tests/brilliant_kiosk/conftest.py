"""Fixtures: the pybrilliantkiosk library is mocked at its boundary (Panel and Releases)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.brilliant_kiosk.const import (
    CONF_HOST_KEY,
    CONF_MANAGE_INSTALLS,
    CONF_MQTT_DEVICE_ID,
    CONF_MQTT_PROVIDER,
    CONF_NODE,
    DOMAIN,
    PROVIDER_BUILTIN,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pybrilliantkiosk import (
    UNITS,
    BusListing,
    BusPeripheral,
    BusVariable,
    HostKeyInfo,
    PanelHealth,
    Release,
    SetupStatus,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

HOST = "192.168.1.50"
FINGERPRINT = "SHA256:abcdef"
MD5_FINGERPRINT = "MD5:aa:bb:cc:dd"
DEVICE_ID = "0123456789abcdef0123"
NODE = "brilliant_0123456789ab"
UNIQUE_ID = NODE

HEALTH = PanelHealth(
    uptime_seconds=1000.0,
    load_1=0.5,
    load_5=0.4,
    load_15=0.3,
    mem_total_kb=1000,
    mem_available_kb=400,
    version="v1.4.0",
    units={unit: ("active" if unit in UNITS[:4] else "inactive") for unit in UNITS},
)

SETTINGS = {
    "KIOSK_MQTT": "1",
    "DIM_LEVEL": "4",
    "MIN_LEVEL": "5",
    "MQTT_PASSWORD": "********",
}

STABLE = Release(
    tag="v1.5.0",
    prerelease=False,
    published_at=datetime(2026, 11, 1, tzinfo=UTC),
    body="Stable notes " + "x" * 300,
    bundle_url="https://example.invalid/stable.tar.gz",
    sha256_url="https://example.invalid/stable.tar.gz.sha256",
    version="v1.5.0",
)
NIGHTLY = Release(
    tag="nightly-20261108",
    prerelease=True,
    published_at=datetime(2026, 11, 8, tzinfo=UTC),
    body="",
    bundle_url="https://example.invalid/nightly.tar.gz",
    sha256_url=None,
    version="v1.5.0-nightly.20261108.g89ec239",
)
OLD = Release(
    tag="v1.4.0",
    prerelease=False,
    published_at=None,
    body="old",
    bundle_url="https://example.invalid/old.tar.gz",
    sha256_url=None,
    version="v1.4.0",
)


def make_panel() -> MagicMock:
    """A Panel double with sensible defaults."""
    panel = MagicMock(name="Panel")
    panel.host = HOST
    panel.connect = AsyncMock(return_value=FINGERPRINT)
    panel.close = AsyncMock()
    panel.health = AsyncMock(return_value=HEALTH)
    panel.get_settings = AsyncMock(return_value=dict(SETTINGS))
    panel.setup_status = AsyncMock(return_value=SetupStatus(state="active", version="v1.4.0"))
    panel.bus_peripherals = AsyncMock(return_value=BusListing(DEVICE_ID, (BusPeripheral("p", 1),)))
    panel.bus_get = AsyncMock(return_value=(BusVariable("a", "1", False),))
    panel.bus_set = AsyncMock(return_value=())
    panel.set_env = AsyncMock(return_value=("DIM_LEVEL",))
    panel.env_drift = AsyncMock(return_value=())
    panel.reconcile_env = AsyncMock(return_value=())
    panel.restart = AsyncMock()
    panel.reboot = AsyncMock()
    panel.logs = AsyncMock(return_value="line1\nline2")
    panel.install = AsyncMock(return_value=SetupStatus(state="active"))
    panel.setup_command = AsyncMock(return_value=SetupStatus(state="active"))
    return panel


@pytest.fixture
def panel() -> Generator[MagicMock]:
    """The one Panel double used by the coordinator and the config flow."""
    instance = make_panel()
    scan = AsyncMock(return_value=HostKeyInfo(FINGERPRINT, MD5_FINGERPRINT))
    with (
        patch("custom_components.brilliant_kiosk.coordinator.Panel", return_value=instance),
        patch(
            "custom_components.brilliant_kiosk.config_flow.Panel", return_value=instance
        ) as flow_panel,
        patch("custom_components.brilliant_kiosk.config_flow.fetch_host_key", scan),
    ):
        instance.fetch_host_key = scan  # the credential-less host key scan of the config flow
        instance.flow_panel_cls = flow_panel  # how the config flow built its (credentialed) Panel
        yield instance


@pytest.fixture
def releases() -> Generator[MagicMock]:
    """The Releases client double (listing and download)."""
    client = MagicMock(name="Releases")
    all_releases = (NIGHTLY, STABLE, OLD)

    async def _list(*, include_prereleases: bool = True) -> tuple[Release, ...]:
        return tuple(r for r in all_releases if include_prereleases or not r.prerelease)

    client.list = AsyncMock(side_effect=_list)
    client.download = AsyncMock(return_value=(b"bundle", "a" * 64))
    with (
        patch("custom_components.brilliant_kiosk.coordinator.Releases", return_value=client),
        patch("custom_components.brilliant_kiosk.config_flow.Releases", return_value=client),
    ):
        yield client


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components in every test."""


@pytest.fixture(autouse=True)
def _no_mqtt_broker() -> Generator[None]:
    """The manifest depends on mqtt for discovery; tests need no broker."""
    with patch("homeassistant.components.mqtt.async_setup", return_value=True):
        yield


def make_entry(**options: object) -> MockConfigEntry:
    """A configured panel."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Brilliant panel {HOST}",
        unique_id=UNIQUE_ID,
        data={
            CONF_HOST: HOST,
            CONF_USERNAME: "root",
            CONF_PASSWORD: "pw",
            CONF_HOST_KEY: FINGERPRINT,
            CONF_NODE: NODE,
        },
        options={
            CONF_MQTT_PROVIDER: PROVIDER_BUILTIN,
            CONF_MQTT_DEVICE_ID: "",
            CONF_MANAGE_INSTALLS: True,
            **options,
        },
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry and set it up."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entity_id(hass: HomeAssistant, platform: str, key: str) -> str | None:
    """Entity id for one of the panel's entities, by unique id."""
    from homeassistant.helpers import entity_registry as er

    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{UNIQUE_ID}_{key}")
