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


class InvalidArgument(PanelError, ValueError):
    """A caller-supplied value (unit, key, peripheral name, ...) was rejected."""


class ChecksumMismatch(PanelError):
    """A bundle's SHA-256 does not match the expected value."""


class ReleaseError(PanelError):
    """Listing or downloading GitHub releases failed."""
