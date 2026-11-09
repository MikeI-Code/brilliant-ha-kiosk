"""Constants for the Brilliant Kiosk integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "brilliant_kiosk"

CONF_HOST_KEY: Final = "host_key"
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

DEFAULT_USERNAME: Final = "root"
SCAN_INTERVAL: Final = timedelta(seconds=60)
RELEASES_INTERVAL: Final = timedelta(hours=6)

MQTT_DOMAIN: Final = "mqtt"
WYOMING_DOMAIN: Final = "wyoming"

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
MQTT_DISABLED_VALUES: Final = ("", "0", "off", "false", "no")
