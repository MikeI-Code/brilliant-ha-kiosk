"""Wrapper types for the on-panel ``ha-kiosk-setup`` tool (docs/INTEGRATION.md, section 0)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .exceptions import (
    ActivationRolledBack,
    NotInstalled,
    SettingsRejected,
    SetupBusy,
    SetupError,
    StockUIRestored,
    VerificationFailed,
)

SETUP_PYTHON = "/data/switch-embedded/env/bin/python"
SETUP_PATH = "/var/lib/ha-kiosk/ha-kiosk-setup"
EXIT_IN_PROGRESS = 9
STATE_NOT_INSTALLED = "not_installed"
STATE_ACTIVE = "active"
STATE_IN_PROGRESS = "in_progress"

_EXIT_ERRORS: dict[int, type[SetupError]] = {
    3: VerificationFailed,
    4: SettingsRejected,
    5: ActivationRolledBack,
    6: StockUIRestored,
    7: NotInstalled,
    8: SetupBusy,
}


@dataclass(frozen=True, slots=True)
class SetupStatus:
    """Parsed ``ha-kiosk-setup status`` output."""

    #: not_installed | unmanaged | active | disabled | failed | in_progress
    state: str
    version: str | None = None
    previous: str | None = None
    services: Mapping[str, bool] = field(default_factory=dict)
    mqtt_mode: str | None = None
    mqtt_base: str | None = None
    firmware: str | None = None
    secrets_present: tuple[str, ...] = ()
    heartbeat_age_s: float | None = None
    ha_connected: bool | None = None
    last_result: Mapping[str, Any] | None = None

    @property
    def installed(self) -> bool:
        """The kiosk is on the panel (managed or not)."""
        return self.state != STATE_NOT_INSTALLED


def parse_json_object(text: str) -> dict[str, Any]:
    """The last JSON object printed by the tool (it may print log lines before it)."""
    decoder = json.JSONDecoder()
    found: dict[str, Any] = {}
    idx = text.find("{")
    while idx != -1:
        try:
            obj, end = decoder.raw_decode(text, idx)
        except ValueError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            found = obj
        idx = text.find("{", end)
    return found


def parse_status(data: Mapping[str, Any]) -> SetupStatus:
    """Build a :class:`SetupStatus` from the tool's status JSON."""
    services_raw = data.get("services")
    services = (
        {str(k): bool(v.get("active")) for k, v in services_raw.items() if isinstance(v, dict)}
        if isinstance(services_raw, dict)
        else {}
    )
    mqtt = _dict(data.get("mqtt"))
    ui = _dict(data.get("ui"))
    secrets = data.get("secrets_present")
    age = ui.get("heartbeat_age_s")
    last = data.get("last_result")
    return SetupStatus(
        state=str(data.get("state") or STATE_NOT_INSTALLED),
        version=_opt_str(data.get("version")),
        previous=_opt_str(data.get("previous")),
        services=services,
        mqtt_mode=_opt_str(mqtt.get("mode")),
        mqtt_base=_opt_str(mqtt.get("base")),
        firmware=_opt_str(data.get("firmware")),
        secrets_present=tuple(str(p) for p in secrets) if isinstance(secrets, list) else (),
        heartbeat_age_s=float(age) if isinstance(age, int | float) else None,
        ha_connected=ui.get("home_assistant")
        if isinstance(ui.get("home_assistant"), bool)
        else None,
        last_result=last if isinstance(last, dict) else None,
    )


def _opt_str(value: Any) -> str | None:
    return str(value) if value else None


def error_for_exit(code: int, output: str) -> SetupError:
    """The typed exception for a non-zero exit status, using the tool's own message."""
    data = parse_json_object(output)
    last = data.get("last_result")
    message = data.get("error")
    if message is None and isinstance(last, dict):
        message = last.get("message")
    text = str(message or f"ha-kiosk-setup exited with status {code}")
    return _EXIT_ERRORS.get(code, SetupError)(text, code)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
