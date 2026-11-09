"""Async client for Brilliant kiosk panels."""

from .bus import parse_listing, parse_set_result, parse_variables
from .env import EnvFile, is_secret
from .exceptions import (
    AuthFailed,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    HostKeyMismatch,
    InvalidArgument,
    PanelError,
    ReleaseError,
)
from .models import (
    UNITS,
    BusListing,
    BusPeripheral,
    BusVariable,
    PanelHealth,
    Release,
)
from .panel import Panel
from .releases import Releases

__version__ = "0.1.0"

__all__ = [
    "UNITS",
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
    "Panel",
    "PanelError",
    "PanelHealth",
    "Release",
    "ReleaseError",
    "Releases",
    "__version__",
    "is_secret",
    "parse_listing",
    "parse_set_result",
    "parse_variables",
]
