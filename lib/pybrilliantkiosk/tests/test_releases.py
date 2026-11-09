import hashlib
import json
from datetime import datetime
from typing import Any

import aiohttp
import pytest

from pybrilliantkiosk import ChecksumMismatch, Release, ReleaseError, Releases

URL = "https://api.github.com/repos/MikeI-Code/brilliant-ha-kiosk/releases?per_page=30"
BUNDLE = b"bundle-bytes"
SHA = hashlib.sha256(BUNDLE).hexdigest()


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"") -> None:
        self.status = status
        self._body = body
        self.released = False

    def release(self) -> None:
        self.released = True

    async def json(self) -> Any:
        return json.loads(self._body)

    async def text(self) -> str:
        return self._body.decode()

    async def read(self) -> bytes:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


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
