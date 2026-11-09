"""Translate library errors into Home Assistant exceptions (exception-translations)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pybrilliantkiosk import (
    ActivationRolledBack,
    AuthFailed,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    ConcurrentModification,
    DeviceIdUnavailable,
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

from .const import DOMAIN


class InstallRefused(PanelError):
    """An install this integration will not do; ``key`` is the translation key of the reason."""

    def __init__(self, key: str, message: str, tag: str) -> None:
        super().__init__(message)
        self.key = key
        self.tag = tag


def to_ha_error(err: PanelError) -> HomeAssistantError:
    """Map a library error to a translated Home Assistant error."""
    if isinstance(err, InstallRefused):
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=err.key,
            translation_placeholders={"error": str(err), "tag": err.tag},
        )
    if isinstance(err, InvalidArgument):
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_argument",
            translation_placeholders={"error": str(err)},
        )
    keys: tuple[tuple[type[PanelError], str], ...] = (
        (AuthFailed, "invalid_auth"),
        (HostKeyMismatch, "host_key_mismatch"),
        (SetupBusy, "setup_busy"),
        (VerificationFailed, "verification_failed"),
        (SettingsRejected, "settings_rejected"),
        (ActivationRolledBack, "activation_rolled_back"),
        (StockUIRestored, "stock_ui_restored"),
        (NotInstalled, "not_installed"),
        (SetupError, "setup_failed"),
        (DeviceIdUnavailable, "device_id_unavailable"),
        (ConcurrentModification, "concurrent_modification"),
        (CannotConnect, "cannot_connect"),
        (ChecksumMismatch, "checksum_mismatch"),
        (ReleaseError, "release_error"),
        (CommandFailed, "command_failed"),
    )
    key = next((k for cls, k in keys if isinstance(err, cls)), "unknown_error")
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"error": str(err)},
    )


@contextmanager
def panel_errors() -> Iterator[None]:
    """Raise translated Home Assistant errors for any library error in the block."""
    try:
        yield
    except PanelError as err:
        raise to_ha_error(err) from err
