"""Which releases may be installed, and how a bundle is verified before it goes to a panel."""

from __future__ import annotations

from pybrilliantkiosk import Release, Releases

from .errors import InstallRefused
from .pinned import PINNED_RELEASES


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
