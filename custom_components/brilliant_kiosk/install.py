"""Which releases may be installed, and how a bundle is verified before it goes to a panel."""

from __future__ import annotations

from collections.abc import Iterable

from pybrilliantkiosk import Release, Releases, is_newer, parse_version

from .errors import InstallRefused
from .pinned import PINNED_RELEASES


def is_installable(release: Release) -> bool:
    """Whether :func:`async_fetch_bundle` could accept ``release``: a nightly, or a pinned stable.

    A nightly is accepted subject to the channel (checked at install time); a stable release only
    when its tag is in :data:`~.pinned.PINNED_RELEASES`.
    """
    return release.prerelease or release.tag in PINNED_RELEASES


def newest_unverifiable_stable(releases: Iterable[Release]) -> Release | None:
    """The newest stable release that is newer than every pinned one and not pinned itself.

    Such a release needs a newer version of this integration before a panel can install it. A
    stable release whose tag is no version, or that is older than a pinned one, is ignored (an
    old release that was never pinned is not a reason to update).
    """
    pinned = [tag for tag in PINNED_RELEASES if parse_version(tag) is not None]
    candidates = [
        (version, release)
        for release in releases
        if not release.prerelease
        and release.tag not in PINNED_RELEASES
        and (version := parse_version(release.tag)) is not None
        and all(is_newer(release.tag, tag) for tag in pinned)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


async def async_fetch_bundle(
    client: Releases, release: Release, *, nightly_allowed: bool
) -> tuple[bytes, str, int]:
    """Download ``release`` and verify it; returns ``(bundle, sha256, size)``.

    A stable release must be in :data:`~.pinned.PINNED_RELEASES` and match the pinned SHA-256 and
    size, so an attacker who controls the GitHub release page cannot get code installed on a
    panel. A nightly is accepted only when ``nightly_allowed`` (the user chose the nightly
    channel) and is verified against its ``.sha256`` asset, which is weaker (see ``pinned.py``).
    The returned size is the pinned one for a stable release, the download length for a nightly.

    Raises :class:`InstallRefused` for a release that may not be installed, and the library's
    ``ReleaseError`` / ``ChecksumMismatch`` for a failed or mismatching download.
    """
    if release.prerelease:
        if not nightly_allowed:
            raise InstallRefused(
                "nightly_not_allowed",
                f"{release.tag} is a nightly build and the nightly channel is not selected",
                release.tag,
            )
        bundle, sha256 = await client.download(release)
        return bundle, sha256, len(bundle)
    pinned = PINNED_RELEASES.get(release.tag)
    if pinned is None:
        raise InstallRefused(
            "release_not_pinned",
            f"{release.tag} is not in this integration's list of verified releases",
            release.tag,
        )
    bundle, sha256 = await client.download(release, sha256=pinned.sha256, size=pinned.size)
    return bundle, sha256, pinned.size
