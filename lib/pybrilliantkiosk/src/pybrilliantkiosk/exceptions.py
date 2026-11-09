"""Exceptions raised by pybrilliantkiosk."""

from __future__ import annotations


class PanelError(Exception):
    """Base class for all library errors."""


class CannotConnect(PanelError):
    """The panel could not be reached (network, timeout, connection lost)."""


class AuthFailed(PanelError):
    """The panel rejected the credentials."""


class HostKeyMismatch(PanelError):
    """The panel's SSH host key does not match the pinned key."""

    def __init__(self, message: str, fingerprint: str | None = None) -> None:
        super().__init__(message)
        self.fingerprint = fingerprint


class CommandFailed(PanelError):
    """A remote command exited with a non-zero status."""

    def __init__(self, command: str, exit_status: int | None, stderr: str) -> None:
        super().__init__(f"command failed with exit status {exit_status}: {stderr.strip()}")
        self.command = command
        self.exit_status = exit_status
        self.stderr = stderr


class DeviceIdUnavailable(PanelError):
    """Brilliant's device id of the panel could not be read from the stock message bus."""


class InvalidArgument(PanelError, ValueError):
    """A caller-supplied value (unit, key, peripheral name, ...) was rejected."""


class ChecksumMismatch(PanelError):
    """A bundle's SHA-256 (or size) does not match the expected value."""


class ConcurrentModification(PanelError):
    """``kiosk.env`` changed on the panel between reading it and writing it; nothing was written."""


class ReleaseError(PanelError):
    """Listing or downloading GitHub releases failed."""


class SetupError(PanelError):
    """``ha-kiosk-setup`` failed; ``code`` is its exit status (docs/INTEGRATION.md section 0)."""

    code = 1

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class VerificationFailed(SetupError):
    """Exit 3: the bundle's SHA-256, size or manifest did not verify."""

    code = 3


class SettingsRejected(SetupError):
    """Exit 4: the settings or token were rejected by the panel's validator."""

    code = 4


class ActivationRolledBack(SetupError):
    """Exit 5: activation failed and the previous version was restored."""

    code = 5


class StockUIRestored(SetupError):
    """Exit 6: activation failed and Brilliant's stock UI was restored."""

    code = 6


class NotInstalled(SetupError):
    """Exit 7: the kiosk is not installed (or not installed with ``ha-kiosk-setup``)."""

    code = 7


class SetupBusy(SetupError):
    """Exit 8: another setup run or detached job is active."""

    code = 8
