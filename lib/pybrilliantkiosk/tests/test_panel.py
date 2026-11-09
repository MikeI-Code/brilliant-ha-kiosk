import hashlib
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest

from pybrilliantkiosk import (
    AuthFailed,
    BusPeripheral,
    BusVariable,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    HostKeyMismatch,
    InvalidArgument,
    Panel,
)
from pybrilliantkiosk.panel import INSTALL_DIR, fingerprint_matches, parse_health

HOST_KEY = asyncssh.generate_private_key("ssh-ed25519")
SHA_FP = HOST_KEY.get_fingerprint("sha256")
MD5_FP = HOST_KEY.get_fingerprint("md5")


def out(stdout: str | bytes = "", status: int | None = 0, err: str | bytes = "") -> Any:
    def enc(v: str | bytes) -> bytes:
        return v.encode() if isinstance(v, str) else v

    return SimpleNamespace(stdout=enc(stdout), stderr=enc(err), exit_status=status)


class FakeFile:
    def __init__(self, sink: list[bytes]) -> None:
        self.sink = sink

    async def __aenter__(self) -> "FakeFile":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def write(self, data: bytes) -> None:
        self.sink.append(data)


class FakeSFTP:
    def __init__(self, sink: list[bytes], error: BaseException | None = None) -> None:
        self.sink = sink
        self.error = error
        self.paths: list[str] = []

    async def __aenter__(self) -> "FakeSFTP":
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def open(self, path: str, mode: str) -> FakeFile:
        self.paths.append(path)
        assert mode == "wb"
        return FakeFile(self.sink)


class FakeConn:
    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.calls: list[tuple[str, Any]] = []
        self.closed = False
        self.sftp = FakeSFTP([])

    async def run(self, command: str, **kw: Any) -> Any:
        assert kw["check"] is False
        assert kw["encoding"] is None
        self.calls.append((command, kw.get("input")))
        result = self.handler(command, kw.get("input"))
        if isinstance(result, BaseException):
            raise result
        return result

    def start_sftp_client(self) -> FakeSFTP:
        return self.sftp

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class Net:
    """Patches asyncssh.connect and records how it was called."""

    def __init__(self, handler: Any = None) -> None:
        self.conn = FakeConn(handler or (lambda c, i: out()))
        self.error: BaseException | None = None
        self.kwargs: dict[str, Any] = {}
        self.connects = 0

    async def connect(self, host: str, **kw: Any) -> FakeConn:
        self.connects += 1
        self.kwargs = kw
        if self.error:
            raise self.error
        client = kw["client_factory"]()
        if not client.validate_host_public_key(host, host, 22, HOST_KEY):
            raise asyncssh.HostKeyNotVerifiable(9, "not trusted")
        return self.conn


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    n = Net()
    monkeypatch.setattr(asyncssh, "connect", n.connect)
    return n


def test_fingerprint_matching():
    assert fingerprint_matches(SHA_FP, HOST_KEY)
    assert fingerprint_matches(SHA_FP.rstrip("="), HOST_KEY)
    assert fingerprint_matches(f"ssh-ed25519 255 {SHA_FP}", HOST_KEY)
    assert fingerprint_matches(MD5_FP, HOST_KEY)
    assert fingerprint_matches(MD5_FP.removeprefix("MD5:").upper(), HOST_KEY)
    assert not fingerprint_matches("SHA256:nope", HOST_KEY)


async def test_connect_unpinned_returns_fingerprint(net: Net):
    panel = Panel("h", password="pw")
    assert panel.host_key_fingerprint is None
    assert await panel.connect() == SHA_FP
    assert await panel.connect() == SHA_FP
    assert net.connects == 1
    assert panel.host_key_fingerprint == SHA_FP
    assert net.kwargs["password"] == "pw"
    assert net.kwargs["username"] == "root"
    assert net.kwargs["client_keys"] == []
    assert net.kwargs["config"] == []


@pytest.mark.parametrize("pin", [SHA_FP, MD5_FP, f"ssh-ed25519 255 {SHA_FP}"])
async def test_connect_pinned_ok(net: Net, pin: str):
    assert await Panel("h", password="pw", host_key=pin).connect() == SHA_FP


async def test_connect_pinned_mismatch(net: Net):
    with pytest.raises(HostKeyMismatch) as err:
        await Panel("h", password="pw", host_key="SHA256:other").connect()
    assert err.value.fingerprint == SHA_FP
    assert net.connects == 1


async def test_connect_errors(net: Net):
    cases: list[tuple[BaseException, type[Exception]]] = [
        (asyncssh.PermissionDenied(14, "denied"), AuthFailed),
        (OSError("refused"), CannotConnect),
        (asyncssh.ConnectionLost(1, "lost"), CannotConnect),
        (TimeoutError(), CannotConnect),
    ]
    for raised, expected in cases:
        net.error = raised
        with pytest.raises(expected):
            await Panel("h").connect()


async def test_private_key(net: Net):
    key = asyncssh.generate_private_key("ssh-ed25519")
    pem = key.export_private_key("pkcs8-pem", passphrase="s3").decode()
    await Panel("h", private_key=pem, passphrase="s3").connect()
    assert len(net.kwargs["client_keys"]) == 1
    with pytest.raises(AuthFailed, match="private key"):
        await Panel("h", private_key="garbage").connect()
    with pytest.raises(AuthFailed):
        await Panel("h", private_key=pem, passphrase="wrong").connect()


async def test_context_manager_and_close(net: Net):
    async with Panel("h") as panel:
        assert net.connects == 1
        assert not net.conn.closed
    assert net.conn.closed
    await panel.close()  # idempotent


async def test_command_failure_and_decode(net: Net):
    net.conn.handler = lambda c, i: out("", 3, b"bad \xff")
    with pytest.raises(CommandFailed) as err:
        await Panel("h").restart("ha-kiosk")
    assert err.value.exit_status == 3
    assert "bad" in err.value.stderr
    assert err.value.command.startswith("systemctl restart")

    net.conn.handler = lambda c, i: SimpleNamespace(stdout=None, stderr="s", exit_status=None)
    with pytest.raises(CommandFailed) as err2:
        await Panel("h").restart("ha-kiosk")
    assert err2.value.exit_status is None
    assert err2.value.stderr == "s"


async def test_connection_lost_then_reconnect(net: Net):
    panel = Panel("h")
    net.conn.handler = lambda c, i: asyncssh.ConnectionLost(1, "gone")
    with pytest.raises(CannotConnect):
        await panel.start("ha-kiosk")
    net.conn.handler = lambda c, i: out()
    await panel.start("ha-kiosk")
    assert net.connects == 2


async def test_command_timeout(net: Net):
    net.conn.handler = lambda c, i: TimeoutError()
    with pytest.raises(CannotConnect, match="timed out"):
        await Panel("h").stop("ha-kiosk")


HEALTH_OUT = """uptime=12345.67
loadavg=0.10 0.20 0.30
memtotal=1000
memavail=400
version=v1.5.0
backlight=12
backlight_max=15
display=awake
unit.ha-kiosk.service=active
unit.ha-kiosk-voice.service=
no equals line
"""


async def test_health_one_round_trip(net: Net):
    net.conn.handler = lambda c, i: out(HEALTH_OUT)
    health = await Panel("h").health()
    assert len(net.conn.calls) == 1
    script = net.conn.calls[0][0]
    for unit in (
        "ha-kiosk.service",
        "ha-kiosk-display.service",
        "ha-kiosk-bridge.service",
        "ha-kiosk-swap.service",
        "ha-kiosk-voice.service",
        "ha-kiosk-intercom.service",
        "ha-kiosk-capture.service",
    ):
        assert f"systemctl is-active {unit}" in script
    for path in ("/proc/uptime", "/proc/loadavg", "/proc/meminfo", "/etc/ha-kiosk/VERSION"):
        assert path in script
    assert "/sys/class/backlight/backlight_lcd/brightness" in script
    assert health.uptime_seconds == 12345.67
    assert (health.load_1, health.load_5, health.load_15) == (0.1, 0.2, 0.3)
    assert health.mem_used_percent == 60.0
    assert health.version == "v1.5.0"
    assert (health.backlight, health.backlight_max, health.display_state) == (12, 15, "awake")
    assert health.unit_active("ha-kiosk")
    assert health.units["ha-kiosk-voice.service"] == "unknown"
    with pytest.raises(AttributeError):
        health.version = "x"  # type: ignore[misc]


def test_parse_health_garbage():
    h = parse_health("uptime=abc\nloadavg=1\nmemtotal=x\nbacklight=\n")
    assert h.uptime_seconds is None
    assert h.load_1 == 1.0
    assert h.load_5 is None
    assert h.mem_total_kb is None
    assert h.backlight is None
    assert h.version is None
    assert h.display_state is None
    assert h.units == {}


ENV_TEXT = "# comment\nDAY_LEVEL=15\nMQTT_PASSWORD=hunter2\nHA_URL=http://x:8123\n"


async def test_get_env(net: Net):
    net.conn.handler = lambda c, i: out(ENV_TEXT)
    panel = Panel("h")
    masked = await panel.get_env()
    assert masked["MQTT_PASSWORD"] != "hunter2"
    assert masked["DAY_LEVEL"] == "15"
    assert (await panel.get_env(mask_secrets=False))["MQTT_PASSWORD"] == "hunter2"
    assert net.conn.calls[0][0] == "cat /etc/ha-kiosk/kiosk.env"


async def test_set_env_writes_atomically(net: Net):
    net.conn.handler = lambda c, i: out(ENV_TEXT if c.startswith("cat ") else "")
    changed = await Panel("h").set_env({"DAY_LEVEL": "9", "NEW_KEY": "a b"}, ["HA_URL"])
    assert set(changed) == {"DAY_LEVEL", "HA_URL", "NEW_KEY"}
    script, stdin = net.conn.calls[1]
    assert 'cat > "$f.tmp"' in script
    assert 'mv "$f.tmp" "$f"' in script
    assert 'cp -p "$f" "$f.bak"' in script
    assert 'chmod 600 "$f.tmp"' in script
    assert script.index("cp -p") < script.index("mv ")
    assert stdin == "# comment\nDAY_LEVEL=9\nMQTT_PASSWORD=hunter2\nNEW_KEY='a b'\n"
    assert len(net.conn.calls) == 2  # no restart


async def test_set_env_restart_and_noop(net: Net):
    net.conn.handler = lambda c, i: out(ENV_TEXT if c.startswith("cat ") else "")
    panel = Panel("h")
    assert await panel.set_env({"DAY_LEVEL": "15"}, restart=True) == ()
    assert len(net.conn.calls) == 1  # unchanged: no write, no restart
    assert await panel.set_env({"DAY_LEVEL": "1"}, restart=True) == ("DAY_LEVEL",)
    assert net.conn.calls[-1][0] == "systemctl restart ha-kiosk.service"
    assert await panel.set_env({}, ["NOT_PRESENT"]) == ()


async def test_set_env_validates_before_io(net: Net):
    panel = Panel("h")
    with pytest.raises(InvalidArgument):
        await panel.set_env({"bad key": "x"})
    with pytest.raises(InvalidArgument):
        await panel.set_env({"A": "line\nbreak"})
    with pytest.raises(InvalidArgument):
        await panel.set_env({}, ["1bad"])
    assert net.connects == 0


async def test_units_allowlist(net: Net):
    panel = Panel("h")
    await panel.restart("ha-kiosk-voice")
    await panel.start("ha-kiosk-display.service")
    await panel.stop("ha-kiosk-capture")
    assert [c for c, _ in net.conn.calls] == [
        "systemctl restart ha-kiosk-voice.service",
        "systemctl start ha-kiosk-display.service",
        "systemctl stop ha-kiosk-capture.service",
    ]
    for bad in ("sshd", "ha-kiosk; reboot", "../x", "message_bus.service"):
        for action in (panel.restart, panel.start, panel.stop):
            with pytest.raises(InvalidArgument):
                await action(bad)
        with pytest.raises(InvalidArgument):
            await panel.logs(bad)
    assert len(net.conn.calls) == 3


async def test_logs(net: Net):
    net.conn.handler = lambda c, i: out("line1\nline2\n")
    panel = Panel("h")
    assert await panel.logs("ha-kiosk", 50) == "line1\nline2\n"
    assert net.conn.calls[0][0] == "journalctl -u ha-kiosk.service -n 50 --no-pager -o short-iso"
    for bad in (0, -1, 5001):
        with pytest.raises(InvalidArgument):
            await panel.logs("ha-kiosk", bad)


async def test_reboot_closes(net: Net):
    panel = Panel("h")
    await panel.reboot()
    assert "systemctl reboot" in net.conn.calls[0][0]
    assert net.conn.closed


async def test_bus(net: Net):
    def handler(cmd: str, _: Any) -> Any:
        if cmd.endswith(" list"):
            return out("device d1\n  p1  (2 variables)\n")
        if " get " in cmd:
            return out("a = 1\nb = 2   [read-only]\n")
        return out("set a = 5\n")

    net.conn.handler = handler
    panel = Panel("h")
    listing = await panel.bus_peripherals()
    assert listing.device_id == "d1"
    assert listing.peripherals == (BusPeripheral("p1", 2),)
    assert await panel.bus_get("p1") == (BusVariable("a", "1", False), BusVariable("b", "2", True))
    assert await panel.bus_set("p1", {"a": 5}) == (BusVariable("a", "5", False),)
    assert net.conn.calls[-1][0].endswith("set p1 a=5")
    n = len(net.conn.calls)
    with pytest.raises(InvalidArgument):
        await panel.bus_get("p1; reboot")
    with pytest.raises(InvalidArgument):
        await panel.bus_set("p1", {"a;b": 1})
    assert len(net.conn.calls) == n


BUNDLE = b"tarball"
SHA = hashlib.sha256(BUNDLE).hexdigest()


def install_handler(remote_sha: str = SHA, install_status: int = 0, calls: list[str] | None = None):
    def handler(cmd: str, stdin: Any) -> Any:
        if calls is not None:
            calls.append(cmd)
        if cmd.startswith("sha256sum"):
            return out(f"{remote_sha}  {INSTALL_DIR}/bundle.tar.gz\n")
        if "install-local.sh" in cmd:
            return out("ha-kiosk: installed v2\n", install_status)
        return out()

    return handler


async def test_install_sftp(net: Net):
    cmds: list[str] = []
    net.conn.handler = install_handler(calls=cmds)
    log = await Panel("h").install(BUNDLE, SHA.upper())
    assert log == "ha-kiosk: installed v2\n"
    assert net.conn.sftp.sink == [BUNDLE]
    assert net.conn.sftp.paths == [f"{INSTALL_DIR}/bundle.tar.gz"]
    assert cmds[0].startswith(f"rm -rf {INSTALL_DIR} && mkdir -m 700")
    script = cmds[-1]
    assert "tar xzf bundle.tar.gz" in script
    assert 'sh "$d"' in script
    assert "--env" not in script
    assert "--token" not in script
    assert f"rm -rf {INSTALL_DIR}" in script


@pytest.mark.parametrize(
    "error",
    [asyncssh.SFTPFailure("no sftp"), asyncssh.ChannelOpenError(1, "no subsystem")],
)
async def test_install_falls_back_to_cat(net: Net, error: BaseException):
    net.conn.sftp = FakeSFTP([], error)
    cat_inputs: list[Any] = []

    def handler(cmd: str, stdin: Any) -> Any:
        if cmd.startswith("cat > "):
            cat_inputs.append((cmd, stdin))
            return out()
        return install_handler()(cmd, stdin)

    net.conn.handler = handler
    assert await Panel("h").install(BUNDLE, SHA) == "ha-kiosk: installed v2\n"
    assert cat_inputs == [(f"cat > {INSTALL_DIR}/bundle.tar.gz", BUNDLE)]


async def test_install_upload_connection_error(net: Net):
    net.conn.sftp = FakeSFTP([], OSError("reset"))
    net.conn.handler = install_handler()
    with pytest.raises(CannotConnect, match="upload"):
        await Panel("h").install(BUNDLE, SHA)


async def test_install_rejects_bad_checksums(net: Net):
    panel = Panel("h")
    with pytest.raises(InvalidArgument):
        await panel.install(BUNDLE, "xyz")
    with pytest.raises(ChecksumMismatch):
        await panel.install(BUNDLE, "0" * 64)
    assert net.connects == 0

    cmds: list[str] = []
    net.conn.handler = install_handler(remote_sha="f" * 64, calls=cmds)
    with pytest.raises(ChecksumMismatch, match="panel"):
        await panel.install(BUNDLE, SHA)
    assert cmds[-1] == f"rm -rf {INSTALL_DIR}"  # cleaned up
    assert not any("install-local.sh" in c for c in cmds)

    net.conn.handler = lambda c, i: out("", status=0) if not c.startswith("sha256sum") else out("")
    with pytest.raises(ChecksumMismatch):
        await panel.install(BUNDLE, SHA)


async def test_install_failure_reports_log(net: Net):
    net.conn.handler = install_handler(install_status=1)
    with pytest.raises(CommandFailed) as err:
        await Panel("h").install(BUNDLE, SHA)
    assert "installed v2" in err.value.stderr
    assert err.value.exit_status == 1


async def test_install_cleanup_failure_is_swallowed(net: Net):
    def handler(cmd: str, stdin: Any) -> Any:
        if cmd == f"rm -rf {INSTALL_DIR}":
            return out("", 1, "nope")
        return install_handler(remote_sha="e" * 64)(cmd, stdin)

    net.conn.handler = handler
    with pytest.raises(ChecksumMismatch):
        await Panel("h").install(BUNDLE, SHA)
