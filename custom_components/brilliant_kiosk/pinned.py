"""Pinned stable releases: the integration's own root of trust for what it installs as root.

Installing a kiosk bundle runs code as root on a panel, so the bundle must be verified against
something that does not move with the GitHub release page. GitHub's ``.sha256`` asset is no such
thing: whoever can replace the bundle can replace that file too. Each stable release is
therefore listed here with the SHA-256 and size of its bundle, shipped inside this integration
and reviewed with it.

**What the pin is, and is not.** It is trust on first use: the values below are a snapshot taken
from GitHub (the release assets of github.com/MikeI-Code/brilliant-ha-kiosk) on 2026-11-09 and
checked three ways (the API's asset ``digest`` and ``size``, the ``.sha256`` asset, and a local
``sha256sum`` / ``wc -c`` of the downloaded bundle; all three agree). It is not independent of
GitHub at the moment of the snapshot: a release already tampered with then would have been
pinned. What it does give is that a release replaced on GitHub *afterwards* no longer changes
what this integration accepts, and that a change has to go through a reviewed update of the
integration.

* **Stable channel:** a release is installed only if its tag is in :data:`PINNED_RELEASES` and the
  download matches. A release the table does not know is refused (``release_not_pinned``): it is
  offered by the update entity, but installing it needs a newer version of this integration.
* **Nightly channel:** nightlies are built every night and cannot be pinned in advance. They are
  installed only when the user opted into the nightly channel, and verified against the
  release's ``.sha256`` asset. That proves the download is intact, NOT who published it, so it is
  weaker than a pin. Never use the nightly channel on a panel you cannot afford to break.

To add a release: download ``brilliant-ha-kiosk-<tag>.tar.gz``, compute ``sha256sum`` and the size
yourself, cross-check them with the ``.sha256`` asset and the GitHub API's asset ``digest``, then
add a line below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PinnedRelease:
    """The expected SHA-256 and size (bytes) of a stable release's bundle."""

    sha256: str
    size: int


#: Stable releases by tag. v1.4.0: a trust-on-first-use snapshot taken from GitHub on 2026-11-09:
#: sha256 and size from the release assets (API ``digest`` and ``size``), the ``.sha256`` asset and
#: a local ``sha256sum``/``wc -c`` of the downloaded bundle; all three agree.
#: v1.5.0 (2026-11-10): the ``.sha256`` asset and a ``sha256sum``/``wc -c`` of the downloaded
#: bundle agree, and an independent Linux build of the tag has byte-identical contents (74 files;
#: the archive itself differs only in file modes, which the build does not pin).
PINNED_RELEASES: Mapping[str, PinnedRelease] = {
    "v1.4.0": PinnedRelease(
        sha256="6ea396194a70a5e130325fb21c1c9c24f310a95043c32bf704aa8fd9d8b85cf1",
        size=774344,
    ),
    "v1.5.0": PinnedRelease(
        sha256="468fcfd6dda0dadac870a5ab06e20a21045d6606dc010752266be67e07252856",
        size=823449,
    ),
}
