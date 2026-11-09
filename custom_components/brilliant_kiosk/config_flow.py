"""Config flow for Brilliant Kiosk."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from pybrilliantkiosk import (
    AuthFailed,
    CannotConnect,
    HostKeyMismatch,
    InvalidArgument,
    Panel,
    PanelError,
    Releases,
    SettingsRejected,
    SetupError,
    kiosk_env_from_bundle,
)

from . import BrilliantKioskConfigEntry
from .const import (
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
    DEFAULT_USERNAME,
    DOMAIN,
    MQTT_DOMAIN,
    PANEL_MQTT_FULL,
    PANEL_MQTT_KIOSK,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
    PROVIDERS,
)

_LOGGER = logging.getLogger(__name__)
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Probe:
    """What a successful connection to a panel told us."""

    fingerprint: str
    unique_id: str
    node: str | None
    installed: bool
    settings: Mapping[str, str]


async def probe_panel(
    host: str, username: str, password: str, host_key: str | None = None
) -> Probe:
    """Connect, read the state of the panel and close again. Raises library errors."""
    panel = Panel(host, username=username, password=password, host_key=host_key)
    try:
        fingerprint = await panel.connect()
        status = await panel.setup_status()
        settings = await panel.get_settings()
        node: str | None = None
        try:
            node = "brilliant_" + (await panel.bus_peripherals()).device_id[:12]
        except PanelError:
            _LOGGER.debug("Could not read the Brilliant device id from %s", host)
    finally:
        await panel.close()
    return Probe(
        fingerprint=fingerprint,
        unique_id=node or fingerprint,
        node=node,
        installed=status.installed,
        settings=settings,
    )


def _error_key(err: Exception) -> str:
    if isinstance(err, CannotConnect):
        return "cannot_connect"
    if isinstance(err, AuthFailed):
        return "invalid_auth"
    if isinstance(err, HostKeyMismatch):
        return "host_key_mismatch"
    return "unknown"


def _provider_of(settings: Mapping[str, str]) -> str:
    return (
        PROVIDER_BRILLIANT_MQTT
        if settings.get("KIOSK_MQTT", "").lower() == PANEL_MQTT_KIOSK
        else PROVIDER_BUILTIN
    )


def _internal_url(hass: HomeAssistant) -> str:
    try:
        return get_url(hass, allow_external=False, allow_cloud=False)
    except NoURLAvailableError:
        return ""


class BrilliantKioskConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for one panel."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._data: dict[str, Any] = {}
        self._probe: Probe | None = None

    # -- discovery ----------------------------------------------------------------------

    async def async_step_mqtt(self, discovery_info: MqttServiceInfo) -> ConfigFlowResult:
        """A panel's retained ``<base>/<node>/kiosk_state`` appeared on the broker."""
        parts = discovery_info.topic.split("/")
        if len(parts) < 3 or not parts[1]:
            return self.async_abort(reason="unknown")
        await self.async_set_unique_id(parts[1])
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": parts[1]}
        return await self.async_step_user()

    # -- user ---------------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the panel's address and SSH credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                probe = await probe_panel(
                    user_input[CONF_HOST], user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except PanelError as err:
                errors["base"] = _error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected error connecting to %s", user_input[CONF_HOST])
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(probe.unique_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                self._probe = probe
                self._data = {
                    **user_input,
                    CONF_HOST_KEY: probe.fingerprint,
                    CONF_NODE: probe.node,
                }
                if not probe.installed:
                    return await self.async_step_install()
                return self._create_entry(probe.settings)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    def _create_entry(self, settings: Mapping[str, str]) -> ConfigFlowResult:
        provider = _provider_of(settings)
        return self.async_create_entry(
            title=f"Brilliant panel {self._data[CONF_HOST]}",
            data=self._data,
            options={
                CONF_MQTT_PROVIDER: provider,
                CONF_MQTT_DEVICE_ID: settings.get("MQTT_DEVICE_ID", ""),
                CONF_MANAGE_INSTALLS: provider == PROVIDER_BUILTIN,
            },
        )

    # -- first install ------------------------------------------------------------------

    async def async_step_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to install the latest stable kiosk release on a bare panel."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            if not user_input[CONF_INSTALL]:
                return self._create_entry({})
            try:
                await self._install(user_input)
            except InvalidArgument as err:
                errors["base"], placeholders["error"] = "invalid_value", str(err)
            except SettingsRejected as err:
                errors["base"], placeholders["error"] = "settings_rejected", str(err)
            except SetupError as err:
                errors["base"], placeholders["error"] = "install_failed", str(err)
            except PanelError as err:
                errors["base"], placeholders["error"] = _error_key(err), str(err)
            else:
                return self._create_entry({"KIOSK_MQTT": PANEL_MQTT_FULL})
        broker = next(iter(self.hass.config_entries.async_entries(MQTT_DOMAIN)), None)
        mqtt = broker.data if broker else {}
        text = TextSelector()
        password = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        return self.async_show_form(
            step_id="install",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INSTALL, default=True): bool,
                    vol.Required(
                        CONF_HA_URL,
                        default=(user_input or {}).get(CONF_HA_URL) or _internal_url(self.hass),
                    ): text,
                    vol.Required(CONF_TOKEN): password,
                    vol.Required(CONF_MQTT_HOST, default=mqtt.get("broker", "")): text,
                    vol.Optional(CONF_MQTT_USER, default=mqtt.get("username") or ""): text,
                    vol.Optional(CONF_MQTT_PASSWORD, default=mqtt.get("password") or ""): password,
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _install(self, user_input: Mapping[str, Any]) -> None:
        """Download the latest stable release and install it with ha-kiosk-setup."""
        releases = Releases(async_get_clientsession(self.hass))
        stable = next((r for r in await releases.list(include_prereleases=False)), None)
        if stable is None:
            raise SetupError("no stable release is published")
        bundle, sha256 = await releases.download(stable)
        overrides = {
            "HA_URL": user_input[CONF_HA_URL],
            "KIOSK_MQTT": PANEL_MQTT_FULL,
            "MQTT_HOST": user_input[CONF_MQTT_HOST],
        }
        if user_input.get(CONF_MQTT_USER):
            overrides["MQTT_USER"] = user_input[CONF_MQTT_USER]
        if user_input.get(CONF_MQTT_PASSWORD):
            overrides["MQTT_PASSWORD"] = user_input[CONF_MQTT_PASSWORD]
        env = kiosk_env_from_bundle(bundle, overrides)
        panel = Panel(
            self._data[CONF_HOST],
            username=self._data[CONF_USERNAME],
            password=self._data[CONF_PASSWORD],
            host_key=self._data[CONF_HOST_KEY],
        )
        try:
            await panel.install(bundle, sha256, env=env, token=user_input[CONF_TOKEN].strip())
        finally:
            await panel.close()

    # -- reauth -------------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The stored password stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await probe_panel(
                    entry.data[CONF_HOST],
                    entry.data[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    entry.data[CONF_HOST_KEY],
                )
            except PanelError as err:
                errors["base"] = _error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected error during reauthentication")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    # -- reconfigure --------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the panel's address (the pinned host key must still match)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                probe = await probe_panel(
                    user_input[CONF_HOST],
                    entry.data[CONF_USERNAME],
                    entry.data[CONF_PASSWORD],
                    entry.data[CONF_HOST_KEY],
                )
            except PanelError as err:
                errors["base"] = _error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected error during reconfiguration")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(probe.unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: user_input[CONF_HOST]}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: BrilliantKioskConfigEntry) -> OptionsFlow:
        """Options: MQTT provider and install management."""
        return BrilliantKioskOptionsFlow()


class BrilliantKioskOptionsFlow(OptionsFlow):
    """Choose the panel's MQTT provider and whether this integration manages installs."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and apply the options."""
        entry: BrilliantKioskConfigEntry = self.config_entry
        if not hasattr(entry, "runtime_data"):
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        options = entry.options
        if user_input is not None:
            provider = user_input[CONF_MQTT_PROVIDER]
            device_id = (user_input.get(CONF_MQTT_DEVICE_ID) or "").strip()
            if provider == PROVIDER_BRILLIANT_MQTT and not _DEVICE_ID_RE.match(device_id):
                errors[CONF_MQTT_DEVICE_ID] = "device_id_required"
            else:
                try:
                    await self._apply_provider(entry, provider, device_id)
                except PanelError as err:
                    errors["base"] = _error_key(err)
                else:
                    return self.async_create_entry(
                        data={
                            **options,
                            CONF_MQTT_PROVIDER: provider,
                            CONF_MQTT_DEVICE_ID: device_id,
                            CONF_MANAGE_INSTALLS: user_input[CONF_MANAGE_INSTALLS],
                        }
                    )
        provider_now = options.get(CONF_MQTT_PROVIDER, PROVIDER_BUILTIN)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MQTT_PROVIDER, default=provider_now): SelectSelector(
                        SelectSelectorConfig(
                            options=list(PROVIDERS),
                            translation_key=CONF_MQTT_PROVIDER,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(
                        CONF_MQTT_DEVICE_ID, default=options.get(CONF_MQTT_DEVICE_ID, "")
                    ): str,
                    vol.Required(
                        CONF_MANAGE_INSTALLS,
                        default=options.get(CONF_MANAGE_INSTALLS, provider_now == PROVIDER_BUILTIN),
                    ): bool,
                }
            ),
            errors=errors,
        )

    @staticmethod
    async def _apply_provider(
        entry: BrilliantKioskConfigEntry, provider: str, device_id: str
    ) -> None:
        """Set KIOSK_MQTT (and MQTT_DEVICE_ID) on the panel when it differs; never off."""
        env = entry.runtime_data.data.env
        panel = entry.runtime_data.panel
        if provider == PROVIDER_BRILLIANT_MQTT:
            changes = {"KIOSK_MQTT": PANEL_MQTT_KIOSK, "MQTT_DEVICE_ID": device_id}
            delete: tuple[str, ...] = ()
        else:
            changes = {"KIOSK_MQTT": PANEL_MQTT_FULL}
            delete = ("MQTT_DEVICE_ID",) if env.get("MQTT_DEVICE_ID") else ()
        if await panel.set_env(changes, delete):
            await panel.restart("ha-kiosk-bridge")
