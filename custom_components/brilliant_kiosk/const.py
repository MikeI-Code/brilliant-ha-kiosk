"""Constants for the Brilliant Kiosk integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Final

DOMAIN: Final = "brilliant_kiosk"

CONF_HOST_KEY: Final = "host_key"
CONF_PRIVATE_KEY: Final = "private_key"
CONF_NEW_HOST_KEY: Final = "trust_new_key"
CONF_NODE: Final = "node"
CONF_HA_URL: Final = "ha_url"
CONF_TOKEN: Final = "token"  # noqa: S105
CONF_INSTALL: Final = "install"

CONF_CHANNEL: Final = "channel"
CONF_MQTT_PROVIDER: Final = "mqtt_provider"
CONF_MQTT_DEVICE_ID: Final = "mqtt_device_id"
CONF_MQTT_HOST: Final = "mqtt_host"
CONF_MQTT_USER: Final = "mqtt_user"
CONF_MQTT_PASSWORD: Final = "mqtt_password"  # noqa: S105

CHANNEL_STABLE: Final = "stable"
CHANNEL_NIGHTLY: Final = "nightly"
CHANNELS: Final = (CHANNEL_STABLE, CHANNEL_NIGHTLY)

PROVIDER_BUILTIN: Final = "builtin"
PROVIDER_BRILLIANT_MQTT: Final = "brilliant_mqtt"
PROVIDERS: Final = (PROVIDER_BUILTIN, PROVIDER_BRILLIANT_MQTT)

#: Prefix of the node name, the Brilliant device id's 12 first characters after it. It is the
#: config entry's unique id, the name the kiosk's built-in MQTT registers its device under and
#: the topic of the retained ``kiosk_state`` the integration is discovered from.
NODE_PREFIX: Final = "brilliant_"
NODE_ID_LENGTH: Final = 12


def node_of(device_id: str) -> str:
    """The node name (and unique id) of the panel whose Brilliant device id is ``device_id``."""
    return f"{NODE_PREFIX}{device_id[:NODE_ID_LENGTH]}"


def panel_title(room: str | None, host: str) -> str:
    """The entry title, and so the device name: Brilliant's room name when it is known."""
    return f"{room} Brilliant kiosk" if room else f"Brilliant kiosk {host}"


def is_fingerprint(unique_id: str | None) -> bool:
    """Whether a unique id is an SSH host key fingerprint, as version 1 entries may have.

    Version 1 used the host key fingerprint of a panel whose Brilliant device id could not be
    read (the kiosk's own tool was needed for that). A fingerprint changes when the panel is
    re-flashed, so such an entry is converted to the device id on its first successful poll.
    """
    return bool(unique_id) and str(unique_id).startswith("SHA256:")


DEFAULT_USERNAME: Final = "root"
DEFAULT_PORT: Final = 22
SCAN_INTERVAL: Final = timedelta(seconds=60)
RELEASES_INTERVAL: Final = timedelta(hours=6)

MQTT_DOMAIN: Final = "mqtt"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_VALUES: Final = "values"
ATTR_DELETE: Final = "delete"
ATTR_RESTART: Final = "restart"
ATTR_PERIPHERAL: Final = "peripheral"
ATTR_UNIT: Final = "unit"
ATTR_LINES: Final = "lines"
ATTR_TAG: Final = "tag"

SERVICE_SET_ENV: Final = "set_env"
SERVICE_GET_ENV: Final = "get_env"
SERVICE_BUS_GET: Final = "bus_get"
SERVICE_BUS_SET: Final = "bus_set"
SERVICE_LOGS: Final = "logs"
SERVICE_INSTALL_VERSION: Final = "install_version"

CONF_MANAGE_INSTALLS: Final = "manage_installs"
#: ha-kiosk-setup v1.4.0 accepts 1/0/kiosk (not "full"/"off"); "1" is the built-in full mode.
PANEL_MQTT_KIOSK: Final = "kiosk"
PANEL_MQTT_FULL: Final = "1"
#: kiosk.env keys that belong to the MQTT provider option: set in the options, never with set_env.
MQTT_PROVIDER_KEYS: Final = ("KIOSK_MQTT", "MQTT_DEVICE_ID")


def provider_of(settings: Mapping[str, str]) -> str:
    """The MQTT provider a panel's settings mean.

    ``KIOSK_MQTT`` is absent on panels installed without setting it: ha-kiosk-setup then defaults
    to ``1`` (built-in MQTT), so absent, ``1`` and anything but ``kiosk`` all mean built-in.
    """
    if settings.get("KIOSK_MQTT", "").lower() == PANEL_MQTT_KIOSK:
        return PROVIDER_BRILLIANT_MQTT
    return PROVIDER_BUILTIN


#: Repair issue ids are ``<key>_<config entry id>``; the key is also the translation key.
ISSUE_MQTT_PROVIDER: Final = "mqtt_provider_not_applied"
ISSUE_ENV_DRIFT: Final = "env_drift"
#: The drift issue of a panel whose running kiosk.env has values that cannot be written safely.
ISSUE_ENV_DRIFT_UNSAFE: Final = "env_drift_unsafe"

CONF_EXPECTED_FINGERPRINT: Final = "expected_fingerprint"

#: Where the documentation explains how to fix what a repair issue cannot fix from Home Assistant.
TROUBLESHOOTING_URL: Final = "https://github.com/MikeI-Code/ha-brilliant-kiosk#troubleshooting"

#: The retained ``<base>/kiosk_state`` of a kiosk that is running. The kiosk and ha-kiosk-setup
#: publish ``not_installed``, ``disabled`` and ``failed`` otherwise; only a running kiosk is
#: offered by MQTT discovery (a stale retained message of an uninstalled panel is not).
KIOSK_STATE_ACTIVE: Final = "active"
