"""Helpers for release bundles."""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Mapping

from .env import EnvFile, format_value, parse_setup_keys, render_line, validate_setting_key
from .exceptions import InvalidArgument, ReleaseError

EXAMPLE_NAME = "kiosk.env.example"
SETUP_NAME = "ha-kiosk-setup"


def _read_member(tar: tarfile.TarFile, name: str) -> str | None:
    """Text of ``<top directory>/<name>`` in the bundle (None if it is not there)."""
    member = next(
        (
            m
            for m in tar.getmembers()
            if m.isfile() and m.name.count("/") == 1 and m.name.endswith(f"/{name}")
        ),
        None,
    )
    if member is None or (handle := tar.extractfile(member)) is None:
        return None
    return handle.read().decode("utf-8", errors="replace")


def kiosk_env_from_bundle(bundle: bytes, overrides: Mapping[str, str]) -> str:
    """``kiosk.env`` text built from the bundle's ``kiosk.env.example`` plus ``overrides``.

    The settings are validated against the ``KEYS`` of the ``ha-kiosk-setup`` inside the bundle
    (the tool that will install them), not against this library's own list, so a bundle with a new
    setting installs with that setting. Nothing is dropped silently: a setting the bundle's tool
    does not know, or whose value it would refuse, raises :class:`InvalidArgument` naming it.
    Lines are rendered ``KEY="value"`` like ``ha-kiosk-setup`` does. CPU-bound (gunzip): call it
    from an executor in async code.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
            example = _read_member(tar, EXAMPLE_NAME)
            setup = _read_member(tar, SETUP_NAME)
    except tarfile.TarError as err:
        raise ReleaseError(f"unreadable bundle: {err}") from err
    if example is None:
        raise ReleaseError(f"bundle has no {EXAMPLE_NAME}")
    if setup is None:
        raise ReleaseError(f"bundle has no {SETUP_NAME}")
    allowed = parse_setup_keys(setup)
    if allowed is None:
        raise ReleaseError(f"cannot read the list of settings from the bundle's {SETUP_NAME}")
    values: dict[str, str] = {}
    for key, value in EnvFile.parse(example).values().items():
        try:
            values[validate_setting_key(key, allowed)] = format_value(value)
        except InvalidArgument as err:
            raise InvalidArgument(f"{EXAMPLE_NAME} of the bundle: {key}: {err}") from err
    for key, value in overrides.items():
        values[validate_setting_key(key, allowed)] = format_value(value)
    return "".join(f"{render_line(k, v)}\n" for k, v in values.items())


def sha256_hex(data: bytes) -> str:
    """SHA-256 of ``data`` as hex (CPU-bound for big bundles: use ``asyncio.to_thread``)."""
    return hashlib.sha256(data).hexdigest()
