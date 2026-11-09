"""GitHub releases of the kiosk bundle."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

import aiohttp

from .bundle import sha256_hex
from .exceptions import ChecksumMismatch, InvalidArgument, ReleaseError
from .models import Release

DEFAULT_REPO = "MikeI-Code/brilliant-ha-kiosk"
_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
#: Bundle asset name of scripts/build-bundle.sh: the part between prefix and suffix is VERSION.
_BUNDLE_NAME = re.compile(r"^brilliant-ha-kiosk-(?P<version>.+)\.tar\.gz$")
#: Largest bundle :meth:`Releases.download` accepts (a release is under 1 MB today).
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
#: A ``.sha256`` asset is one line; anything bigger is not one.
_MAX_SHA_BYTES = 4096
_CHUNK_BYTES = 64 * 1024


class Releases:
    """Lists and downloads releases using an injected :class:`aiohttp.ClientSession`."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        repo: str = DEFAULT_REPO,
        *,
        timeout: float = 60.0,
        download_timeout: float = 300.0,
    ) -> None:
        self._session = session
        self._repo = repo
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._download_timeout = aiohttp.ClientTimeout(total=download_timeout)

    async def _fetch(
        self,
        url: str,
        *,
        json_accept: bool = False,
        client_timeout: aiohttp.ClientTimeout | None = None,
        allow_missing: bool = False,
    ) -> aiohttp.ClientResponse | None:
        headers = {"Accept": "application/vnd.github+json"} if json_accept else {}
        try:
            resp = await self._session.get(
                url, headers=headers, timeout=client_timeout or self._timeout
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ReleaseError(f"request to {url} failed: {err}") from err
        if resp.status == 404 and allow_missing:
            resp.release()
            return None
        if resp.status != 200:
            resp.release()
            raise ReleaseError(f"{url} returned HTTP {resp.status}")
        return resp

    async def _json(self, url: str, *, allow_missing: bool = False) -> Any:
        resp = await self._fetch(url, json_accept=True, allow_missing=allow_missing)
        if resp is None:
            return None
        try:
            return await resp.json()
        except (aiohttp.ClientError, ValueError, TimeoutError) as err:
            raise ReleaseError(f"invalid response from {url}: {err}") from err

    async def _read_capped(self, url: str, limit: int) -> bytes:
        """The body of ``url``, streamed; more than ``limit`` bytes is an error, not a download."""
        resp = await self._fetch(url, client_timeout=self._download_timeout)
        assert resp is not None
        if resp.content_length is not None and resp.content_length > limit:
            resp.release()
            raise ReleaseError(f"{url} is {resp.content_length} bytes, more than the {limit} limit")
        data = bytearray()
        try:
            async for chunk in resp.content.iter_chunked(_CHUNK_BYTES):
                data += chunk
                if len(data) > limit:
                    resp.release()
                    raise ReleaseError(f"{url} is larger than the {limit} byte limit")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ReleaseError(f"download failed: {err}") from err
        return bytes(data)

    async def list(self, *, include_prereleases: bool = True) -> tuple[Release, ...]:
        """Published releases that ship a ``.tar.gz`` bundle, in GitHub's order (newest first).

        Only the newest 30 releases are listed; with a daily nightly an older stable release can
        fall off the end, so use :meth:`latest_stable` to find the newest stable release.
        """
        data = await self._json(f"https://api.github.com/repos/{self._repo}/releases?per_page=30")
        if not isinstance(data, list):
            raise ReleaseError("unexpected release list format")
        releases = [r for item in data if (r := _parse_release(item)) is not None]
        if not include_prereleases:
            releases = [r for r in releases if not r.prerelease]
        return tuple(releases)

    async def latest_stable(self) -> Release | None:
        """The release GitHub marks *Latest*: the newest published non-prerelease, whatever its age.

        ``None`` when there is none (or it ships no bundle).
        """
        data = await self._json(
            f"https://api.github.com/repos/{self._repo}/releases/latest", allow_missing=True
        )
        return None if data is None else _parse_release(data)

    async def download(
        self,
        release: Release,
        *,
        sha256: str | None = None,
        size: int | None = None,
        max_size: int = MAX_BUNDLE_BYTES,
    ) -> tuple[bytes, str]:
        """Download the bundle (streamed, at most ``max_size`` bytes) and verify it.

        With ``sha256`` (and ``size``) from a source independent of GitHub, such as a table
        pinned in the integration, the bundle is checked against those and the release's own
        ``.sha256`` asset is not used. Without them the bundle is checked against that asset,
        which proves the download was intact but not who published it: an attacker who can
        publish a release can publish a matching ``.sha256`` too.
        """
        if sha256 is not None:
            if not _SHA256_RE.match(sha256):
                raise InvalidArgument("sha256 must be 64 hex characters")
            expected = sha256.lower()
        else:
            if release.sha256_url is None:
                raise ReleaseError(f"release {release.tag} has no .sha256 asset to verify against")
            text = (await self._read_capped(release.sha256_url, _MAX_SHA_BYTES)).decode(
                "utf-8", "replace"
            )
            match = _HEX64.search(text)
            if match is None:
                raise ReleaseError(f"no sha256 found in {release.sha256_url}")
            expected = match.group(0).lower()
        limit = max_size if size is None else min(max_size, size)
        bundle = await self._read_capped(release.bundle_url, limit)
        if size is not None and len(bundle) != size:
            raise ChecksumMismatch(f"bundle is {len(bundle)} bytes, expected {size}")
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
