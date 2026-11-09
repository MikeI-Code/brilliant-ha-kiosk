import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import aiohttp
import pytest

from pybrilliantkiosk import ChecksumMismatch, InvalidArgument, Release, ReleaseError, Releases

URL = "https://api.github.com/repos/MikeI-Code/brilliant-ha-kiosk/releases?per_page=30"
LATEST = "https://api.github.com/repos/MikeI-Code/brilliant-ha-kiosk/releases/latest"
BUNDLE = b"bundle-bytes"
SHA = hashlib.sha256(BUNDLE).hexdigest()


class FakeContent:
    def __init__(self, body: bytes | BaseException) -> None:
        self._body = body

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        if isinstance(self._body, BaseException):
            raise self._body
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes | BaseException = b"",
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.content = FakeContent(body)
        if content_length is None and isinstance(body, bytes):
            content_length = len(body)
        self.content_length = content_length
        self.released = False

    def release(self) -> None:
        self.released = True

    async def json(self) -> Any:
        assert isinstance(self._body, bytes)
        return json.loads(self._body)


class FakeSession:
    """Minimal stand-in for aiohttp.ClientSession.get."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, headers: dict[str, str], timeout: Any) -> FakeResponse:  # noqa: ASYNC109
        self.calls.append((url, headers))
        result = self.routes[url]
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[no-any-return]


def ok(payload: Any) -> FakeResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return FakeResponse(200, body)


def releases_api(session: FakeSession) -> Releases:
    return Releases(session)  # type: ignore[arg-type]


def gh_release(tag, *, prerelease=False, draft=False, assets=True, date="2026-11-01T10:00:00Z"):
    base = f"https://github.com/x/y/releases/download/{tag}/kiosk-{tag}.tar.gz"
    items = (
        [
            {"name": f"kiosk-{tag}.tar.gz", "browser_download_url": base},
            {"name": f"kiosk-{tag}.tar.gz.sha256", "browser_download_url": base + ".sha256"},
            {"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
            {"name": "broken.tar.gz"},
        ]
        if assets
        else []
    )
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "published_at": date,
        "body": "notes",
        "assets": items,
    }


async def test_list():
    payload = [
        gh_release("v1.5.0"),
        gh_release("nightly-1", prerelease=True),
        gh_release("draft", draft=True),
        gh_release("noassets", assets=False),
        gh_release("baddate", date="nope"),
        {"tag_name": 5, "assets": []},
        "junk",
    ]
    session = FakeSession({URL: ok(payload)})
    releases = await releases_api(session).list()
    assert session.calls[0][1] == {"Accept": "application/vnd.github+json"}
    assert [r.tag for r in releases] == ["v1.5.0", "nightly-1", "baddate"]
    first = releases[0]
    assert first.published_at == datetime.fromisoformat("2026-11-01T10:00:00Z")
    assert first.bundle_url.endswith(".tar.gz")
    assert first.sha256_url is not None
    assert first.sha256_url.endswith(".sha256")
    assert first.body == "notes"
    assert not first.prerelease
    assert releases[1].prerelease
    assert releases[2].published_at is None


async def test_list_stable_only_and_custom_repo():
    url = URL.replace("MikeI-Code/brilliant-ha-kiosk", "o/r")
    session = FakeSession({url: ok([gh_release("v1"), gh_release("n", prerelease=True)])})
    releases = await Releases(session, "o/r").list(include_prereleases=False)  # type: ignore[arg-type]
    assert [r.tag for r in releases] == ["v1"]


async def test_list_errors():
    bad_status = FakeResponse(403)
    session = FakeSession({URL: bad_status})
    with pytest.raises(ReleaseError, match="403"):
        await releases_api(session).list()
    assert bad_status.released

    for exc in (aiohttp.ClientConnectionError("down"), TimeoutError()):
        with pytest.raises(ReleaseError):
            await releases_api(FakeSession({URL: exc})).list()

    with pytest.raises(ReleaseError, match="format"):
        await releases_api(FakeSession({URL: ok({"message": "x"})})).list()
    with pytest.raises(ReleaseError, match="invalid"):
        await releases_api(FakeSession({URL: ok(b"not json")})).list()


def release(sha_url="https://x/b.sha256"):
    return Release("v1", False, None, "", "https://x/b.tar.gz", sha_url)


async def test_download():
    session = FakeSession(
        {
            "https://x/b.sha256": ok(f"{SHA.upper()}  kiosk-v1.tar.gz\n".encode()),
            "https://x/b.tar.gz": ok(BUNDLE),
        }
    )
    data, sha = await releases_api(session).download(release())
    assert data == BUNDLE
    assert sha == SHA


async def test_download_mismatch():
    session = FakeSession({"https://x/b.sha256": ok(b"0" * 64), "https://x/b.tar.gz": ok(BUNDLE)})
    with pytest.raises(ChecksumMismatch):
        await releases_api(session).download(release())


async def test_download_errors():
    api = releases_api(FakeSession({}))
    with pytest.raises(ReleaseError, match=r"no .sha256"):
        await api.download(release(sha_url=None))

    with pytest.raises(ReleaseError, match="no sha256"):
        await releases_api(FakeSession({"https://x/b.sha256": ok(b"no hash here")})).download(
            release()
        )

    routes = {"https://x/b.sha256": ok(SHA.encode()), "https://x/b.tar.gz": FakeResponse(404)}
    with pytest.raises(ReleaseError, match="404"):
        await releases_api(FakeSession(routes)).download(release())

    routes = {
        "https://x/b.sha256": ok(SHA.encode()),
        "https://x/b.tar.gz": FakeResponse(200, aiohttp.ClientPayloadError("cut")),  # type: ignore[arg-type]
    }
    with pytest.raises(ReleaseError):
        await releases_api(FakeSession(routes)).download(release())


def bundle_release(tag: str, version: str, *, prerelease: bool = False) -> dict[str, Any]:
    name = f"brilliant-ha-kiosk-{version}.tar.gz"
    base = f"https://github.com/x/y/releases/download/{tag}/{name}"
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "published_at": "2026-11-08T06:00:00Z",
        "body": "",
        "assets": [
            {"name": name, "browser_download_url": base},
            {"name": name + ".sha256", "browser_download_url": base + ".sha256"},
        ],
    }


async def test_release_version_comes_from_the_bundle_asset_name_not_the_tag():
    payload = [
        bundle_release("nightly-20261108", "v1.4.0-nightly.20261108.g89ec239", prerelease=True),
        bundle_release("v1.4.0", "v1.4.0"),
        gh_release("odd-asset-name"),  # asset not called brilliant-ha-kiosk-<VERSION>.tar.gz
    ]
    releases = await releases_api(FakeSession({URL: ok(payload)})).list()
    assert [(r.tag, r.version) for r in releases] == [
        ("nightly-20261108", "v1.4.0-nightly.20261108.g89ec239"),
        ("v1.4.0", "v1.4.0"),
        ("odd-asset-name", None),
    ]


async def test_download_hashes_off_the_event_loop(monkeypatch: pytest.MonkeyPatch):
    import threading

    from pybrilliantkiosk import releases as releases_module

    threads: list[str] = []
    real = releases_module.sha256_hex

    def spy(data: bytes) -> str:
        threads.append(threading.current_thread().name)
        return real(data)

    monkeypatch.setattr(releases_module, "sha256_hex", spy)
    routes = {"https://x/b.sha256": ok(SHA.encode()), "https://x/b.tar.gz": ok(BUNDLE)}
    await releases_api(FakeSession(routes)).download(release())
    assert threads
    assert threading.main_thread().name not in threads


# -- stable via /releases/latest ------------------------------------------------------------------


async def test_latest_stable_uses_the_latest_endpoint_not_the_first_page():
    session = FakeSession({LATEST: ok(bundle_release("v1.4.0", "v1.4.0"))})
    release_ = await releases_api(session).latest_stable()
    assert release_ is not None
    assert (release_.tag, release_.version, release_.prerelease) == ("v1.4.0", "v1.4.0", False)
    assert [url for url, _ in session.calls] == [LATEST]  # one request, wherever the stable is
    assert session.calls[0][1] == {"Accept": "application/vnd.github+json"}


async def test_latest_stable_none_when_github_has_no_such_release_or_no_bundle():
    missing = FakeResponse(404)
    assert await releases_api(FakeSession({LATEST: missing})).latest_stable() is None
    assert missing.released
    no_bundle = ok(gh_release("v1", assets=False))
    assert await releases_api(FakeSession({LATEST: no_bundle})).latest_stable() is None


async def test_latest_stable_errors():
    with pytest.raises(ReleaseError, match="500"):
        await releases_api(FakeSession({LATEST: FakeResponse(500)})).latest_stable()
    with pytest.raises(ReleaseError, match="invalid"):
        await releases_api(FakeSession({LATEST: ok(b"not json")})).latest_stable()


# -- pinned downloads, streaming and the size cap -------------------------------------------------


def pinned_session(body: bytes | BaseException = BUNDLE, length: int | None = None) -> FakeSession:
    # No route for the .sha256 asset: a pinned download must never ask GitHub for it.
    return FakeSession({"https://x/b.tar.gz": FakeResponse(200, body, length)})


async def test_pinned_download_ignores_the_release_checksum_asset():
    session = pinned_session()
    data, sha = await releases_api(session).download(
        release(), sha256=SHA.upper(), size=len(BUNDLE)
    )
    assert (data, sha) == (BUNDLE, SHA)
    assert [url for url, _ in session.calls] == ["https://x/b.tar.gz"]


async def test_pinned_download_works_for_a_release_without_a_checksum_asset():
    data, _ = await releases_api(pinned_session()).download(release(sha_url=None), sha256=SHA)
    assert data == BUNDLE


async def test_a_bundle_the_github_checksum_vouches_for_still_fails_the_pin():
    """The attack the pin exists for: a modified bundle with a matching .sha256 asset."""
    evil = b"evil-bundle"
    session = FakeSession(
        {
            "https://x/b.sha256": ok(hashlib.sha256(evil).hexdigest().encode()),
            "https://x/b.tar.gz": ok(evil),
        }
    )
    # Unpinned (nightly): GitHub's own checksum accepts it.
    assert (await releases_api(session).download(release()))[0] == evil
    # Pinned (stable): the independent hash does not.
    with pytest.raises(ChecksumMismatch):
        await releases_api(session).download(release(), sha256=SHA, size=len(evil))


async def test_pinned_download_checks_size_and_hash():
    api = releases_api(pinned_session())
    with pytest.raises(ChecksumMismatch, match="expected 99"):
        await api.download(release(), sha256=SHA, size=99)
    with pytest.raises(ChecksumMismatch, match="sha256"):
        await api.download(release(), sha256="0" * 64, size=len(BUNDLE))
    with pytest.raises(InvalidArgument):
        await api.download(release(), sha256="nope")


async def test_download_is_streamed_in_chunks():
    big = b"x" * (3 * 64 * 1024 + 5)
    api = releases_api(pinned_session(big))
    data, _ = await api.download(release(), sha256=hashlib.sha256(big).hexdigest())
    assert data == big


async def test_download_refuses_a_body_over_the_cap_while_streaming():
    session = pinned_session(b"x" * 100, length=None)
    session.routes["https://x/b.tar.gz"].content_length = None  # a server that sends no length
    with pytest.raises(ReleaseError, match="larger than"):
        await releases_api(session).download(release(), sha256=SHA, max_size=50)
    assert session.routes["https://x/b.tar.gz"].released


async def test_download_refuses_a_declared_length_over_the_cap_without_reading():
    response = FakeResponse(200, BaseException("must not be read"), content_length=10**9)
    session = FakeSession({"https://x/b.tar.gz": response})
    with pytest.raises(ReleaseError, match="more than the"):
        await releases_api(session).download(release(), sha256=SHA)
    assert response.released


async def test_default_cap_is_50_mib():
    from pybrilliantkiosk.releases import MAX_BUNDLE_BYTES

    assert MAX_BUNDLE_BYTES == 50 * 1024 * 1024
    session = pinned_session(BUNDLE, length=MAX_BUNDLE_BYTES + 1)
    with pytest.raises(ReleaseError, match="more than the"):
        await releases_api(session).download(release(), sha256=SHA)


async def test_pinned_size_beyond_the_cap_is_capped_too():
    session = pinned_session(b"x" * 100)
    session.routes["https://x/b.tar.gz"].content_length = None
    with pytest.raises(ReleaseError, match="larger than"):
        await releases_api(session).download(release(), sha256=SHA, size=10**9, max_size=50)


async def test_the_checksum_asset_is_capped_as_well():
    huge = FakeResponse(200, b"0" * 10_000)
    session = FakeSession({"https://x/b.sha256": huge, "https://x/b.tar.gz": ok(BUNDLE)})
    with pytest.raises(ReleaseError, match="more than the 4096"):
        await releases_api(session).download(release())
