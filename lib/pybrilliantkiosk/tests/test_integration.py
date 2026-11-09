"""End-to-end against an in-process asyncssh server on loopback (no panel, no network)."""

import hashlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import asyncssh
import pytest

from pybrilliantkiosk import (
    AuthFailed,
    CannotConnect,
    HostKeyMismatch,
    Panel,
    fetch_host_key,
)
from pybrilliantkiosk import panel as panel_module

SERVER_KEY = asyncssh.generate_private_key("ssh-ed25519")
RSA_KEY = asyncssh.generate_private_key("ssh-rsa", key_size=2048)
ECDSA_KEY = asyncssh.generate_private_key("ecdsa-sha2-nistp256")
FP = SERVER_KEY.get_fingerprint("sha256")
RSA_FP = RSA_KEY.get_fingerprint("sha256")
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
    elif cmd.startswith("d=$(mktemp"):
        process.stdout.write(b"/tmp/ha-kiosk-install.RealTest1\n")
    elif "ha-kiosk-setup install" in cmd:
        process.stdout.write(b'{"state": "in_progress"}\n')
    elif "ha-kiosk-setup wait" in cmd:
        process.stdout.write(b'{"state": "active", "version": "v9"}\n')
    elif cmd.startswith("echo '#--kl:begin--'"):
        process.stdout.write(
            b"#--kl:begin--\n#--kl:config:present--\nA=1\n\n"
            b"#--kl:running:absent--\n#--kl:overrides--\n"
        )
    elif cmd == "echo-stdin":
        process.stdout.write(stdin)
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
        server_host_keys=[RSA_KEY, ECDSA_KEY, SERVER_KEY],  # RSA listed first on purpose
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


async def test_real_pinned_ed25519_connects_to_a_server_with_several_host_keys(port: int):
    # A panel offers ED25519, ECDSA and RSA keys; ssh, ssh-keyscan and plink pin the ED25519 one.
    async with Panel("127.0.0.1", port=port, password="pw", host_key=FP) as panel:
        assert panel.host_key_fingerprint == FP
    info = await fetch_host_key("127.0.0.1", port=port)
    assert info.sha256 == FP
    assert info.algorithm == "ssh-ed25519"
    assert info.display == f"ED25519 {FP}"


async def test_real_rsa_pin_is_not_what_gets_negotiated(port: int):
    with pytest.raises(HostKeyMismatch) as err:
        await Panel("127.0.0.1", port=port, password="pw", host_key=RSA_FP).connect()
    assert err.value.fingerprint == FP


@pytest.fixture
def rsa_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(panel_module, "HOST_KEY_ALGS", ("rsa-sha2-512", "rsa-sha2-256"))
    yield


async def test_real_algorithm_order_decides_which_key_is_seen(port: int, rsa_only: None):
    # Proves the loopback server really would hand out RSA, i.e. the test above is meaningful.
    assert (await fetch_host_key("127.0.0.1", port=port)).sha256 == RSA_FP
    assert (await fetch_host_key("127.0.0.1", port=port)).label == "RSA"


@pytest.mark.parametrize(
    "data", ["text \u00e9\u20ac\nline two\n", "", b"\x00\x01\xff binary\n", b""]
)
async def test_real_stdin_reaches_the_process_intact(port: int, data: str | bytes):
    async with Panel("127.0.0.1", port=port, password="pw") as panel:
        result = await panel._run("echo-stdin", stdin=data)
    expected = data.encode() if isinstance(data, str) else data
    assert result.stdout == expected.decode("utf-8", "replace")
