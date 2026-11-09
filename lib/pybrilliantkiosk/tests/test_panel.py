import json
import shlex
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest

from pybrilliantkiosk import (
    AuthFailed,
    BusPeripheral,
    BusVariable,
    CannotConnect,
    CommandFailed,
    HostKeyMismatch,
    InvalidArgument,
    Panel,
    SetupBusy,
    fetch_host_key,
)
from pybrilliantkiosk.panel import fingerprint_matches, parse_health

HOST_KEY = asyncssh.generate_private_key("ssh-ed25519")
SHA_FP = HOST_KEY.get_fingerprint("sha256")
MD5_FP = HOST_KEY.get_fingerprint("md5")


def sh_run(script: str, cwd: Any, *, args: tuple[str, ...] = ()) -> str:
    """Run ``sh -ec script`` locally (sync helper, so async tests do not block-call subprocess)."""
    import subprocess

    done = subprocess.run(  # noqa: S603
        ["sh", "-ec", script, "sh", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


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
    assert net.conn.closed  # the stale connection was closed, not just forgotten
    net.conn.handler = lambda c, i: out()
    await panel.start("ha-kiosk")
    assert net.connects == 2


async def test_concurrent_first_commands_share_one_connection(
    net: Net, monkeypatch: pytest.MonkeyPatch
):
    import asyncio

    original = net.connect

    async def slow_connect(host: str, **kw: Any) -> FakeConn:
        await asyncio.sleep(0.01)
        return await original(host, **kw)

    monkeypatch.setattr(asyncssh, "connect", slow_connect)
    panel = Panel("h")
    results = await asyncio.gather(panel.connect(), panel.connect(), panel.restart("ha-kiosk"))
    assert results[:2] == [SHA_FP, SHA_FP]
    assert net.connects == 1


async def test_failed_command_does_not_close_a_newer_connection(net: Net):
    panel = Panel("h")
    await panel.connect()
    stale = net.conn
    replacement = FakeConn(lambda c, i: out())
    panel._conn = replacement  # type: ignore[assignment]  # a concurrent reconnect won the race
    await panel._drop(stale)  # type: ignore[arg-type]
    assert stale.closed
    assert not replacement.closed
    assert panel._conn is replacement  # type: ignore[comparison-overlap]


async def test_run_after_connection_dropped_concurrently(net: Net, monkeypatch: pytest.MonkeyPatch):
    panel = Panel("h")

    async def fake_connect() -> str:
        return "x"

    monkeypatch.setattr(panel, "connect", fake_connect)
    with pytest.raises(CannotConnect, match="lost"):
        await panel.restart("ha-kiosk")


async def test_fetch_host_key_sends_no_credentials(net: Net):
    info = await fetch_host_key("h")
    assert info.sha256 == SHA_FP
    assert info.md5 == MD5_FP
    assert info.matches(SHA_FP)
    assert info.matches(f"ssh-ed25519 255 {SHA_FP}")
    assert info.matches(MD5_FP.removeprefix("MD5:").upper())
    assert not info.matches("SHA256:nope")
    # No password, no key, no username: authentication can never start.
    assert "password" not in net.kwargs
    assert "username" not in net.kwargs
    assert net.kwargs["client_keys"] == []
    assert net.conn.calls == []


async def test_fetch_host_key_unreachable(net: Net):
    for raised in (OSError("refused"), asyncssh.ConnectionLost(1, "lost"), TimeoutError()):
        net.error = raised
        with pytest.raises(CannotConnect):
            await fetch_host_key("h")


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
    assert "/var/lib/ha-kiosk/config/kiosk.env" in net.conn.calls[0][0]


def env_handler(
    base: str = ENV_TEXT,
    overrides: str = "",
    state: str = "active",
    calls: list[tuple[str, Any]] | None = None,
    write_status: int = 0,
) -> Any:
    """Fake panel: status probe, config read (with overrides) and the locked write."""

    def handler(cmd: str, stdin: Any) -> Any:
        if calls is not None:
            calls.append((cmd, stdin))
        if "ha-kiosk-setup status" in cmd:
            return out(json.dumps({"state": state}))
        if cmd.startswith("if [ -f /var/lib/ha-kiosk/config/kiosk.env"):
            return out(f"{base}\n#--overrides--\n{overrides}")
        if "fcntl.flock" in cmd:
            return out("", write_status)
        return out()

    return handler


def locked_write(calls: list[tuple[str, Any]]) -> tuple[str, str, Any]:
    """(runner python code, inner shell script, stdin) of the one locked write in ``calls``."""
    writes = [(c, i) for c, i in calls if "fcntl.flock" in c]
    assert len(writes) == 1
    command, stdin = writes[0]
    words = shlex.split(command)
    assert words[:2] == ["/data/switch-embedded/env/bin/python", "-c"]
    return words[2], words[3], stdin


async def test_set_env_writes_both_copies_under_the_setup_lock(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(calls=calls)
    changed = await Panel("h").set_env({"DAY_LEVEL": "9", "DIM_LEVEL": "a b"}, ["HA_URL"])
    assert set(changed) == {"DAY_LEVEL", "HA_URL", "DIM_LEVEL"}
    runner, script, stdin = locked_write(calls)
    # The runner takes the same flock as ha-kiosk-setup (/run/ha-kiosk-setup.lock), non-blocking.
    assert "/run/ha-kiosk-setup.lock" in runner
    assert "LOCK_EX | fcntl.LOCK_NB" in runner
    assert "sys.exit(8)" in runner
    assert "umask 077" in script
    assert "/var/lib/ha-kiosk/config/kiosk.env /etc/ha-kiosk/kiosk.env" in script
    assert 'cp -p "$f" "$f.bak"' in script
    assert 'mv "$f.new" "$f"' in script
    assert "overrides.env" not in script  # nothing to purge: no override was set
    assert stdin == '# comment\nDAY_LEVEL="9"\nMQTT_PASSWORD=hunter2\nDIM_LEVEL="a b"\n'
    assert not any("systemctl restart" in c for c, _ in calls)


async def test_set_env_written_text_is_inert_when_the_panel_sources_it(net: Net, tmp_path: Any):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(calls=calls)
    await Panel("h").set_env({"LAYOUT_DASHBOARD": "x;touch injected|id&y>z (w) v"})
    _, _, stdin = locked_write(calls)
    (tmp_path / "kiosk.env").write_text(stdin)
    sourced = sh_run('. ./kiosk.env; printf %s "$LAYOUT_DASHBOARD"', tmp_path)
    assert sourced == "x;touch injected|id&y>z (w) v"
    assert not (tmp_path / "injected").exists()


async def test_set_env_restart_and_noop(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(calls=calls)
    panel = Panel("h")
    assert await panel.set_env({"DAY_LEVEL": "15"}, restart=True) == ()
    assert not any("fcntl.flock" in c or "restart" in c for c, _ in calls)  # no write, no restart
    assert await panel.set_env({"DAY_LEVEL": "1"}, restart=True) == ("DAY_LEVEL",)
    assert calls[-1][0] == "systemctl restart ha-kiosk.service"
    assert await panel.set_env({}, ["NOT_PRESENT"]) == ()


async def test_set_env_compares_against_the_effective_view(net: Net):
    # kiosk.env says 15 but an override (HA switch) makes the effective value 9.
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base="DAY_LEVEL=15\n", overrides="DAY_LEVEL=9\n", calls=calls)
    # Setting the file's own value (15) changes what the panel really uses (9 -> 15).
    assert await Panel("h").set_env({"DAY_LEVEL": "15"}) == ("DAY_LEVEL",)
    _, script, _ = locked_write(calls)
    assert "-e '/^DAY_LEVEL=/d'" in script


async def test_set_env_always_purges_overrides_for_passed_keys(net: Net):
    # The file already holds 9 and an override also says 9: the effective value does not change,
    # but the stale override must still go so a later toggle cannot resurrect it.
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(
        base='DAY_LEVEL="9"\n', overrides="DAY_LEVEL=9\nSCREENSAVER=0\n", calls=calls
    )
    assert await Panel("h").set_env({"DAY_LEVEL": "9"}, restart=True) == ()
    _, script, _ = locked_write(calls)
    assert "-e '/^DAY_LEVEL=/d'" in script
    assert "SCREENSAVER" not in script  # keys that were not passed keep their override
    assert "cat >" not in script  # kiosk.env itself needs no rewrite
    assert not any("systemctl restart" in c for c, _ in calls)  # nothing effectively changed


async def test_set_env_delete_also_purges_override(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(
        base="SCREENSAVER=1\n", overrides="SCREENSAVER=0\n", calls=calls
    )
    assert await Panel("h").set_env({}, ["SCREENSAVER"]) == ("SCREENSAVER",)
    _, script, _ = locked_write(calls)
    assert "-e '/^SCREENSAVER=/d'" in script


async def test_set_env_purge_script_is_atomic_and_applies_to_sh(net: Net, tmp_path: Any):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(
        base='DAY_LEVEL="9"\n', overrides="DAY_LEVEL=9\nSCREENSAVER=0\n", calls=calls
    )
    await Panel("h").set_env({"DAY_LEVEL": "9"})
    _, script, _ = locked_write(calls)
    overrides = tmp_path / "overrides.env"
    overrides.write_text("# Written by ha-kiosk-bridge\nDAY_LEVEL=9\nSCREENSAVER=0\n")
    local = script.replace("/var/lib/ha-kiosk/overrides.env", str(overrides))
    sh_run(local, tmp_path)
    assert overrides.read_text() == "# Written by ha-kiosk-bridge\nSCREENSAVER=0\n"
    assert [p.name for p in tmp_path.iterdir()] == ["overrides.env"]  # no temp file left behind


async def test_set_env_refuses_while_setup_job_runs(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(state="in_progress", calls=calls)
    with pytest.raises(SetupBusy):
        await Panel("h").set_env({"DAY_LEVEL": "1"})
    assert not any("fcntl.flock" in c for c, _ in calls)


async def test_set_env_maps_lock_contention_to_busy(net: Net):
    net.conn.handler = env_handler(write_status=8)
    with pytest.raises(SetupBusy, match="lock"):
        await Panel("h").set_env({"DAY_LEVEL": "1"})


async def test_set_env_write_failure_and_broken_setup_tool(net: Net):
    net.conn.handler = env_handler(write_status=1)
    with pytest.raises(CommandFailed):
        await Panel("h").set_env({"DAY_LEVEL": "1"})

    # A failing status probe is not "busy": the flock is what protects the write.
    base = env_handler()

    def handler(cmd: str, stdin: Any) -> Any:
        if "ha-kiosk-setup status" in cmd:
            return out("", 2)
        return base(cmd, stdin)

    net.conn.handler = handler
    assert await Panel("h").set_env({"DAY_LEVEL": "1"}) == ("DAY_LEVEL",)


async def test_set_env_busy_status_error_propagates(net: Net):
    base = env_handler()

    def handler(cmd: str, stdin: Any) -> Any:
        if "ha-kiosk-setup status" in cmd:
            return out('{"error": "busy"}', 8)
        return base(cmd, stdin)

    net.conn.handler = handler
    with pytest.raises(SetupBusy):
        await Panel("h").set_env({"DAY_LEVEL": "1"})


async def test_set_env_calls_on_one_panel_do_not_interleave(net: Net):
    import asyncio

    inside = 0
    peak = 0
    base = env_handler()

    async def run(command: str, **kw: Any) -> Any:
        nonlocal inside, peak
        if "fcntl.flock" in command:
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.01)
            inside -= 1
        return base(command, kw.get("input"))

    net.conn.run = run  # type: ignore[method-assign]
    panel = Panel("h")
    await asyncio.gather(panel.set_env({"DAY_LEVEL": "1"}), panel.set_env({"DIM_LEVEL": "2"}))
    assert peak == 1


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


async def test_get_settings_merges_overrides(net: Net):
    net.conn.handler = lambda c, i: out(
        "A=1\nDAY_LEVEL=15\nMQTT_PASSWORD=s\n\n#--overrides--\nDAY_LEVEL=9\nOTHER=1\n"
    )
    settings = await Panel("h").get_settings()
    assert settings["DAY_LEVEL"] == "9"
    assert settings["A"] == "1"
    assert "OTHER" not in settings
    assert settings["MQTT_PASSWORD"] != "s"
    assert (await Panel("h").get_settings(mask_secrets=False))["MQTT_PASSWORD"] == "s"
    assert "overrides.env" in net.conn.calls[0][0]
