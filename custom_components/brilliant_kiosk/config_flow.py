"""Config flow for Brilliant Kiosk."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
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
    DeviceIdUnavailable,
    HostKeyInfo,
    HostKeyMismatch,
    InvalidArgument,
    Panel,
    PanelError,
    Releases,
    SettingsRejected,
    SetupError,
    fetch_host_key,
    format_value,
    is_md5_fingerprint,
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
    CONF_NEW_HOST_KEY,
    CONF_NODE,
    CONF_PRIVATE_KEY,
    CONF_TOKEN,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
    KIOSK_STATE_ACTIVE,
    PANEL_MQTT_FULL,
    PROVIDER_BRILLIANT_MQTT,
    PROVIDER_BUILTIN,
    PROVIDERS,
    is_fingerprint,
    node_of,
    panel_title,
    provider_of,
)
from .errors import InstallRefused
from .install import async_fetch_bundle

_LOGGER = logging.getLogger(__name__)
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEX_ID_RE = re.compile(r"^[0-9A-Fa-f]{12,64}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
_URL_RE = re.compile(r"^https?://[^\s/]+")
_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_KEY_TEXT = TextSelector(TextSelectorConfig(multiline=True, autocomplete="off"))


@dataclass(frozen=True, slots=True)
class Probe:
    """What a successful connection to a panel told us."""

    fingerprint: str
    #: The panel's identity: ``brilliant_<12 characters of Brilliant's device id>``. None only
    #: when it could not be read and the caller allowed that (``require_node=False``).
    node: str | None
    installed: bool
    settings: Mapping[str, str]
    #: Brilliant's name for the room of the panel (None when not known).
    room: str | None = None


async def probe_panel(
    host: str,
    username: str,
    password: str | None,
    host_key: str | None = None,
    private_key: str | None = None,
    *,
    require_node: bool = True,
    port: int = DEFAULT_PORT,
) -> Probe:
    """Connect, read the state of the panel and close again. Raises library errors.

    Always pass ``host_key`` once the user confirmed it: the password is then only sent to a
    panel presenting that key. Authenticate with ``password``, ``private_key`` or both.

    The panel's identity is Brilliant's device id, read from the stock firmware (it needs no part
    of the kiosk, so it is there before an install and after an uninstall or a re-flash). When it
    cannot be read :class:`DeviceIdUnavailable` is raised, unless ``require_node`` is False (a
    reauth or reconfigure, which compare the id only when both sides have one). The host key
    fingerprint is never used as an identity: it changes when the panel is re-flashed.
    """
    panel = Panel(
        host,
        username=username,
        password=password or None,
        private_key=private_key or None,
        port=port,
        host_key=host_key,
    )
    try:
        fingerprint = await panel.connect()
        status = await panel.setup_status()
        settings = await panel.get_settings()
        node: str | None = None
        room: str | None = None
        try:
            identity = await panel.identity()
            node = node_of(identity.device_id)
            room = identity.room
        except DeviceIdUnavailable:
            if require_node:
                raise
            _LOGGER.debug("Could not read the Brilliant device id from %s", host)
    finally:
        await panel.close()
    return Probe(
        fingerprint=fingerprint,
        node=node,
        installed=status.installed,
        settings=settings,
        room=room,
    )


def _port_of(data: Mapping[str, Any]) -> int:
    """The SSH port of a config entry (entries made before the port was asked for use 22)."""
    return int(data.get(CONF_PORT, DEFAULT_PORT))


_PORT = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))


def _kiosk_state(payload: str | bytes | bytearray) -> str:
    """The retained ``kiosk_state`` text (the MQTT payload may arrive as bytes)."""
    text = payload if isinstance(payload, str) else bytes(payload).decode("utf-8", "replace")
    return text.strip()


@dataclass(frozen=True, slots=True)
class Discovered:
    """The retained ``<base>/<node>/discovery`` message of a kiosk, validated."""

    state: str
    device_id: str
    host: str | None
    room: str | None


def _valid_host(host: object) -> str | None:
    """The host if it is an IPv4 address or a host name (None otherwise)."""
    if not isinstance(host, str):
        return None
    if host.replace(".", "").isdigit():
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError:
            return None
    return host if _HOSTNAME_RE.match(host) else None


def _parse_discovery(payload: str | bytes | bytearray) -> Discovered | None:
    """The discovery JSON of a kiosk (None when it is not what the kiosk publishes)."""
    try:
        data = json.loads(_kiosk_state(payload))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    state, device_id, room = data.get("state"), data.get("device_id"), data.get("room")
    if not isinstance(state, str) or not isinstance(device_id, str):
        return None
    if not _HEX_ID_RE.match(device_id):
        return None
    cleaned = None
    if isinstance(room, str):
        text = "".join(c for c in room if c.isprintable()).strip()[:64]
        cleaned = text or None
    return Discovered(state, device_id, _valid_host(data.get("host")), cleaned)


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
    if isinstance(err, InstallRefused):
        return err.key, str(err)
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


def _key_placeholders(info: HostKeyInfo) -> dict[str, str]:
    """The fingerprint (``SHA256:...``, as ssh-keygen -l prints it) and the key type to show."""
    return {"fingerprint": info.sha256, "key_type": info.label or "unknown"}


def _internal_url(hass: HomeAssistant) -> str:
    try:
        return get_url(hass, allow_external=False, allow_cloud=False)
    except NoURLAvailableError:
        return ""


def _normalise_key(text: str | None) -> str | None:
    """A pasted private key without stray whitespace, ending in one newline (None if empty)."""
    stripped = (text or "").strip()
    return f"{stripped}\n" if stripped else None


def _credentials(user_input: Mapping[str, Any]) -> dict[str, str]:
    """The password and/or private key of a form, normalised; only what was given."""
    found: dict[str, str] = {}
    if password := user_input.get(CONF_PASSWORD):
        found[CONF_PASSWORD] = password
    if key := _normalise_key(user_input.get(CONF_PRIVATE_KEY)):
        found[CONF_PRIVATE_KEY] = key
    return found


def _auth_schema() -> dict[Any, Any]:
    """Password and private key; one of them is required (checked in the step)."""
    return {
        vol.Optional(CONF_PASSWORD): _PASSWORD,
        vol.Optional(CONF_PRIVATE_KEY): _KEY_TEXT,
    }


def _install_field_errors(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Field errors for the install form, found before anything is downloaded.

    Settings are written ``KEY="value"`` into a file that root shell scripts source, so values
    must not contain quotes, backslash, ``$``, a backtick or control characters (this includes
    the MQTT password); the panel's own validator would reject them after the download.
    """
    errors: dict[str, str] = {}
    for field in (CONF_HA_URL, CONF_MQTT_HOST, CONF_MQTT_USER, CONF_MQTT_PASSWORD):
        try:
            format_value(user_input.get(field) or "")
        except InvalidArgument:
            errors[field] = "invalid_characters"
    if CONF_HA_URL not in errors and not _URL_RE.match(user_input[CONF_HA_URL]):
        errors[CONF_HA_URL] = "invalid_url"
    return errors


class BrilliantKioskConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for one panel."""

    #: 2: the unique id is the node of the Brilliant device id (1 allowed a host key fingerprint).
    VERSION = 2

    def __init__(self) -> None:
        """Initialise."""
        self._data: dict[str, Any] = {}
        self._user_input: dict[str, Any] = {}
        self._host_info: HostKeyInfo | None = None
        self._install_input: dict[str, Any] = {}
        self._install_task: asyncio.Task[None] | None = None
        self._install_error: tuple[str, str] = ("unknown", "")
        #: The node of the panel this flow was discovered for; the panel found at the address the
        #: user types must be that one (see :meth:`_async_connect`).
        self._discovered_node: str | None = None
        #: The address and room of the panel the kiosk's ``discovery`` message named.
        self._discovered_host: str | None = None
        self._room: str | None = None

    @callback
    def async_remove(self) -> None:
        """The flow was closed: stop an install that is still running."""
        task, self._install_task = self._install_task, None
        if task is not None and not task.done():
            task.cancel()

    # -- discovery ----------------------------------------------------------------------

    async def async_step_mqtt(self, discovery_info: MqttServiceInfo) -> ConfigFlowResult:
        """A panel's retained ``<base>/<node>/kiosk_state`` appeared on the broker.

        Only a running kiosk (``active``) is offered. ``not_installed``, ``disabled`` and
        ``failed`` are what ha-kiosk-setup leaves behind on the broker when the kiosk is removed,
        switched off or fails to start: a stale message must not make a panel pop up as new, and
        such a panel is added by hand (the flow can install the kiosk on it then).
        """
        parts = discovery_info.topic.split("/")
        if len(parts) < 3 or not parts[1]:
            return self.async_abort(reason="unknown")
        if parts[2] == "discovery":
            return await self._async_discovery_message(discovery_info)
        await self.async_set_unique_id(parts[1])
        self._abort_if_unique_id_configured()
        if _kiosk_state(discovery_info.payload) != KIOSK_STATE_ACTIVE:
            return self.async_abort(reason="kiosk_not_active")
        self._discovered_node = parts[1]
        self.context["title_placeholders"] = {"name": parts[1]}
        return await self.async_step_user()

    async def _async_discovery_message(self, discovery_info: MqttServiceInfo) -> ConfigFlowResult:
        """A kiosk's retained ``<base>/<node>/discovery``: JSON with state, host, id and room.

        The unique id comes from the device id up front, so a panel that is already added is not
        offered again, and the address is prefilled: the user only enters the password. Older
        kiosks publish only ``kiosk_state`` (see :meth:`async_step_mqtt`), without an address.
        """
        found = _parse_discovery(discovery_info.payload)
        if found is None:
            return self.async_abort(reason="unknown")
        node = node_of(found.device_id)
        # A flow made by the older ``kiosk_state`` message knows less: this one replaces it.
        for flow in self.hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, match_context={"unique_id": node}
        ):
            if flow["flow_id"] != self.flow_id and not flow["context"].get("prefilled"):
                self.hass.config_entries.flow.async_abort(flow["flow_id"])
        await self.async_set_unique_id(node)
        # A panel that is already added but now reports a different address (DHCP) gets its
        # stored host updated and the entry reloaded. Safe: the connection still has to present
        # the pinned SSH host key, so a wrong or spoofed address cannot be used.
        self._abort_if_unique_id_configured(updates={CONF_HOST: found.host} if found.host else None)
        if found.state != KIOSK_STATE_ACTIVE:
            return self.async_abort(reason="kiosk_not_active")
        self._discovered_node = node
        self._discovered_host = found.host
        self._room = found.room
        cast(dict[str, Any], self.context)["prefilled"] = True
        self.context["title_placeholders"] = {
            "name": f"{found.room} Brilliant panel found" if found.room else node
        }
        return await self.async_step_user()

    # -- user: address, credentials, host key (trust on first use) -----------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the panel's address and SSH credentials.

        The host key is read first, without sending any credential; the password is only used
        after the key was confirmed (or matched the fingerprint pasted here).
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            expected = (user_input.get(CONF_EXPECTED_FINGERPRINT) or "").strip()
            if not _credentials(user_input):
                errors["base"] = "auth_required"
            elif is_md5_fingerprint(expected):
                errors[CONF_EXPECTED_FINGERPRINT] = "md5_fingerprint"
            else:
                try:
                    info = await fetch_host_key(user_input[CONF_HOST], port=user_input[CONF_PORT])
                except PanelError as err:
                    errors["base"] = _error_key(err)
                except Exception:
                    _LOGGER.exception("Unexpected error reading the host key of the panel")
                    errors["base"] = "unknown"
                else:
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
                vol.Required(CONF_PORT, default=DEFAULT_PORT): _PORT,
                vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                **_auth_schema(),
                vol.Optional(CONF_EXPECTED_FINGERPRINT): str,
            }
        )
        secrets = (CONF_PASSWORD, CONF_PRIVATE_KEY)
        suggested = {k: v for k, v in (previous or {}).items() if k not in secrets}
        if self._discovered_host and CONF_HOST not in suggested:
            suggested[CONF_HOST] = self._discovered_host
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
                **_key_placeholders(self._host_info),
            },
        )

    async def _async_connect(self) -> ConfigFlowResult:
        """Log in with the confirmed host key pinned, then add the panel or offer an install."""
        assert self._host_info is not None
        user_input = self._user_input
        credentials = _credentials(user_input)
        try:
            probe = await probe_panel(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                credentials.get(CONF_PASSWORD),
                self._host_info.sha256,
                credentials.get(CONF_PRIVATE_KEY),
                port=user_input[CONF_PORT],
            )
        except DeviceIdUnavailable:
            return self.async_abort(reason="device_id_unavailable")
        except PanelError as err:
            return self._show_user_form(user_input, {"base": _error_key(err)})
        except Exception:
            _LOGGER.exception("Unexpected error connecting to %s", user_input[CONF_HOST])
            return self._show_user_form(user_input, {"base": "unknown"})
        assert probe.node is not None  # require_node
        self._room = probe.room or self._room
        if self._discovered_node is not None and probe.node != self._discovered_node:
            return self.async_abort(reason="unique_id_mismatch")
        await self.async_set_unique_id(probe.node)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: user_input[CONF_HOST], CONF_PORT: user_input[CONF_PORT]}
        )
        # A version 1 entry that is not converted yet has the host key as its unique id.
        for existing in self._async_current_entries(include_ignore=False):
            if is_fingerprint(existing.unique_id) and (
                existing.data.get(CONF_HOST_KEY) == probe.fingerprint
            ):
                return self.async_abort(reason="already_configured")
        self._data = {
            CONF_HOST: user_input[CONF_HOST],
            CONF_PORT: user_input[CONF_PORT],
            CONF_USERNAME: user_input[CONF_USERNAME],
            **credentials,
            CONF_HOST_KEY: probe.fingerprint,
            CONF_NODE: probe.node,
        }
        if not probe.installed:
            return await self.async_step_install()
        return self._create_entry(probe.settings)

    def _create_entry(self, settings: Mapping[str, str]) -> ConfigFlowResult:
        provider = provider_of(settings)
        return self.async_create_entry(
            title=panel_title(self._room, self._data[CONF_HOST]),
            data=self._data,
            options={
                CONF_MQTT_PROVIDER: provider,
                CONF_MQTT_DEVICE_ID: settings.get("MQTT_DEVICE_ID", ""),
                CONF_MANAGE_INSTALLS: provider == PROVIDER_BUILTIN,
            },
        )

    # -- first install (long running: progress step with a background task) -------------

    async def async_step_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to install the latest stable kiosk release on a bare panel."""
        if user_input is not None:
            if not user_input[CONF_INSTALL]:
                return self._create_entry({})
            self._install_input = user_input
            if errors := _install_field_errors(user_input):
                return self._show_install_form(errors, "")
            self._install_task = self.hass.async_create_task(
                self._install(user_input), "brilliant_kiosk first install"
            )
            self._install_task.add_done_callback(self._log_install_end)
            return await self.async_step_installing()
        return self._show_install_form({}, "")

    def _show_install_form(self, errors: dict[str, str], error_text: str) -> ConfigFlowResult:
        """The install form. Nothing is prefilled from other integrations: the MQTT broker and
        its login are typed by the user (the broker's password is never read from the MQTT
        integration's config entry)."""
        previous = self._install_input
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
                    vol.Required(CONF_MQTT_HOST, default=previous.get(CONF_MQTT_HOST, "")): text,
                    vol.Optional(CONF_MQTT_USER, default=previous.get(CONF_MQTT_USER, "")): text,
                    # Never pre-filled: a default would put the password into the form.
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

    @callback
    def _log_install_end(self, task: asyncio.Task[None]) -> None:
        """Retrieve and log how the install task ended.

        The task's error is shown in the flow when the flow is still open. When it was closed
        first nobody awaits the task, and an unretrieved exception would be reported by asyncio
        only when the task is garbage collected. Reading it here prevents that and leaves a line
        in the log. A cancelled task (the flow was closed, see :meth:`async_remove`) is not an
        error.
        """
        if task.cancelled():
            _LOGGER.debug("The kiosk install on %s was cancelled", self._data[CONF_HOST])
        elif (err := task.exception()) is not None:
            _LOGGER.warning("The kiosk install on %s failed: %s", self._data[CONF_HOST], err)

    async def _install(self, user_input: Mapping[str, Any]) -> None:
        """Download the latest stable release, verify it and install it with ha-kiosk-setup.

        Only a release pinned in this integration is installed (see ``pinned.py``).
        """
        releases = Releases(async_get_clientsession(self.hass))
        stable = await releases.latest_stable()
        if stable is None:
            raise SetupError("no stable release is published")
        bundle, sha256, size = await async_fetch_bundle(releases, stable, nightly_allowed=False)
        overrides = {
            "HA_URL": user_input[CONF_HA_URL],
            "KIOSK_MQTT": PANEL_MQTT_FULL,
            "MQTT_HOST": user_input[CONF_MQTT_HOST],
        }
        for key, field in (("MQTT_USER", CONF_MQTT_USER), ("MQTT_PASSWORD", CONF_MQTT_PASSWORD)):
            if value := user_input.get(field):
                overrides[key] = value
        env = await self.hass.async_add_executor_job(kiosk_env_from_bundle, bundle, overrides)
        panel = Panel(
            self._data[CONF_HOST],
            username=self._data[CONF_USERNAME],
            password=self._data.get(CONF_PASSWORD),
            private_key=self._data.get(CONF_PRIVATE_KEY),
            port=self._data[CONF_PORT],
            host_key=self._data[CONF_HOST_KEY],
        )
        try:
            await panel.install(
                bundle, sha256, size=size, env=env, token=user_input[CONF_TOKEN].strip()
            )
        finally:
            await panel.close()

    # -- a changed host key: shown, and pinned only on explicit confirmation ---------------

    def _show_key_confirm(
        self,
        step_id: str,
        entry: ConfigEntry[Any],
        errors: dict[str, str],
        host: str | None = None,
    ) -> ConfigFlowResult:
        assert self._host_info is not None
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_NEW_HOST_KEY, default=False): bool}),
            description_placeholders={
                "host": host or entry.data[CONF_HOST],
                "old_fingerprint": entry.data[CONF_HOST_KEY],
                **_key_placeholders(self._host_info),
            },
            errors=errors,
        )

    def _node_mismatch(self, entry: ConfigEntry[Any], probe: Probe) -> bool:
        """Is this another panel than the entry's, by its Brilliant device id?

        Compared only when both ids are known: an entry made before the id could be read has
        none (its unique id is a fingerprint), and a panel whose id cannot be read right now
        proves nothing either way.
        """
        known = entry.data.get(CONF_NODE) or (
            None if is_fingerprint(entry.unique_id) else entry.unique_id
        )
        return bool(known) and probe.node is not None and probe.node != known

    # -- reauth -------------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The stored credentials stopped working, or the panel's host key changed.

        The host key is read first, without credentials. A different key is shown for explicit
        confirmation before any password is sent to it.
        """
        entry = self._get_reauth_entry()
        try:
            info = await fetch_host_key(entry.data[CONF_HOST], port=_port_of(entry.data))
        except PanelError:
            info = None  # unreachable: the next step reports it when the login is tried
        if info is not None and not info.matches(entry.data[CONF_HOST_KEY]):
            self._host_info = info
            return await self.async_step_reauth_host_key()
        self._host_info = None
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_host_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the panel's new host key; continue only if the user trusts it."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_NEW_HOST_KEY]:
                return await self.async_step_reauth_confirm()
            errors["base"] = "host_key_not_confirmed"
        return self._show_key_confirm("reauth_host_key", entry, errors)

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password and/or private key (sent only to the pinned or confirmed key)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            credentials = _credentials(user_input)
            key = self._host_info.sha256 if self._host_info else entry.data[CONF_HOST_KEY]
            if not credentials:
                errors["base"] = "auth_required"
            else:
                try:
                    probe = await probe_panel(
                        entry.data[CONF_HOST],
                        entry.data[CONF_USERNAME],
                        credentials.get(CONF_PASSWORD),
                        key,
                        credentials.get(CONF_PRIVATE_KEY),
                        require_node=False,
                        port=_port_of(entry.data),
                    )
                except PanelError as err:
                    errors["base"] = _error_key(err)
                except Exception:
                    _LOGGER.exception("Unexpected error during reauthentication")
                    errors["base"] = "unknown"
                else:
                    if self._node_mismatch(entry, probe):
                        return self.async_abort(reason="unique_id_mismatch")
                    # Replace both credentials with what was given: a stale password or key
                    # that no longer works must not linger next to the new one.
                    data = {
                        k: v
                        for k, v in entry.data.items()
                        if k not in (CONF_PASSWORD, CONF_PRIVATE_KEY)
                    }
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**data, **credentials, CONF_HOST_KEY: probe.fingerprint},
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_auth_schema()),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    # -- reconfigure --------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the panel's address.

        A panel that presents the pinned host key is accepted. A different key is shown and
        pinned only after explicit confirmation; the stored credentials are never sent to a
        host whose key was not confirmed.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await fetch_host_key(user_input[CONF_HOST], port=user_input[CONF_PORT])
            except PanelError as err:
                errors["base"] = _error_key(err)
            except Exception:
                _LOGGER.exception("Unexpected error during reconfiguration")
                errors["base"] = "unknown"
            else:
                self._user_input = user_input
                if info.matches(entry.data[CONF_HOST_KEY]):
                    self._host_info = None
                    return await self._async_finish_reconfigure(entry, entry.data[CONF_HOST_KEY])
                self._host_info = info
                return await self.async_step_reconfigure_host_key()
        return self._show_reconfigure_form(entry, errors)

    def _show_reconfigure_form(
        self, entry: ConfigEntry[Any], errors: dict[str, str]
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=_port_of(entry.data)): _PORT,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure_host_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The new address presents another host key: show it, pin it only if confirmed."""
        entry = self._get_reconfigure_entry()
        assert self._host_info is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_NEW_HOST_KEY]:
                return await self._async_finish_reconfigure(entry, self._host_info.sha256)
            errors["base"] = "host_key_not_confirmed"
        return self._show_key_confirm(
            "reconfigure_host_key", entry, errors, self._user_input[CONF_HOST]
        )

    async def _async_finish_reconfigure(
        self, entry: ConfigEntry[Any], host_key: str
    ) -> ConfigFlowResult:
        """Log in at the new address with ``host_key`` pinned and store host, port and key."""
        host = self._user_input[CONF_HOST]
        port = self._user_input[CONF_PORT]
        try:
            probe = await probe_panel(
                host,
                entry.data[CONF_USERNAME],
                entry.data.get(CONF_PASSWORD),
                host_key,
                entry.data.get(CONF_PRIVATE_KEY),
                require_node=False,
                port=port,
            )
        except PanelError as err:
            return self._reconfigure_failed(entry, {"base": _error_key(err)})
        except Exception:
            _LOGGER.exception("Unexpected error during reconfiguration")
            return self._reconfigure_failed(entry, {"base": "unknown"})
        if self._node_mismatch(entry, probe):
            return self.async_abort(reason="unique_id_mismatch")
        return self.async_update_reload_and_abort(
            entry,
            data_updates={CONF_HOST: host, CONF_PORT: port, CONF_HOST_KEY: probe.fingerprint},
        )

    def _reconfigure_failed(
        self, entry: ConfigEntry[Any], errors: dict[str, str]
    ) -> ConfigFlowResult:
        if self._host_info is not None:
            return self._show_key_confirm(
                "reconfigure_host_key", entry, errors, self._user_input[CONF_HOST]
            )
        return self._show_reconfigure_form(entry, errors)

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
