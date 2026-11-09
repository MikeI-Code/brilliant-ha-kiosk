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
#: Files and unit ``ha-kiosk-setup status`` reads (VAR, ETC, HEARTBEAT, RESULT and JOB there).
VAR_DIR = "/var/lib/ha-kiosk"
ETC_DIR = "/etc/ha-kiosk"
HEARTBEAT_PATH = "/run/ha-kiosk/heartbeat.json"
RESULT_PATH = "/run/ha-kiosk/setup-result.json"
JOB_UNIT = "ha-kiosk-setup-job.service"
#: Secret-bearing files the tool reports in ``secrets_present`` (``secrets_present()`` there).
SECRET_PATHS = (
    f"{ETC_DIR}/token",
    f"{ETC_DIR}/kiosk.env",
    f"{VAR_DIR}/config/token",
    f"{VAR_DIR}/config/kiosk.env",
)
#: Units of the tool's ``services`` that :class:`~pybrilliantkiosk.models.PanelHealth` also reports.
STATUS_UNITS = (
    "ha-kiosk",
    "ha-kiosk-bridge",
    "ha-kiosk-display",
    "ha-kiosk-voice",
    "ha-kiosk-intercom",
    "ha-kiosk-capture",
)
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
        ha_connected=_ha_connected(ui.get("home_assistant")),
        last_result=last if isinstance(last, dict) else None,
    )


def _ha_connected(value: Any) -> bool | None:
    """The kiosk UI's heartbeat reports ``connected`` (a string); a bool is taken as it is."""
    if isinstance(value, bool):
        return value
    return value == "connected" if isinstance(value, str) else None


def status_from_files(
    facts: Mapping[str, str],
    heartbeat: str,
    result: str,
    units: Mapping[str, str],
    env: Mapping[str, str],
) -> SetupStatus:
    """The ``ha-kiosk-setup status`` of a panel, rebuilt from the files that command reads.

    A poll reads those files in the shell instead of starting the tool's Python interpreter every
    minute. The logic is that of ``status()`` in ``kiosk/ha-kiosk-setup``:

    * ``facts`` holds the first line of ``current``, ``state``, ``previous`` and ``mqtt_base``
      under ``/var/lib/ha-kiosk``, whether ``/etc/ha-kiosk/native/main.qml`` exists (``qml``), the
      state of the job unit (``job``), the firmware strings, the secret files that exist and the
      panel's clock (``now``, whole seconds);
    * ``heartbeat`` and ``result`` are the texts of the two JSON files under ``/run``;
    * ``units`` are the ``systemctl is-active`` answers of the health section;
    * ``env`` is the saved kiosk.env (the running one when there is no saved copy).

    A panel without the tool is ``not_installed``, as ``setup_status`` reports it. The enabled
    flags and ``switch_ui_app`` are in the full status only.
    """
    if facts.get("tool") != "present":
        return parse_status({"state": STATE_NOT_INSTALLED})
    state = facts.get("state") or ("unmanaged" if facts.get("qml") else STATE_NOT_INSTALLED)
    if facts.get("job") == "active":
        state = STATE_IN_PROGRESS
    elif state == "activating" or (
        state == STATE_ACTIVE and units.get("ha-kiosk.service") != "active"
    ):  # interrupted mid-activation (e.g. a power cut)
        state = "failed"
    heartbeat_data = _json_dict(heartbeat)
    stamp, now = heartbeat_data.get("time"), _to_number(facts.get("now"))
    age = (
        round(now - stamp, 1)
        if isinstance(stamp, int | float) and stamp and now is not None
        else None
    )
    return parse_status(
        {
            "state": state,
            "version": facts.get("current") or facts.get("version"),
            "previous": facts.get("previous"),
            "ui": {"heartbeat_age_s": age, "home_assistant": heartbeat_data.get("ha")},
            "services": {
                unit: {"active": units.get(f"{unit}.service") == "active"} for unit in STATUS_UNITS
            },
            "mqtt": {"mode": env.get("KIOSK_MQTT", "1"), "base": facts.get("mqtt_base")},
            "firmware": facts.get("firmware") or facts.get("os_release"),
            "secrets_present": facts.get("secrets", "").split(),
            "last_result": _json_dict(result) or None,
        }
    )


def _json_dict(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _to_number(text: str | None) -> float | None:
    try:
        return float(text) if text else None
    except ValueError:
        return None


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
