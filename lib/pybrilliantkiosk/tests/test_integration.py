"""End-to-end against an in-process asyncssh server on loopback (no panel, no network)."""

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import asyncssh
import pytest

from pybrilliantkiosk import AuthFailed, CannotConnect, HostKeyMismatch, Panel

SERVER_KEY = asyncssh.generate_private_key("ssh-ed25519")
FP = SERVER_KEY.get_fingerprint("sha256")
BUNDLE = b"bundle"
SHA = hashlib.sha256(BUNDLE).hexdigest()


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == "root" and password == "pw"


stored: dict[str, bytes] = {}


async def _handle(process: Any) -> None:
    cmd = process.command
    stdin = await process.stdin.read()
    if cmd.startswith("cat > "):
        stored[cmd[6:]] = stdin
    elif cmd.startswith("sha256sum"):
        path = cmd.split()[1]
        process.stdout.write(f"{hashlib.sha256(stored[path]).hexdigest()}  {path}\n".encode())
    elif "ha-kiosk-setup install" in cmd:
        process.stdout.write(b'{"state": "in_progress"}\n')
    elif "ha-kiosk-setup wait" in cmd:
        process.stdout.write(b'{"state": "active", "version": "v9"}\n')
    elif "kiosk.env" in cmd and cmd.startswith("if [ -f"):
        process.stdout.write(b"A=1\n")
    elif cmd == "fail":
        process.stderr.write(b"boom")
        process.exit(5)
        return
    process.exit(0)


@pytest.fixture
async def port() -> AsyncIterator[int]:
    server = await asyncssh.create_server(
        _Server,
        "127.0.0.1",
        0,
        server_host_keys=[SERVER_KEY],
        process_factory=_handle,
        encoding=None,
        config=[],
    )
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def test_real_connect_pin_and_run(port: int):
    async with Panel("127.0.0.1", port=port, password="pw") as panel:
        assert panel.host_key_fingerprint == FP
        assert await panel.get_env(mask_secrets=False) == {"A": "1"}
    async with Panel("127.0.0.1", port=port, password="pw", host_key=FP) as pinned:
        with pytest.raises(Exception, match="boom") as err:
            await pinned._run("fail")
        assert err.value.exit_status == 5  # type: ignore[attr-defined]


async def test_real_pin_mismatch_before_auth(port: int):
    with pytest.raises(HostKeyMismatch) as err:
        await Panel("127.0.0.1", port=port, password="pw", host_key="SHA256:wrong").connect()
    assert err.value.fingerprint == FP


async def test_real_bad_password(port: int):
    with pytest.raises(AuthFailed):
        await Panel("127.0.0.1", port=port, password="nope").connect()


async def test_real_refused():
    with pytest.raises(CannotConnect):
        await Panel("127.0.0.1", port=1, password="pw", connect_timeout=2).connect()


async def test_real_install_without_sftp(port: int):
    async with Panel("127.0.0.1", port=port, password="pw") as panel:
        status = await panel.install(BUNDLE, SHA)
        assert (status.state, status.version) == ("active", "v9")
