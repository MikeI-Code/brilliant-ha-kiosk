"""Ordering of kiosk bundle versions.

Bundle VERSION strings (``.github/workflows/nightly.yml`` and ``release.yml``) are

* stable:  ``v1.4.0``
* nightly: ``v1.4.0-nightly.20261108.g89ec239`` (``<last stable>-nightly.<UTC date>.g<sha>``)

Ordering: a nightly is newer than the stable release it is built on and older than the next
stable one (``v1.4.0 < v1.4.0-nightly.* < v1.5.0``); nightlies order by date. Two builds of the
same day compare equal, so a second same-day build is not offered as an update.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-nightly\.(?P<date>\d{8})(?:\.g[0-9a-f]+)?)?$"
)


@dataclass(frozen=True, slots=True, order=True)
class KioskVersion:
    """A parsed bundle version, ordered as described in the module docstring."""

    major: int
    minor: int
    patch: int
    #: 0 for a stable release, 1 for a nightly built on top of it.
    nightly: int = 0
    #: Build date (YYYYMMDD) of a nightly, else 0.
    date: int = 0


def parse_version(text: str | None) -> KioskVersion | None:
    """Parse a bundle version or stable tag; ``None`` if it has an unknown format."""
    match = _VERSION_RE.match((text or "").strip())
    if match is None:
        return None
    date = match["date"]
    return KioskVersion(
        int(match["major"]),
        int(match["minor"]),
        int(match["patch"]),
        1 if date else 0,
        int(date) if date else 0,
    )


def is_newer(candidate: str | None, installed: str | None) -> bool:
    """True only if both versions parse and ``candidate`` is strictly newer (never a downgrade)."""
    new, old = parse_version(candidate), parse_version(installed)
    return new is not None and old is not None and new > old
