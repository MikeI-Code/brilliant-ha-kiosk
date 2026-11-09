"""Config flow for Brilliant Kiosk."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryState,
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
    HostKeyInfo,
    HostKeyMismatch,
    InvalidArgument,
    Panel,
    PanelError,
    Releases,
    SettingsRejected,
    SetupError,
    fetch_host_key,
    kiosk_env_from_bundle,
)

from . import BrilliantKioskConfigEntry
from .const import (
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
    DEFAULT_USERNAME,
    DOMAIN,
    MQTT_DOMAIN,
    PANEL_MQTT_FULL,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
    PROVIDERS,
    provider_of,
)

_LOGGER = logging.getLogger(__name__)
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


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
    """Connect, read the state of the panel and close again. Raises library errors.

    Always pass ``host_key`` once the user confirmed it: the password is then only sent to a
    panel presenting that key.
    """
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


def _install_error(err: Exception) -> tuple[str, str]:
    """(error key, text) for a failed install."""
    if isinstance(err, InvalidArgument):
        return "invalid_value", str(err)
    if isinstance(err, SettingsRejected):
        return "settings_rejected", str(err)
    if isinstance(err, SetupError):
        return "install_failed", str(err)
    if isinstance(err, PanelError):
        return _error_key(err), str(err)
    _LOGGER.error("Unexpected error installing the kiosk", exc_info=err)
    return "unknown", ""


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
        self._user_input: dict[str, Any] = {}
        self._host_info: HostKeyInfo | None = None
        self._install_input: dict[str, Any] = {}
        self._install_task: asyncio.Task[None] | None = None
        self._install_error: tuple[str, str] = ("unknown", "")

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

    # -- user: address, credentials, host key (trust on first use) -----------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the panel's address and SSH credentials.

        The host key is read first, without sending any credential; the password is only used
        after the key was confirmed (or matched the fingerprint pasted here).
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await fetch_host_key(user_input[CONF_HOST])
            except PanelError as err:
                errors["base"] = _error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected error reading the host key of the panel")
                errors["base"] = "unknown"
            else:
                expected = (user_input.get(CONF_EXPECTED_FINGERPRINT) or "").strip()
                if expected and not info.matches(expected):
                    errors["base"] = "host_key_mismatch"
                else:
                    self._user_input = user_input
                    self._host_info = info
                    if expected:  # verified up front: nothing left to confirm
                        return await self._async_connect()
                    return await self.async_step_host_key()
        return self._show_user_form(user_input, errors)

    def _show_user_form(
        self, previous: Mapping[str, Any] | None, errors: dict[str, str]
    ) -> ConfigFlowResult:
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
                vol.Optional(CONF_EXPECTED_FINGERPRINT): str,
            }
        )
        suggested = {k: v for k, v in (previous or {}).items() if k != CONF_PASSWORD}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_host_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the host key fingerprint; the password is not used before this is confirmed."""
        assert self._host_info is not None
        if user_input is not None:
            return await self._async_connect()
        return self.async_show_form(
            step_id="host_key",
            data_schema=vol.Schema({}),
            description_placeholders={
                "host": self._user_input[CONF_HOST],
                # "ED25519 SHA256:..." so it can be compared with ssh-keygen -l output.
                "fingerprint": self._host_info.display,
            },
        )

    async def _async_connect(self) -> ConfigFlowResult:
        """Log in with the confirmed host key pinned, then add the panel or offer an install."""
        assert self._host_info is not None
        user_input = self._user_input
        try:
            probe = await probe_panel(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                self._host_info.sha256,
            )
        except PanelError as err:
            return self._show_user_form(user_input, {"base": _error_key(err)})
        except Exception:
            _LOGGER.exception("Unexpected error connecting to %s", user_input[CONF_HOST])
            return self._show_user_form(user_input, {"base": "unknown"})
        await self.async_set_unique_id(probe.unique_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
        self._data = {
            CONF_HOST: user_input[CONF_HOST],
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            CONF_HOST_KEY: probe.fingerprint,
            CONF_NODE: probe.node,
        }
        if not probe.installed:
            return await self.async_step_install()
        return self._create_entry(probe.settings)

    def _create_entry(self, settings: Mapping[str, str]) -> ConfigFlowResult:
        provider = provider_of(settings)
        return self.async_create_entry(
            title=f"Brilliant panel {self._data[CONF_HOST]}",
            data=self._data,
            options={
                CONF_MQTT_PROVIDER: provider,
                CONF_MQTT_DEVICE_ID: settings.get("MQTT_DEVICE_ID", ""),
                CONF_MANAGE_INSTALLS: provider == PROVIDER_BUILTIN,
            },
        )

    # -- first install (long running: progress step with a background task) -------------

    def _mqtt_broker(self) -> Mapping[str, Any]:
        broker = next(iter(self.hass.config_entries.async_entries(MQTT_DOMAIN)), None)
        return broker.data if broker else {}

    async def async_step_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to install the latest stable kiosk release on a bare panel."""
        if user_input is not None:
            if not user_input[CONF_INSTALL]:
                return self._create_entry({})
            self._install_input = user_input
            self._install_task = self.hass.async_create_task(self._install(user_input))
            return await self.async_step_installing()
        return self._show_install_form({}, "")

    def _show_install_form(self, errors: dict[str, str], error_text: str) -> ConfigFlowResult:
        previous = self._install_input
        mqtt = self._mqtt_broker()
        text = TextSelector()
        return self.async_show_form(
            step_id="install",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INSTALL, default=True): bool,
                    vol.Required(
                        CONF_HA_URL,
                        default=previous.get(CONF_HA_URL) or _internal_url(self.hass),
                    ): text,
                    vol.Required(CONF_TOKEN): _PASSWORD,
                    vol.Required(
                        CONF_MQTT_HOST,
                        default=previous.get(CONF_MQTT_HOST) or mqtt.get("broker", ""),
                    ): text,
                    vol.Optional(
                        CONF_MQTT_USER,
                        default=previous.get(CONF_MQTT_USER) or mqtt.get("username") or "",
                    ): text,
                    # Never pre-filled: a default would put the broker password into the form.
                    vol.Optional(CONF_MQTT_PASSWORD): _PASSWORD,
                }
            ),
            errors=errors,
            description_placeholders={"error": error_text},
        )

    async def async_step_installing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Progress while the release downloads and ha-kiosk-setup installs it."""
        task = self._install_task
        assert task is not None
        if not task.done():
            return self.async_show_progress(
                step_id="installing", progress_action="installing", progress_task=task
            )
        self._install_task = None
        try:
            await task
        except Exception as err:
            self._install_error = _install_error(err)
            return self.async_show_progress_done(next_step_id="install_failed")
        return self.async_show_progress_done(next_step_id="install_done")

    async def async_step_install_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Back to the install form with the reason."""
        key, text = self._install_error
        return self._show_install_form({"base": key}, text)

    async def async_step_install_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The kiosk is installed with the built-in MQTT mode; add the panel."""
        return self._create_entry({"KIOSK_MQTT": PANEL_MQTT_FULL})

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
        user = user_input.get(CONF_MQTT_USER)
        if user:
            overrides["MQTT_USER"] = user
        password = user_input.get(CONF_MQTT_PASSWORD)
        broker = self._mqtt_broker()
        if (
            not password
            and user
            and user == broker.get("username")
            and user_input[CONF_MQTT_HOST] == broker.get("broker")
        ):
            password = broker.get("password")  # reused server-side, never shown in the form
        if password:
            overrides["MQTT_PASSWORD"] = password
        env = await self.hass.async_add_executor_job(kiosk_env_from_bundle, bundle, overrides)
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
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD}),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    # -- reconfigure --------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the panel's address; the host key there must be the pinned one."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await fetch_host_key(user_input[CONF_HOST])
                if not info.matches(entry.data[CONF_HOST_KEY]):
                    # Never send the password to a host with a different key.
                    return self.async_abort(reason="host_key_changed")
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
    """Choose the panel's MQTT provider and whether this integration manages installs.

    The flow only validates and stores; the update listener applies the provider on the panel.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and store the options."""
        entry: BrilliantKioskConfigEntry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        options = entry.options
        if user_input is not None:
            provider = user_input[CONF_MQTT_PROVIDER]
            device_id = (user_input.get(CONF_MQTT_DEVICE_ID) or "").strip()
            if provider == PROVIDER_BRILLIANT_MQTT and not _DEVICE_ID_RE.match(device_id):
                errors[CONF_MQTT_DEVICE_ID] = "device_id_required"
            else:
                return self.async_create_entry(
                    data={
                        **options,
                        CONF_MQTT_PROVIDER: provider,
                        CONF_MQTT_DEVICE_ID: device_id,
                        CONF_MANAGE_INSTALLS: user_input[CONF_MANAGE_INSTALLS],
                    }
                )
        # Entries without the option follow what the panel runs (no KIOSK_MQTT means built-in).
        provider_now = options.get(CONF_MQTT_PROVIDER) or provider_of(entry.runtime_data.data.env)
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
