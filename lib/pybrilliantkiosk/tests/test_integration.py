"""End-to-end against an in-process asyncssh server on loopback (no panel, no network)."""

import asyncio
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


#: What the loopback "panel" saw of the commands that hang: ``started``, a signal it was sent
#: (``signal:KILL``) and ``channel closed`` (the client closed the channel).
hung: list[str] = []


async def _hang(process: Any) -> None:
    """A command that never ends by itself (the stand-in for a stuck ``ha-kiosk-setup`` job)."""
    hung.append("started")
    while not process.channel.is_closing():  # noqa: ASYNC110 - asyncssh has no event for it
        await asyncio.sleep(0.01)
    hung.append("channel closed")


@pytest.fixture
def signals_seen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the SSH signals the loopback server receives; ``hang`` dies of one, ``hang-deaf``
    ignores it (like an sshd that does not implement signal requests)."""
    hung.clear()
    original = asyncssh.process.SSHServerProcess.signal_received

    def signal_received(self: Any, signal: str) -> None:
        hung.append(f"signal:{signal}")
        original(self, signal)
        if self.command == "hang":
            self.exit_with_signal(signal)

    monkeypatch.setattr(asyncssh.process.SSHServerProcess, "signal_received", signal_received)
    return hung


async def _handle(process: Any) -> None:
    cmd = process.command
    if cmd in ("hang", "hang-deaf"):
        await _hang(process)
        return
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
    elif cmd.startswith("echo '#--kl-"):
        mark = b"#--kl-" + cmd.split("kl-")[1].split(":")[0].encode()
        process.stdout.write(
            mark
            + b":begin--\n"
            + mark
            + b":config:present:"
            + hashlib.sha256(b"A=1\n").hexdigest().encode()
            + b"--\nA=1\n\n"
            + mark
            + b":running:absent--\n"
            + mark
            + b":overrides--\n"
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


async def _seen(what: str) -> None:
    for _ in range(300):
        if what in hung:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the loopback panel never saw {what!r}: {hung}")


async def test_real_timed_out_command_is_killed_and_the_connection_still_works(
    port: int, signals_seen: list[str]
):
    async with Panel("127.0.0.1", port=port, password="pw", timeout=0.3) as panel:
        await panel.connect()
        conn = panel._conn
        with pytest.raises(CannotConnect, match="timed out"):
            await panel._run("hang")
        await _seen("signal:KILL")  # the process was told to die, not left running
        assert hung[0] == "started"
        assert panel._conn is conn  # the connection was not thrown away
        assert (await panel._run("echo-stdin", stdin="still usable")).stdout == "still usable"


async def test_real_timed_out_command_that_ignores_kill_still_has_its_channel_closed(
    port: int, signals_seen: list[str]
):
    async with Panel("127.0.0.1", port=port, password="pw", timeout=0.3) as panel:
        await panel.connect()
        conn = panel._conn
        with pytest.raises(CannotConnect, match="timed out"):
            await panel._run("hang-deaf")
        await _seen("channel closed")  # the channel did not stay open next to the dead command
        assert "signal:KILL" in hung
        assert panel._conn is conn
        assert (await panel._run("echo-stdin", stdin="still usable")).stdout == "still usable"


async def test_real_timed_out_command_whose_channel_never_closes_replaces_the_connection(
    port: int, signals_seen: list[str], monkeypatch: pytest.MonkeyPatch
):

    async def never(self: Any) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(panel_module, "_ABANDON_GRACE", 0.05)
    async with Panel("127.0.0.1", port=port, password="pw", timeout=0.3) as panel:
        await panel.connect()
        conn = panel._conn
        monkeypatch.setattr(asyncssh.SSHClientProcess, "wait_closed", never)
        with pytest.raises(CannotConnect, match="timed out"):
            await panel._run("hang")
        monkeypatch.undo()
        assert panel._conn is None  # dropped: the next command connects afresh
        assert (await panel._run("echo-stdin", stdin="new connection")).stdout == "new connection"
        assert panel._conn is not conn


# -- a password-only panel never offers (or authenticates with) the host's own default keys --------

DEFAULT_KEY = asyncssh.generate_private_key("ssh-ed25519")  # ~/.ssh/id_ed25519 of the fake home
CONFIGURED_KEY = asyncssh.generate_private_key("ssh-ed25519")  # the key a user pastes in
DEFAULT_FP = DEFAULT_KEY.get_fingerprint()
CONFIGURED_FP = CONFIGURED_KEY.get_fingerprint()
#: Fingerprints of the public keys offered to the server since the test began.
offered: list[str] = []


class _KeyServer(_Server):
    """Accepts the password ``pw`` and the public keys of the default and the configured key."""

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        offered.append(key.get_fingerprint())
        return key.get_fingerprint() in (DEFAULT_FP, CONFIGURED_FP)


class _KeyOnlyServer(_KeyServer):
    """Like :class:`_KeyServer` but no password is ever valid: only a key can log in."""

    def validate_password(self, username: str, password: str) -> bool:
        return False


async def _serve(server_class: type[asyncssh.SSHServer]) -> Any:
    return await asyncssh.create_server(
        server_class,
        "127.0.0.1",
        0,
        server_host_keys=[SERVER_KEY],
        process_factory=_handle,
        encoding=None,
        config=[],
    )


@pytest.fixture
def home_with_a_default_key(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A HOME whose ~/.ssh/id_ed25519 the loopback server would accept."""
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    key_file = ssh / "id_ed25519"
    DEFAULT_KEY.write_private_key(str(key_file))
    key_file.chmod(0o600)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    offered.clear()
    yield tmp_path
    offered.clear()


@pytest.fixture
async def key_port(home_with_a_default_key: Any) -> AsyncIterator[int]:
    server = await _serve(_KeyServer)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


@pytest.fixture
async def key_only_port(home_with_a_default_key: Any) -> AsyncIterator[int]:
    server = await _serve(_KeyOnlyServer)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def test_the_harness_does_see_default_keys_when_asyncssh_is_told_to_load_them(key_port: int):
    """Control: ``client_keys=[]`` (the old code) makes asyncssh offer ~/.ssh/id_ed25519."""
    conn = await asyncssh.connect(
        "127.0.0.1",
        port=key_port,
        username="root",
        known_hosts=None,
        client_keys=[],
        agent_path=None,
        config=[],
    )
    conn.close()
    assert set(offered) == {DEFAULT_FP}


async def test_password_only_panel_never_offers_the_hosts_default_key(key_port: int):
    async with Panel("127.0.0.1", port=key_port, password="pw") as panel:
        assert (await panel._run("echo-stdin", stdin="x")).stdout == "x"
    assert offered == []  # not even offered, let alone accepted


async def test_a_wrong_password_is_not_rescued_by_the_default_key(key_port: int):
    with pytest.raises(AuthFailed):
        await Panel("127.0.0.1", port=key_port, password="wrong").connect()
    assert offered == []


async def test_a_panel_without_credentials_does_not_authenticate_with_the_default_key(
    key_only_port: int,
):
    with pytest.raises(AuthFailed):
        await Panel("127.0.0.1", port=key_only_port).connect()
    assert offered == []


async def test_scanning_the_host_key_offers_no_key(key_port: int):
    info = await fetch_host_key("127.0.0.1", port=key_port)
    assert info.sha256 == FP
    assert offered == []


async def test_only_the_configured_key_is_offered_even_with_a_default_key_present(
    key_only_port: int,
):
    pem = CONFIGURED_KEY.export_private_key().decode()
    async with Panel("127.0.0.1", port=key_only_port, private_key=pem) as panel:
        assert (await panel._run("echo-stdin", stdin="k")).stdout == "k"
    assert set(offered) == {CONFIGURED_FP}  # asked, then signed
