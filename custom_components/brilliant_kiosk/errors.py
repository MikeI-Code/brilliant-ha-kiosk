"""Translate library errors into Home Assistant exceptions (exception-translations)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pybrilliantkiosk import (
    AuthFailed,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    HostKeyMismatch,
    InvalidArgument,
    PanelError,
    ReleaseError,
    SetupBusy,
)

from .const import DOMAIN


def to_ha_error(err: PanelError) -> HomeAssistantError:
    """Map a library error to a translated Home Assistant error."""
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
