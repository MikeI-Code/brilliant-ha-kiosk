"""Plain data types returned by the library."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from .setup import SetupStatus

#: systemd units managed by the kiosk (kiosk/install-local.sh UNITS).
UNITS: tuple[str, ...] = (
    "ha-kiosk.service",
    "ha-kiosk-display.service",
    "ha-kiosk-bridge.service",
    "ha-kiosk-swap.service",
    "ha-kiosk-voice.service",
    "ha-kiosk-intercom.service",
    "ha-kiosk-capture.service",
)

ENV_PATH = "/etc/ha-kiosk/kiosk.env"
VERSION_PATH = "/etc/ha-kiosk/VERSION"
BACKLIGHT_DIR = "/sys/class/backlight/backlight_lcd"
DISPLAY_STATE_PATH = "/run/ha-kiosk-display/state"
OVERRIDES_PATH = "/var/lib/ha-kiosk/overrides.env"
CONFIG_ENV_PATH = "/var/lib/ha-kiosk/config/kiosk.env"
#: Settings the display service accepts as runtime overrides (same list as ha-kiosk-mqtt.py).
OVERRIDE_KEYS: tuple[str, ...] = (
    "SCREENSAVER",
    "NIGHT_MODE",
    "MOTION_WAKE",
    "SMART_DIM",
    "DAY_LEVEL",
)


@dataclass(frozen=True, slots=True)
class PanelHealth:
    """Snapshot of a panel's health, gathered in one SSH round trip."""

    uptime_seconds: float | None
    load_1: float | None
    load_5: float | None
    load_15: float | None
    mem_total_kb: int | None
    mem_available_kb: int | None
    version: str | None
    units: Mapping[str, str] = field(default_factory=dict)
    backlight: int | None = None
    backlight_max: int | None = None
    display_state: str | None = None

    @property
    def mem_used_percent(self) -> float | None:
        """Used memory as a percentage of total, if known."""
        if not self.mem_total_kb or self.mem_available_kb is None:
            return None
        return round(100 * (self.mem_total_kb - self.mem_available_kb) / self.mem_total_kb, 1)

    def unit_active(self, unit: str) -> bool:
        """Whether the unit reported ``active`` (name with or without ``.service``)."""
        name = unit if unit.endswith(".service") else f"{unit}.service"
        return self.units.get(name) == "active"


@dataclass(frozen=True, slots=True)
class PanelSnapshot:
    """Everything a poll needs, read from the panel with ONE SSH command (:meth:`Panel.poll`)."""

    health: PanelHealth
    #: ``ha-kiosk-setup status``; ``state == "unknown"`` when the tool failed or cannot be run.
    setup: SetupStatus
    #: Effective settings: both copies of kiosk.env reconciled, overrides on top, secrets masked.
    settings: Mapping[str, str]
    #: Keys whose value differs between the running and the saved copy of kiosk.env.
    drift: tuple[str, ...] = ()
    #: Drifted keys whose running value cannot be written safely (see ``Panel.reconcile_env``).
    unsafe: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BusPeripheral:
    """A peripheral on the panel's message bus."""

    name: str
    variable_count: int


@dataclass(frozen=True, slots=True)
class BusListing:
    """Result of ``brilliant_bus.py list``."""

    device_id: str
    peripherals: tuple[BusPeripheral, ...]


@dataclass(frozen=True, slots=True)
class BusVariable:
    """A message-bus variable."""

    name: str
    value: str
    read_only: bool


@dataclass(frozen=True, slots=True)
class Release:
    """A GitHub release that ships an installable bundle."""

    tag: str
    prerelease: bool
    published_at: datetime | None
    body: str
    bundle_url: str
    sha256_url: str | None
    #: Bundle VERSION, taken from the asset name ``brilliant-ha-kiosk-<VERSION>.tar.gz``
    #: (a nightly's tag ``nightly-YYYYMMDD`` is not its version).
    version: str | None = None
