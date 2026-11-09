"""GitHub releases of the kiosk bundle."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

import aiohttp

from .bundle import sha256_hex
from .exceptions import ChecksumMismatch, ReleaseError
from .models import Release

DEFAULT_REPO = "MikeI-Code/brilliant-ha-kiosk"
_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
#: Bundle asset name of scripts/build-bundle.sh: the part between prefix and suffix is VERSION.
_BUNDLE_NAME = re.compile(r"^brilliant-ha-kiosk-(?P<version>.+)\.tar\.gz$")


class Releases:
    """Lists and downloads releases using an injected :class:`aiohttp.ClientSession`."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        repo: str = DEFAULT_REPO,
        *,
        timeout: float = 60.0,
    ) -> None:
        self._session = session
        self._repo = repo
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _fetch(self, url: str, *, json_accept: bool = False) -> aiohttp.ClientResponse:
        headers = {"Accept": "application/vnd.github+json"} if json_accept else {}
        try:
            resp = await self._session.get(url, headers=headers, timeout=self._timeout)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ReleaseError(f"request to {url} failed: {err}") from err
        if resp.status != 200:
            resp.release()
            raise ReleaseError(f"{url} returned HTTP {resp.status}")
        return resp

    async def list(self, *, include_prereleases: bool = True) -> tuple[Release, ...]:
        """Published releases that ship a ``.tar.gz`` bundle, in GitHub's order (newest first)."""
        url = f"https://api.github.com/repos/{self._repo}/releases?per_page=30"
        resp = await self._fetch(url, json_accept=True)
        try:
            data = await resp.json()
        except (aiohttp.ClientError, ValueError, TimeoutError) as err:
            raise ReleaseError(f"invalid release list: {err}") from err
        if not isinstance(data, list):
            raise ReleaseError("unexpected release list format")
        releases = [r for item in data if (r := _parse_release(item)) is not None]
        if not include_prereleases:
            releases = [r for r in releases if not r.prerelease]
        return tuple(releases)

    async def download(self, release: Release) -> tuple[bytes, str]:
        """Download the bundle and verify it against the release's ``.sha256`` asset."""
        if release.sha256_url is None:
            raise ReleaseError(f"release {release.tag} has no .sha256 asset to verify against")
        try:
            sha_resp = await self._fetch(release.sha256_url)
            match = _HEX64.search(await sha_resp.text())
            if match is None:
                raise ReleaseError(f"no sha256 found in {release.sha256_url}")
            expected = match.group(0).lower()
            bundle = await (await self._fetch(release.bundle_url)).read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ReleaseError(f"download failed: {err}") from err
        actual = await asyncio.to_thread(sha256_hex, bundle)
        if actual != expected:
            raise ChecksumMismatch(f"bundle sha256 {actual} != expected {expected}")
        return bundle, actual


def _parse_release(item: Any) -> Release | None:
    if not isinstance(item, dict) or item.get("draft"):
        return None
    bundle_url: str | None = None
    sha_url: str | None = None
    version: str | None = None
    for asset in item.get("assets") or []:
        name = str(asset.get("name", ""))
        url = asset.get("browser_download_url")
        if not isinstance(url, str):
            continue
        if name.endswith(".tar.gz.sha256"):
            sha_url = url
        elif name.endswith(".tar.gz"):
            bundle_url = url
            named = _BUNDLE_NAME.match(name)
            version = named["version"] if named else None
    tag = item.get("tag_name")
    if bundle_url is None or not isinstance(tag, str):
        return None
    published: datetime | None = None
    raw_date = item.get("published_at")
    if isinstance(raw_date, str):
        try:
            published = datetime.fromisoformat(raw_date)
        except ValueError:
            published = None
    return Release(
        tag=tag,
        prerelease=bool(item.get("prerelease")),
        published_at=published,
        body=str(item.get("body") or ""),
        bundle_url=bundle_url,
        sha256_url=sha_url,
        version=version,
    )
