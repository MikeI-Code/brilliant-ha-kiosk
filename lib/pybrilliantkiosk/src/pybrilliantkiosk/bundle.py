"""Helpers for release bundles."""

from __future__ import annotations

import io
import tarfile
from collections.abc import Mapping

from .env import EnvFile, format_value, validate_setting_key
from .exceptions import InvalidArgument, ReleaseError

EXAMPLE_NAME = "kiosk.env.example"


def kiosk_env_from_bundle(bundle: bytes, overrides: Mapping[str, str]) -> str:
    """``kiosk.env`` text built from the bundle's ``kiosk.env.example`` plus ``overrides``.

    Only settings the panel's validator accepts are kept; lines it would reject are dropped.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
            member = next(
                (m for m in tar.getmembers() if m.isfile() and m.name.endswith(f"/{EXAMPLE_NAME}")),
                None,
            )
            if member is None or (handle := tar.extractfile(member)) is None:
                raise ReleaseError(f"bundle has no {EXAMPLE_NAME}")
            text = handle.read().decode("utf-8", errors="replace")
    except tarfile.TarError as err:
        raise ReleaseError(f"unreadable bundle: {err}") from err
    values: dict[str, str] = {}
    for key, value in EnvFile.parse(text).values().items():
        try:
            values[validate_setting_key(key)] = format_value(value)
        except InvalidArgument:
            continue
    for key, value in overrides.items():
        values[validate_setting_key(key)] = format_value(value)
    return "".join(f"{k}={v}\n" for k, v in values.items())
