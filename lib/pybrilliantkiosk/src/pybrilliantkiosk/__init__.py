"""Async client for Brilliant kiosk panels."""

from .bundle import kiosk_env_from_bundle
from .bus import parse_listing, parse_set_result, parse_variables
from .env import EnvFile, is_secret
from .exceptions import (
    ActivationRolledBack,
    AuthFailed,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    HostKeyMismatch,
    InvalidArgument,
    NotInstalled,
    PanelError,
    ReleaseError,
    SettingsRejected,
    SetupBusy,
    SetupError,
    StockUIRestored,
    VerificationFailed,
)
from .models import (
    OVERRIDE_KEYS,
    UNITS,
    BusListing,
    BusPeripheral,
    BusVariable,
    PanelHealth,
    Release,
)
from .panel import Panel
from .releases import Releases
from .setup import SetupStatus

__version__ = "0.1.0"

__all__ = [
    "OVERRIDE_KEYS",
    "UNITS",
    "ActivationRolledBack",
    "AuthFailed",
    "BusListing",
    "BusPeripheral",
    "BusVariable",
    "CannotConnect",
    "ChecksumMismatch",
    "CommandFailed",
    "EnvFile",
    "HostKeyMismatch",
    "InvalidArgument",
    "NotInstalled",
    "Panel",
    "PanelError",
    "PanelHealth",
    "Release",
    "ReleaseError",
    "Releases",
    "SettingsRejected",
    "SetupBusy",
    "SetupError",
    "SetupStatus",
    "StockUIRestored",
    "VerificationFailed",
    "__version__",
    "is_secret",
    "kiosk_env_from_bundle",
    "parse_listing",
    "parse_set_result",
    "parse_variables",
]
