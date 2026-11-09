import hashlib
import json
import re
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
    ChecksumMismatch,
    CommandFailed,
    ConcurrentModification,
    HostKeyMismatch,
    InvalidArgument,
    Panel,
    SetupBusy,
    fetch_host_key,
)
from pybrilliantkiosk.panel import fingerprint_matches, parse_health

HOST_KEY = asyncssh.generate_private_key("ssh-ed25519")
SHA_FP = HOST_KEY.get_fingerprint("sha256")
MD5_FP = HOST_KEY.get_fingerprint("md5")  # only to prove it is refused


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


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def state_out(
    saved: str | None,
    running: str | object | None = ...,
    overrides: str = "",
    prelude: str = "",
) -> Any:
    """What the one-round-trip settings read prints (``running`` defaults to ``saved``)."""
    return out(prelude + "#--kl-TESTNONCE:begin--\n" + _env_sections(saved, running, overrides))


def _env_sections(saved: str | None, running: str | object | None, overrides: str) -> str:
    if running is ...:
        running = saved
    text = ""
    for name, body in (("config", saved), ("running", running)):
        if body is None:
            text += f"#--kl-TESTNONCE:{name}:absent--\n"
        else:
            text += f"#--kl-TESTNONCE:{name}:present:{sha(str(body))}--\n{body}\n"
    return text + f"#--kl-TESTNONCE:overrides--\n{overrides}"


POLL_HEALTH = "uptime=100.5\nloadavg=0.1 0.2 0.3\nversion=v1.4.0\nunit.ha-kiosk.service=active\n"
POLL_FACTS = {
    "tool": "present",
    "current": "v1.4.0",
    "state": "active",
    "now": "1000",
}


def poll_out(
    saved: str | None,
    running: str | object | None = ...,
    overrides: str = "",
    *,
    facts: dict[str, str] | None = None,
    heartbeat: str = "",
    result: str = "",
) -> Any:
    """What the one-round-trip poll prints: health, both copies, overrides and the setup files."""
    text = "banner line\n#--kl-TESTNONCE:begin--\n#--kl-TESTNONCE:health--\n" + POLL_HEALTH
    text += _env_sections(saved, running, overrides)
    text += "\n#--kl-TESTNONCE:setup--\n"
    text += "".join(f"{k}={v}\n" for k, v in (POLL_FACTS if facts is None else facts).items())
    text += f"#--kl-TESTNONCE:heartbeat--\n{heartbeat}\n#--kl-TESTNONCE:result--\n{result}\n"
    return out(text)


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


def renonce(command: str, result: Any) -> Any:
    """Markers carry a random nonce chosen per command: answer with the one asked for."""
    nonce = re.search(r"#--kl-([0-9a-f]+):begin--", command)
    if nonce and isinstance(getattr(result, "stdout", None), bytes):
        result.stdout = result.stdout.replace(b"#--kl-TESTNONCE:", f"#--kl-{nonce[1]}:".encode())
    return result


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
        return renonce(command, result)

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
    assert fingerprint_matches(f"256 {SHA_FP} host (ED25519)", HOST_KEY)
    assert fingerprint_matches(SHA_FP.replace("SHA256:", "sha256:"), HOST_KEY)  # the prefix only
    assert not fingerprint_matches("SHA256:nope", HOST_KEY)
    # Base64 is case-sensitive: a case-folded digest is another digest.
    assert not fingerprint_matches(SHA_FP.swapcase().replace("sha256:", "SHA256:"), HOST_KEY)
    assert not fingerprint_matches(SHA_FP.removeprefix("SHA256:"), HOST_KEY)  # no algorithm named


def test_md5_fingerprints_are_never_accepted():
    from pybrilliantkiosk import is_md5_fingerprint

    assert not fingerprint_matches(MD5_FP, HOST_KEY)
    assert not fingerprint_matches(MD5_FP.removeprefix("MD5:").upper(), HOST_KEY)
    assert not fingerprint_matches(f"{MD5_FP} {MD5_FP.upper()}", HOST_KEY)
    assert is_md5_fingerprint(MD5_FP)
    assert is_md5_fingerprint(MD5_FP.removeprefix("MD5:").upper())
    assert is_md5_fingerprint(f"ssh-ed25519 255 {MD5_FP}")
    assert not is_md5_fingerprint(SHA_FP)
    assert not is_md5_fingerprint("")
    assert not is_md5_fingerprint("aa:bb:cc")


async def test_connect_unpinned_returns_fingerprint(net: Net):
    panel = Panel("h", password="pw")
    assert panel.host_key_fingerprint is None
    assert await panel.connect() == SHA_FP
    assert await panel.connect() == SHA_FP
    assert net.connects == 1
    assert panel.host_key_fingerprint == SHA_FP
    assert net.kwargs["password"] == "pw"
    assert net.kwargs["username"] == "root"
    # None, never []: asyncssh reads an empty list as "load ~/.ssh/id_* as default keypairs".
    assert net.kwargs["client_keys"] is None
    assert net.kwargs["config"] == []


@pytest.mark.parametrize("pin", [SHA_FP, f"ssh-ed25519 255 {SHA_FP}"])
async def test_connect_pinned_ok(net: Net, pin: str):
    assert await Panel("h", password="pw", host_key=pin).connect() == SHA_FP


async def test_connect_with_an_md5_pin_is_a_mismatch(net: Net):
    with pytest.raises(HostKeyMismatch):
        await Panel("h", password="pw", host_key=MD5_FP).connect()


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
    assert info.matches(SHA_FP)
    assert info.matches(f"ssh-ed25519 255 {SHA_FP}")
    assert not info.matches(MD5_FP)
    assert not info.matches("SHA256:nope")
    # No password, no key, no username: authentication can never start.
    assert "password" not in net.kwargs
    assert "username" not in net.kwargs
    assert net.kwargs["client_keys"] is None
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
    net.conn.handler = lambda c, i: state_out(ENV_TEXT)
    panel = Panel("h")
    masked = await panel.get_env()
    assert masked["MQTT_PASSWORD"] != "hunter2"
    assert masked["DAY_LEVEL"] == "15"
    assert (await panel.get_env(mask_secrets=False))["MQTT_PASSWORD"] == "hunter2"
    assert "/var/lib/ha-kiosk/config/kiosk.env" in net.conn.calls[0][0]
    assert "/etc/ha-kiosk/kiosk.env" in net.conn.calls[0][0]


def env_handler(
    base: str = ENV_TEXT,
    overrides: str = "",
    state: str = "active",
    calls: list[tuple[str, Any]] | None = None,
    write_status: int | list[int] = 0,
    running: str | object | None = ...,
    setup_tool: str | None = None,
) -> Any:
    """Fake panel: status probe, settings read (both copies, overrides), tool and locked write."""

    def handler(cmd: str, stdin: Any) -> Any:
        if calls is not None:
            calls.append((cmd, stdin))
        if "ha-kiosk-setup status" in cmd:
            return out(json.dumps({"state": state}))
        if cmd.startswith("echo '#--kl-"):
            return renonce(cmd, state_out(base, running, overrides))
        if cmd.startswith("cat /var/lib/ha-kiosk/ha-kiosk-setup"):
            return out(setup_tool or "", 0 if setup_tool else 1)
        if "fcntl.flock" in cmd:
            code = write_status.pop(0) if isinstance(write_status, list) else write_status
            return out("", code)
        return out()

    return handler


def locked_write(calls: list[tuple[str, Any]]) -> tuple[str, str, Any]:
    """(runner python code, inner shell script, stdin) of the one locked write in ``calls``."""
    writes = [(c, i) for c, i in calls if "fcntl.flock" in c]
    assert len(writes) == 1
    command, raw = writes[0]
    stdin = raw.decode() if isinstance(raw, bytes) else raw  # the wire carries UTF-8 bytes
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
    net.conn.handler = env_handler(base="SCREENSAVER=1\n", overrides="SCREENSAVER=0\n", calls=calls)
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
    net.conn.handler = lambda c, i: state_out(
        "A=1\nDAY_LEVEL=15\nMQTT_PASSWORD=s\n", overrides="DAY_LEVEL=9\nOTHER=1\n"
    )
    settings = await Panel("h").get_settings()
    assert settings["DAY_LEVEL"] == "9"
    assert settings["A"] == "1"
    assert "OTHER" not in settings
    assert settings["MQTT_PASSWORD"] != "s"
    assert (await Panel("h").get_settings(mask_secrets=False))["MQTT_PASSWORD"] == "s"
    assert "overrides.env" in net.conn.calls[0][0]
    net.conn.handler = lambda c, i: out("garbage, no begin marker")
    with pytest.raises(CommandFailed, match="unexpected output"):
        await Panel("h").get_settings()


# -- A: host key algorithms ---------------------------------------------------------------------


async def test_host_key_algorithms_are_requested_in_openssh_order(net: Net):
    from pybrilliantkiosk.panel import HOST_KEY_ALGS

    assert HOST_KEY_ALGS[0] == "ssh-ed25519"  # the key ssh, ssh-keyscan and plink show first
    assert HOST_KEY_ALGS.index("ssh-ed25519") < HOST_KEY_ALGS.index("rsa-sha2-512")
    await fetch_host_key("h")
    assert net.kwargs["server_host_key_algs"] == list(HOST_KEY_ALGS)
    net.kwargs = {}
    await Panel("h", password="pw").connect()
    assert net.kwargs["server_host_key_algs"] == list(HOST_KEY_ALGS)


async def test_host_key_info_carries_the_key_algorithm(net: Net):
    info = await fetch_host_key("h")
    assert info.algorithm == "ssh-ed25519"
    assert info.label == "ED25519"
    assert info.display == f"ED25519 {SHA_FP}"
    assert info.matches(info.display)  # the shown text is accepted as a pin as well
    from pybrilliantkiosk import HostKeyInfo

    assert HostKeyInfo("SHA256:x").display == "SHA256:x"  # unknown type: just the hash
    labels = {
        "ecdsa-sha2-nistp256": "ECDSA",
        "rsa-sha2-512": "RSA",
        "ssh-rsa": "RSA",
        "ssh-dss": "DSA",
        "sk-something": "SK-SOMETHING",
    }
    for alg, label in labels.items():
        assert HostKeyInfo("SHA256:x", alg).label == label


# -- B: stdin and the kiosk.env write script ----------------------------------------------------


def env_script_for(
    sha: str, tmp_path: Any, expected: tuple[str | None, str | None] = (None, None)
) -> str:
    """The generated write script with the panel paths pointed into ``tmp_path``."""
    from pybrilliantkiosk.panel import _write_env_script

    script = _write_env_script((), True, sha, expected)
    for src, dst in (
        ("/var/lib/ha-kiosk/config/kiosk.env", tmp_path / "var" / "kiosk.env"),
        ("/etc/ha-kiosk/kiosk.env", tmp_path / "etc" / "kiosk.env"),
    ):
        (dst.parent).mkdir(exist_ok=True)
        script = script.replace(src, str(dst))
    return script


def run_script(script: str, stdin: bytes) -> Any:
    import subprocess

    return subprocess.run(  # noqa: S603
        ["sh", "-c", script],  # noqa: S607
        input=stdin,
        capture_output=True,
        check=False,
    )


def test_write_script_requires_a_hash():
    from pybrilliantkiosk.panel import _write_env_script

    with pytest.raises(InvalidArgument, match="sha256"):
        _write_env_script((), True)
    with pytest.raises(InvalidArgument, match="sha256"):
        _write_env_script((), True, "not-a-hash")
    assert _write_env_script(("A",), False).startswith("set -e")  # purge-only needs none


@pytest.mark.parametrize("stdin", [b"", b"A=1\n", b'A="1"\nB="2"'])
def test_write_script_refuses_empty_or_wrong_input_and_keeps_the_files(tmp_path: Any, stdin: bytes):
    import hashlib

    new = b'A="1"\nB="2"\n'
    script = env_script_for(hashlib.sha256(new).hexdigest(), tmp_path)
    for name in ("var", "etc"):
        (tmp_path / name / "kiosk.env").write_bytes(b'ORIG="1"\n')
    done = run_script(script, stdin)
    assert done.returncode == 3
    assert b"nothing changed" in done.stderr
    for name in ("var", "etc"):
        assert (tmp_path / name / "kiosk.env").read_bytes() == b'ORIG="1"\n'
        assert not (tmp_path / name / "kiosk.env.bak").exists()
    leftovers = {p.name for d in ("var", "etc") for p in (tmp_path / d).iterdir()}
    assert leftovers == {"kiosk.env"}  # no temp file left behind


def test_write_script_replaces_both_copies_for_the_right_input(tmp_path: Any):
    import hashlib

    new = b'A="1"\nB="2"\n'
    orig = sha(ORIG.decode())
    script = env_script_for(hashlib.sha256(new).hexdigest().upper(), tmp_path, (orig, orig))
    for name in ("var", "etc"):
        (tmp_path / name / "kiosk.env").write_bytes(ORIG)
    done = run_script(script, new)
    assert done.returncode == 0, done.stderr
    for name in ("var", "etc"):
        assert (tmp_path / name / "kiosk.env").read_bytes() == new
        assert (tmp_path / name / "kiosk.env.bak").read_bytes() == ORIG
    assert not [p for p in tmp_path.rglob("*") if ".tmp." in p.name]


ORIG = b'ORIG="1"\n'


@pytest.mark.parametrize("changed_copy", ["var", "etc"])
def test_write_script_aborts_when_a_copy_changed_since_it_was_read(
    tmp_path: Any, changed_copy: str
):
    """Optimistic concurrency: the live file no longer has the hash it had when it was read."""
    new = b'A="1"\n'
    orig = sha(ORIG.decode())
    script = env_script_for(sha(new.decode()), tmp_path, (orig, orig))
    for name in ("var", "etc"):
        (tmp_path / name / "kiosk.env").write_bytes(ORIG)
    (tmp_path / changed_copy / "kiosk.env").write_bytes(b'HAND_EDIT="1"\n')  # someone else
    done = run_script(script, new)
    assert done.returncode == 10
    assert b"changed on the panel" in done.stderr
    assert (tmp_path / changed_copy / "kiosk.env").read_bytes() == b'HAND_EDIT="1"\n'
    other = "etc" if changed_copy == "var" else "var"
    assert (tmp_path / other / "kiosk.env").read_bytes() == ORIG  # nothing was written
    assert {p.name for d in ("var", "etc") for p in (tmp_path / d).iterdir()} == {"kiosk.env"}


def test_write_script_aborts_when_a_copy_that_was_absent_appeared(tmp_path: Any):
    new = b'A="1"\n'
    script = env_script_for(sha(new.decode()), tmp_path, (None, None))
    (tmp_path / "etc" / "kiosk.env").write_bytes(ORIG)
    done = run_script(script, new)
    assert done.returncode == 10
    assert (tmp_path / "etc" / "kiosk.env").read_bytes() == ORIG
    assert not (tmp_path / "var" / "kiosk.env").exists()


def test_write_script_aborts_when_a_copy_that_was_read_disappeared(tmp_path: Any):
    new = b'A="1"\n'
    orig = sha(ORIG.decode())
    script = env_script_for(sha(new.decode()), tmp_path, (orig, orig))
    (tmp_path / "etc" / "kiosk.env").write_bytes(ORIG)  # the saved copy is gone
    assert run_script(script, new).returncode == 10


def test_write_script_creates_copies_that_were_absent(tmp_path: Any):
    new = b'A="1"\n'
    script = env_script_for(sha(new.decode()), tmp_path, (None, None))
    done = run_script(script, new)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "var" / "kiosk.env").read_bytes() == new
    assert (tmp_path / "etc" / "kiosk.env").read_bytes() == new


async def test_incomplete_stdin_is_a_clear_error(net: Net):
    from pybrilliantkiosk import ChecksumMismatch

    net.conn.handler = env_handler(write_status=3)
    with pytest.raises(ChecksumMismatch, match="did not arrive intact"):
        await Panel("h").set_env({"DAY_LEVEL": "1"})


async def test_set_env_sends_the_hash_of_exactly_what_it_sends(net: Net):
    import hashlib

    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(calls=calls)
    await Panel("h").set_env({"DAY_LEVEL": "9"})
    _, script, stdin = locked_write(calls)
    assert hashlib.sha256(stdin.encode()).hexdigest() in script


async def test_run_encodes_text_and_passes_bytes_through(net: Net):
    panel = Panel("h")
    await panel._run("cat", stdin="text \u00e9\n")
    await panel._run("cat", stdin=b"\x00\xff")
    await panel._run("cat")
    inputs = [i for _, i in net.conn.calls]
    assert inputs == ["text \u00e9\n".encode(), b"\x00\xff", None]


# -- C: divergent copies ------------------------------------------------------------------------

SAVED = '# saved\nDAY_LEVEL="9"\nKIOSK_MQTT="1"\n'
RUNNING = '# Written by ha-kiosk-setup\nDAY_LEVEL="12"\nKIOSK_MQTT="1"\nHA_ZOOM="150"\n'


async def test_env_drift_lists_differing_keys(net: Net):
    net.conn.handler = lambda c, i: state_out(SAVED, RUNNING)
    assert await Panel("h").env_drift() == ("DAY_LEVEL", "HA_ZOOM")
    net.conn.handler = lambda c, i: state_out(SAVED, SAVED)
    assert await Panel("h").env_drift() == ()
    net.conn.handler = lambda c, i: state_out(SAVED, None)  # one copy only: nothing to compare
    assert await Panel("h").env_drift() == ()
    net.conn.handler = lambda c, i: state_out(None, RUNNING)
    assert await Panel("h").env_drift() == ()


async def test_set_env_reconciles_diverged_copies_instead_of_dropping_running_keys(
    net: Net, caplog: pytest.LogCaptureFixture
):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED, running=RUNNING, calls=calls)
    panel = Panel("h")
    with caplog.at_level("WARNING"):
        changed = await panel.set_env({"DIM_LEVEL": "3"})
    assert changed == ("DIM_LEVEL",)  # HA_ZOOM and DAY_LEVEL=12 were already in effect
    _, _, stdin = locked_write(calls)
    # saved copy + the key only /etc had, running value preferred, comments of the saved copy kept
    assert stdin == '# saved\nDAY_LEVEL="12"\nKIOSK_MQTT="1"\nHA_ZOOM="150"\nDIM_LEVEL="3"\n'
    assert panel.last_reconciled == ("DAY_LEVEL", "HA_ZOOM")
    assert "kept the running value of DAY_LEVEL, HA_ZOOM" in caplog.text


async def test_set_env_with_no_change_still_heals_the_drift(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED, running=RUNNING, calls=calls)
    panel = Panel("h")
    # Nothing effective changes, but the copies differ, so both are rewritten identically.
    changed = await panel.set_env({"DAY_LEVEL": "12"}, restart=True)
    assert changed == ()
    _, _, stdin = locked_write(calls)
    assert 'HA_ZOOM="150"' in stdin
    assert not any("systemctl restart" in c for c, _ in calls)


async def test_set_env_delete_applies_after_reconcile(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED, running=RUNNING, calls=calls)
    assert await Panel("h").set_env({}, ["HA_ZOOM"]) == ("HA_ZOOM",)
    _, _, stdin = locked_write(calls)
    assert "HA_ZOOM" not in stdin


async def test_config_only_keys_count_as_changed_when_written_to_the_running_copy(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(
        base='DAY_LEVEL="9"\nHA_ZOOM="150"\n', running='DAY_LEVEL="9"\n', calls=calls
    )
    assert await Panel("h").set_env({"DIM_LEVEL": "3"}) == ("HA_ZOOM", "DIM_LEVEL")


async def test_reconcile_env_writes_both_copies(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED, running=RUNNING, calls=calls)
    assert await Panel("h").reconcile_env() == ("DAY_LEVEL", "HA_ZOOM")
    _, _, stdin = locked_write(calls)
    assert stdin == '# saved\nDAY_LEVEL="12"\nKIOSK_MQTT="1"\nHA_ZOOM="150"\n'


async def test_reconcile_env_without_drift_writes_nothing(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED, calls=calls)
    assert await Panel("h").reconcile_env() == ()
    assert not any("fcntl.flock" in c for c, _ in calls)


UNSAFE_RUNNING = 'DAY_LEVEL="9"\nHA_URL="http://x/$(id)"\n'
SAVED_WITH_URL = 'DAY_LEVEL="9"\nHA_URL="http://good:8123"\n'


async def test_unsafe_running_value_blocks_reconcile_and_nothing_is_written(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED_WITH_URL, running=UNSAFE_RUNNING, calls=calls)
    with pytest.raises(InvalidArgument, match="HA_URL") as err:
        await Panel("h").reconcile_env()
    assert "cannot be written safely" in str(err.value)
    assert not any("fcntl.flock" in c for c, _ in calls)


async def test_unsafe_running_value_blocks_set_env_instead_of_reverting_it(net: Net):
    """The saved HA_URL must not silently replace a hand-set (here unsafe) running one."""
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED_WITH_URL, running=UNSAFE_RUNNING, calls=calls)
    panel = Panel("h")
    with pytest.raises(InvalidArgument, match="HA_URL"):
        await panel.set_env({"DIM_LEVEL": "3"}, restart=True)
    with pytest.raises(InvalidArgument, match="HA_URL"):
        await panel.set_env({}, ["DAY_LEVEL"])
    assert not any("fcntl.flock" in c or "restart" in c for c, _ in calls)  # untouched


async def test_unsafe_running_value_the_caller_replaces_explicitly_is_not_blocked(
    net: Net, caplog: pytest.LogCaptureFixture
):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED_WITH_URL, running=UNSAFE_RUNNING, calls=calls)
    with caplog.at_level("WARNING"):
        changed = await Panel("h").set_env({"HA_URL": "http://new:8123"})
    assert changed == ("HA_URL",)
    _, _, stdin = locked_write(calls)
    assert 'HA_URL="http://new:8123"' in stdin
    assert "$(id)" not in stdin  # the unsafe line is replaced, never copied
    assert "replaced the unsafe running value of HA_URL" in caplog.text
    calls.clear()
    assert await Panel("h").set_env({}, ["HA_URL"]) == ("HA_URL",)
    assert "HA_URL" not in locked_write(calls)[2]


async def test_poll_reports_the_unsafe_keys(net: Net):
    net.conn.handler = lambda c, i: poll_out(SAVED_WITH_URL, UNSAFE_RUNNING)
    snapshot = await Panel("h").poll()
    assert snapshot.drift == ("HA_URL",)
    assert snapshot.unsafe == ("HA_URL",)
    assert snapshot.settings["HA_URL"] == "http://good:8123"  # the saved one: running is unsafe


async def test_set_env_with_no_copies_creates_them(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=None, calls=calls)
    assert await Panel("h").set_env({"DAY_LEVEL": "9"}) == ("DAY_LEVEL",)
    _, _, stdin = locked_write(calls)
    assert stdin == 'DAY_LEVEL="9"\n'
    calls.clear()
    assert await Panel("h").set_env({}, ["DAY_LEVEL"]) == ()  # nothing there, nothing to do
    assert not any("fcntl.flock" in c for c, _ in calls)


async def test_set_env_with_only_the_running_copy_uses_it_as_base(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=None, running=RUNNING, calls=calls)
    await Panel("h").set_env({"DIM_LEVEL": "3"})
    _, _, stdin = locked_write(calls)
    assert stdin.startswith("# Written by ha-kiosk-setup\n")
    assert 'HA_ZOOM="150"' in stdin


# -- D: the allowlist comes from the panel's tool -------------------------------------------------

TOOL = '"""tool"""\nimport re\nKEYS = set("""\nDAY_LEVEL DIM_LEVEL NEW_SETTING\n""".split())\n'


async def test_setting_allowlist_is_read_from_the_installed_tool(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(setup_tool=TOOL, calls=calls)
    panel = Panel("h")
    assert await panel.set_env({"NEW_SETTING": "on"}) == ("NEW_SETTING",)  # not bundled
    with pytest.raises(InvalidArgument, match="unknown setting"):
        await panel.set_env({"HA_ZOOM": "150"})  # in the bundled list, not in this tool
    with pytest.raises(InvalidArgument, match="unknown setting"):
        await panel.set_env({"TYPO_KEY": "1"})
    assert await panel.set_env({"GESTURE_2_TAP": "x"})  # patterns stay allowed
    tool_reads = [c for c, _ in calls if c.startswith("cat /var/lib/ha-kiosk/ha-kiosk-setup")]
    assert len(tool_reads) == 1  # cached for the connection


async def test_allowlist_cache_is_per_connection(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(setup_tool=TOOL, calls=calls)
    panel = Panel("h")
    await panel.set_env({"NEW_SETTING": "on"})
    await panel.close()
    net.conn = FakeConn(net.conn.handler)  # the next connection is a different object
    await panel.set_env({"DIM_LEVEL": "2"})
    assert len([c for c, _ in calls if c.startswith("cat /var/lib/ha-kiosk/ha-kiosk-setup")]) == 2


@pytest.mark.parametrize("tool", [None, "def broken(:\n", "X = 1\n", 'KEYS = {"lower"}\n'])
async def test_allowlist_falls_back_to_the_bundled_list(net: Net, tool: str | None):
    net.conn.handler = env_handler(setup_tool=tool)
    panel = Panel("h")
    assert await panel.set_env({"HA_ZOOM": "150"}) == ("HA_ZOOM",)
    with pytest.raises(InvalidArgument, match="unknown setting"):
        await panel.set_env({"NEW_SETTING": "on"})  # only a newer tool knows it


async def test_charset_rules_stay_strict_with_a_dynamic_allowlist(net: Net):
    net.conn.handler = env_handler(setup_tool=TOOL)
    panel = Panel("h")
    for bad in ('a"b', "a$b", "a`b", "a\\b", "a'b", "a\nb"):
        with pytest.raises(InvalidArgument):
            await panel.set_env({"NEW_SETTING": bad})
    with pytest.raises(InvalidArgument):
        await panel.set_env({"bad key": "x"})


# -- E: blank lines -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '# Written by ha-kiosk-setup\nA="1"\nB="2"\n',
        "\nA=1\n",  # a blank first line that IS in the file stays
        "A=1\n\n\nB=2\n\n",
        "# only a comment\n",
        'A="1"\n# trailing comment\n',
    ],
)
async def test_round_trip_changes_only_what_was_asked(net: Net, text: str):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=text, calls=calls)
    await Panel("h").set_env({"DAY_LEVEL": "7"})
    _, _, stdin = locked_write(calls)
    assert stdin == text + 'DAY_LEVEL="7"\n'


async def test_banner_output_before_the_settings_is_not_written_into_kiosk_env(net: Net):
    calls: list[tuple[str, Any]] = []
    base = env_handler(base='A="1"', calls=calls)

    def handler(cmd: str, stdin: Any) -> Any:
        if cmd.startswith("echo '#--kl-"):
            return state_out('A="1"', prelude="\nWelcome to the panel\n\n")
        return base(cmd, stdin)

    net.conn.handler = handler
    await Panel("h").set_env({"DAY_LEVEL": "7"})
    _, _, stdin = locked_write(calls)
    assert stdin == 'A="1"\nDAY_LEVEL="7"\n'


async def test_file_without_trailing_newline_round_trips(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base="A=1", calls=calls)
    await Panel("h").set_env({"DAY_LEVEL": "7"})
    _, _, stdin = locked_write(calls)
    assert stdin == 'A=1\nDAY_LEVEL="7"\n'


async def test_no_op_round_trip_writes_nothing(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base="\n# c\nDAY_LEVEL=1\n", calls=calls)
    assert await Panel("h").set_env({"DAY_LEVEL": "1"}) == ()
    assert not any("fcntl.flock" in c for c, _ in calls)


def run_sh_c(command: str, cwd: Any) -> str:
    """Run ``sh -c`` like sshd does (no ``-e``)."""
    import subprocess

    done = subprocess.run(  # noqa: S603
        ["sh", "-c", command],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


NONCE = "0123456789abcdef"


def test_state_read_survives_real_sh_output(tmp_path: Any):
    """Run the real read command with sh over files with awkward endings, then parse it."""
    from pybrilliantkiosk.panel import _parse_state, _state_command

    (tmp_path / "saved").write_bytes(b"\n# c\nA=1\n\n")
    (tmp_path / "running").write_bytes(b"A=1\nB=2")  # no trailing newline
    command = (
        _state_command(NONCE)
        .replace("/var/lib/ha-kiosk/config/kiosk.env", str(tmp_path / "saved"))
        .replace("/etc/ha-kiosk/kiosk.env", str(tmp_path / "running"))
        .replace("/var/lib/ha-kiosk/overrides.env", str(tmp_path / "overrides"))
    )
    state = _parse_state(run_sh_c(command, tmp_path), NONCE)
    assert state.saved is not None
    assert state.saved.serialize() == "\n# c\nA=1\n\n"
    assert state.running is not None
    assert state.running.serialize() == "A=1\nB=2\n"
    assert state.drift == ("B",)
    assert state.overrides == {}
    (tmp_path / "saved").unlink()
    assert _parse_state(run_sh_c(command, tmp_path), NONCE).saved is None


# -- F: one exec per poll -------------------------------------------------------------------------


async def test_poll_is_one_ssh_command_with_everything_in_it(net: Net):
    net.conn.handler = lambda c, i: poll_out(
        'DAY_LEVEL="9"\nMQTT_PASSWORD="s3cret"\n',
        'DAY_LEVEL="12"\nMQTT_PASSWORD="s3cret"\n',
        overrides="SCREENSAVER=0\nOTHER=1\n",
    )
    snapshot = await Panel("h").poll()
    assert len(net.conn.calls) == 1  # was four (health, settings, status, drift)
    command = net.conn.calls[0][0]
    for needle in (
        "systemctl is-active ha-kiosk.service",
        "systemctl is-active ha-kiosk-setup-job.service",
        "/etc/ha-kiosk/kiosk.env",
        "/var/lib/ha-kiosk/config/kiosk.env",
        "/var/lib/ha-kiosk/overrides.env",
        "/var/lib/ha-kiosk/state",
        "/run/ha-kiosk/heartbeat.json",
        "sha256sum",
    ):
        assert needle in command
    assert snapshot.health.uptime_seconds == 100.5
    assert snapshot.health.version == "v1.4.0"
    assert snapshot.health.unit_active("ha-kiosk")
    assert snapshot.setup.state == "active"
    assert snapshot.setup.version == "v1.4.0"
    assert snapshot.settings["DAY_LEVEL"] == "12"  # the running copy wins
    assert snapshot.settings["SCREENSAVER"] == "0"  # overrides on top
    assert "OTHER" not in snapshot.settings
    assert snapshot.settings["MQTT_PASSWORD"] != "s3cret"  # masked
    assert snapshot.drift == ("DAY_LEVEL",)
    assert snapshot.unsafe == ()


async def test_a_poll_starts_no_python_interpreter(net: Net):
    """The per-minute poll used to spawn the setup tool's Python (a heavy process on the panel)."""
    net.conn.handler = lambda c, i: poll_out("A=1")
    await Panel("h").poll()
    command = net.conn.calls[0][0]
    assert "python" not in command.lower()
    assert "ha-kiosk-setup status" not in command
    assert "/data/switch-embedded" not in command


async def test_poll_builds_the_whole_setup_status_from_the_files(net: Net):
    facts = {
        "tool": "present",
        "current": "v1.5.0",
        "state": "active",
        "previous": "v1.4.0",
        "mqtt_base": "brilliant_kiosk",
        "firmware": "",
        "os_release": 'NAME="Brilliant"',
        "now": "1010",
        "secrets": "/etc/ha-kiosk/token /etc/ha-kiosk/kiosk.env ",
    }
    heartbeat = '{"time": 1000.5, "ha": "connected"}'
    result = '{"command": "install", "exit": 0, "message": "ok"}'
    net.conn.handler = lambda c, i: poll_out(
        'KIOSK_MQTT="kiosk"\n', facts=facts, heartbeat=heartbeat, result=result
    )
    setup = (await Panel("h").poll()).setup
    assert (setup.state, setup.version, setup.previous) == ("active", "v1.5.0", "v1.4.0")
    assert (setup.mqtt_mode, setup.mqtt_base) == ("kiosk", "brilliant_kiosk")
    assert setup.firmware == 'NAME="Brilliant"'
    assert setup.heartbeat_age_s == 9.5
    assert setup.ha_connected is True
    assert setup.secrets_present == ("/etc/ha-kiosk/token", "/etc/ha-kiosk/kiosk.env")
    assert setup.last_result == {"command": "install", "exit": 0, "message": "ok"}
    # Unit states come from the health section, not from a second systemctl per unit.
    assert setup.services["ha-kiosk"] is True
    assert setup.services["ha-kiosk-voice"] is False


@pytest.mark.parametrize(
    ("facts", "units", "state"),
    [
        ({"state": "active", "job": "active"}, {"ha-kiosk.service": "active"}, "in_progress"),
        ({"state": "activating"}, {"ha-kiosk.service": "active"}, "failed"),
        ({"state": "active"}, {"ha-kiosk.service": "inactive"}, "failed"),
        ({"state": "active", "job": "inactive"}, {"ha-kiosk.service": "active"}, "active"),
        ({"state": "disabled"}, {}, "disabled"),
        ({"state": "", "qml": "1"}, {}, "unmanaged"),
        ({"state": ""}, {}, "not_installed"),
    ],
)
def test_setup_state_follows_the_tools_status_rules(
    facts: dict[str, str], units: dict[str, str], state: str
):
    from pybrilliantkiosk.setup import status_from_files

    assert status_from_files({"tool": "present", **facts}, "", "", units, {}).state == state


def test_a_panel_without_the_setup_tool_is_not_installed():
    from pybrilliantkiosk.setup import status_from_files

    status = status_from_files({"tool": "absent", "state": "active"}, "", "", {}, {})
    assert status.state == "not_installed"
    assert not status.installed


@pytest.mark.parametrize(
    ("heartbeat", "age", "ha"),
    [
        ("", None, None),
        ("not json", None, None),
        ("[1, 2]", None, None),
        ('{"time": 0, "ha": "connecting"}', None, False),
        ('{"time": "x"}', None, None),
        ('{"time": 990, "ha": true}', 10.0, True),
        ('{"time": 990.25, "ha": 3}', 9.8, None),
    ],
)
def test_heartbeat_is_read_like_the_tool_does(heartbeat: str, age: float | None, ha: bool | None):
    from pybrilliantkiosk.setup import status_from_files

    facts = {"tool": "present", "state": "active", "now": "1000"}
    status = status_from_files(facts, heartbeat, "", {"ha-kiosk.service": "active"}, {})
    assert status.heartbeat_age_s == age
    assert status.ha_connected is ha


def test_heartbeat_age_needs_the_panels_clock():
    from pybrilliantkiosk.setup import status_from_files

    facts = {"tool": "present", "state": "active", "now": ""}
    heartbeat = '{"time": 990}'
    assert status_from_files(facts, heartbeat, "", {}, {}).heartbeat_age_s is None
    assert status_from_files({**facts, "now": "x"}, heartbeat, "", {}, {}).heartbeat_age_s is None


async def test_poll_with_no_files_and_the_tool_missing(net: Net):
    net.conn.handler = lambda c, i: poll_out(None, None, facts={"tool": "absent"})
    snapshot = await Panel("h").poll()
    assert snapshot.setup.state == "not_installed"
    assert snapshot.setup.installed is False
    assert snapshot.settings == {}
    assert snapshot.drift == ()


async def test_poll_reads_the_running_copy_for_the_mqtt_mode_when_there_is_no_saved_one(net: Net):
    net.conn.handler = lambda c, i: poll_out(None, 'KIOSK_MQTT="kiosk"\n')
    assert (await Panel("h").poll()).setup.mqtt_mode == "kiosk"
    net.conn.handler = lambda c, i: poll_out("# nothing set\n", 'KIOSK_MQTT="kiosk"\n')
    assert (await Panel("h").poll()).setup.mqtt_mode == "1"  # the saved copy wins, as in the tool
    net.conn.handler = lambda c, i: poll_out(None, None)
    assert (await Panel("h").poll()).setup.mqtt_mode == "1"


@pytest.mark.parametrize(
    "stdout",
    [
        "garbage, no begin marker",
        "#--kl-TESTNONCE:begin--\n#--kl-TESTNONCE:overrides--\n",  # no health or status section
        "#--kl-TESTNONCE:begin--\n#--kl-TESTNONCE:health--\nuptime=1\n"
        "#--kl-TESTNONCE:overrides--\n",  # no setup section
    ],
)
async def test_poll_garbled_output_is_a_command_failure(net: Net, stdout: str):
    net.conn.handler = lambda c, i: out(stdout)
    with pytest.raises(CommandFailed, match="unexpected output"):
        await Panel("h").poll()


async def test_a_present_copy_without_a_hash_is_refused(net: Net):
    net.conn.handler = lambda c, i: out(
        "#--kl-TESTNONCE:begin--\n#--kl-TESTNONCE:config:present--\nA=1\n#--kl-TESTNONCE:overrides--\n"
    )
    with pytest.raises(CommandFailed, match="no hash"):
        await Panel("h").get_settings()


def _local_poll_command(tmp_path: Any, nonce: str = NONCE, job: str = "inactive") -> str:
    """The real poll command with the panel's paths pointed into ``tmp_path``."""
    from pybrilliantkiosk.panel import _poll_command

    command = (
        _poll_command(nonce)
        .replace("/var/lib/ha-kiosk", str(tmp_path / "var"))
        .replace("/etc/ha-kiosk/VERSION", str(tmp_path / "VERSION"))
        .replace("/etc/ha-kiosk", str(tmp_path / "etc"))
        .replace("/run/ha-kiosk", str(tmp_path / "run"))
        .replace("/etc/brilliant_release", str(tmp_path / "brilliant_release"))
        .replace("/etc/os-release", str(tmp_path / "os-release"))
    )
    # systemctl is not the point here: it answers like a panel whose job unit is ``job``.
    return f"systemctl() {{ echo {job}; }}\n{command}"


def _sections(tmp_path: Any, **kw: Any) -> Any:
    from pybrilliantkiosk.panel import _split_sections

    return _split_sections(run_sh_c(_local_poll_command(tmp_path, **kw), tmp_path), NONCE)


def test_real_sh_runs_the_whole_poll_command(tmp_path: Any):
    """Run the real command with sh: markers, hashes, a file without a final newline, status."""
    from pybrilliantkiosk.panel import _facts, _state_from, parse_health
    from pybrilliantkiosk.setup import status_from_files

    for name in ("var", "etc", "run", "var/config"):
        (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / "var" / "config" / "kiosk.env").write_bytes(b"A=1\nB=2\n")
    (tmp_path / "etc" / "kiosk.env").write_bytes(b"A=1\nB=3")  # no trailing newline
    (tmp_path / "var" / "overrides.env").write_bytes(
        b"DAY_LEVEL=9"
    )  # must not glue on what follows
    (tmp_path / "VERSION").write_text("v9.9.9\n")
    (tmp_path / "var" / "ha-kiosk-setup").write_text("#!tool\n")
    (tmp_path / "var" / "current").write_text("v9.9.9\nsecond line=ignored\n")
    (tmp_path / "var" / "state").write_text("active\n")
    (tmp_path / "var" / "mqtt_base").write_text("brilliant_kiosk")
    (tmp_path / "etc" / "token").write_text("t")
    (tmp_path / "run" / "heartbeat.json").write_text('{"time": 1, "ha": "connected"}')
    (tmp_path / "run" / "setup-result.json").write_text('{"command": "install", "exit": 0}')
    (tmp_path / "os-release").write_text('NAME="Test OS"\nID=test\n')
    sections = _sections(tmp_path)
    state = _state_from(sections)
    assert state.config_sha == hashlib.sha256(b"A=1\nB=2\n").hexdigest()
    assert state.running_sha == hashlib.sha256(b"A=1\nB=3").hexdigest()
    assert state.drift == ("B",)
    assert state.overrides == {"DAY_LEVEL": "9"}
    health = parse_health(sections["health"].body)
    assert health.version == "v9.9.9"
    facts = _facts(sections["setup"].body)
    assert facts["current"] == "v9.9.9"  # first line only: a file cannot add facts
    assert "second line" not in facts
    assert facts["firmware"] == ""
    assert facts["os_release"] == 'NAME="Test OS"'
    assert set(facts["secrets"].split()) == {
        str(tmp_path / "etc" / "token"),
        str(tmp_path / "etc" / "kiosk.env"),
        str(tmp_path / "var" / "config" / "kiosk.env"),
    }
    assert facts["now"].isdigit()
    status = status_from_files(
        facts,
        sections["heartbeat"].body,
        sections["result"].body,
        {"ha-kiosk.service": "active"},
        {},
    )
    assert (status.state, status.version, status.previous) == ("active", "v9.9.9", None)
    assert status.mqtt_base == "brilliant_kiosk"
    assert status.ha_connected is True
    assert status.last_result == {"command": "install", "exit": 0}
    assert status.heartbeat_age_s is not None and status.heartbeat_age_s > 1_000_000


def test_real_sh_poll_without_files_or_tool(tmp_path: Any):
    from pybrilliantkiosk.panel import _facts, _state_from
    from pybrilliantkiosk.setup import status_from_files

    sections = _sections(tmp_path)
    state = _state_from(sections)
    assert (state.saved, state.running, state.config_sha, state.running_sha) == (
        None,
        None,
        None,
        None,
    )
    facts = _facts(sections["setup"].body)
    assert facts["tool"] == "absent"
    assert status_from_files(facts, "", "", {}, {}).state == "not_installed"
    assert sections["heartbeat"].body == "\n"  # absent files are empty sections, not errors


def test_real_sh_poll_sees_a_running_job(tmp_path: Any):
    from pybrilliantkiosk.panel import _facts

    assert _facts(_sections(tmp_path, job="active")["setup"].body)["job"] == "active"


def test_a_kiosk_env_line_equal_to_the_old_static_marker_cannot_desync_the_poll(tmp_path: Any):
    """Markers carry a random per-command nonce: file content cannot forge or collide with one."""
    from pybrilliantkiosk.panel import _state_from

    old = "#--kl:overrides--\n#--kl:running:absent--\n#--kl:begin--\n"
    forged = "#--kl-0000000000000000:overrides--\nA=evil\n"
    for name in ("var", "etc", "var/config"):
        (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / "var" / "config" / "kiosk.env").write_text(old + forged + 'REAL="1"\n')
    (tmp_path / "etc" / "kiosk.env").write_text('REAL="1"\n')
    (tmp_path / "var" / "overrides.env").write_text("DAY_LEVEL=9\n")
    state = _state_from(_sections(tmp_path))
    assert state.saved is not None
    assert state.saved.serialize() == old + forged + 'REAL="1"\n'  # intact, nothing split
    assert state.running is not None
    assert state.overrides == {"DAY_LEVEL": "9"}  # the overrides section is still the real one
    assert state.config_sha == hashlib.sha256((old + forged + 'REAL="1"\n').encode()).hexdigest()


def test_every_command_uses_a_fresh_unguessable_nonce():
    from pybrilliantkiosk.panel import _new_nonce, _poll_command, _state_command

    nonces = {_new_nonce() for _ in range(50)}
    assert len(nonces) == 50
    assert all(re.fullmatch(r"[0-9a-f]{16}", n) for n in nonces)
    assert _poll_command("aa" * 8) != _poll_command("bb" * 8)
    assert "#--kl-" + "aa" * 8 + ":begin--" in _state_command("aa" * 8)


async def test_a_reply_with_the_wrong_nonce_is_garbled_output(net: Net):
    net.conn.handler = lambda c, i: out(
        "#--kl-ffffffffffffffff:begin--\n#--kl-ffffffffffffffff:overrides--\n"
    )
    with pytest.raises(CommandFailed, match="unexpected output"):
        await Panel("h").get_settings()


# -- G: optimistic concurrency --------------------------------------------------------------------


async def test_write_script_carries_the_hashes_of_what_was_read(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED, running=RUNNING, calls=calls)
    await Panel("h").set_env({"DIM_LEVEL": "3"})
    _, script, _ = locked_write(calls)
    assert sha(SAVED) in script
    assert sha(RUNNING) in script
    assert script.index("incomplete input") < script.index(sha(SAVED))  # stdin is checked first


async def test_write_script_expects_absent_copies_to_still_be_absent(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=None, calls=calls)
    await Panel("h").set_env({"DAY_LEVEL": "9"})
    _, script, _ = locked_write(calls)
    assert "[ ! -e /var/lib/ha-kiosk/config/kiosk.env ] || changed" in script
    assert "[ ! -e /etc/ha-kiosk/kiosk.env ] || changed" in script


async def test_a_concurrent_change_is_retried_once_on_fresh_state(net: Net):
    calls: list[tuple[str, Any]] = []
    reads = 0
    base = env_handler(base=ENV_TEXT, write_status=[10, 0], calls=calls)

    def handler(cmd: str, stdin: Any) -> Any:
        nonlocal reads
        if cmd.startswith("echo '#--kl-"):
            reads += 1
            if reads == 2:  # someone else edited the file between the two attempts
                return state_out(ENV_TEXT + "HA_ZOOM=150\n")
        return base(cmd, stdin)

    net.conn.handler = handler
    changed = await Panel("h").set_env({"DIM_LEVEL": "3"})
    assert changed == ("DIM_LEVEL",)
    assert reads == 2
    writes = [(c, i) for c, i in calls if "fcntl.flock" in c]
    assert len(writes) == 2
    first_stdin, second_stdin = (i.decode() for _, i in writes)
    assert "HA_ZOOM" not in first_stdin
    assert "HA_ZOOM=150" in second_stdin  # the retry built on the new content, not the old
    assert sha(ENV_TEXT + "HA_ZOOM=150\n") in writes[1][0]


async def test_a_second_concurrent_change_is_reported(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(write_status=[10, 10], calls=calls)
    with pytest.raises(ConcurrentModification, match="changed on the panel"):
        await Panel("h").set_env({"DIM_LEVEL": "3"}, restart=True)
    assert len([c for c, _ in calls if "fcntl.flock" in c]) == 2  # exactly one retry
    assert not any("systemctl restart" in c for c, _ in calls)


async def test_reconcile_env_retries_on_a_concurrent_change_too(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED, running=RUNNING, write_status=[10, 0], calls=calls)
    assert await Panel("h").reconcile_env() == ("DAY_LEVEL", "HA_ZOOM")
    assert len([c for c, _ in calls if "fcntl.flock" in c]) == 2


# -- H: install size ------------------------------------------------------------------------------


def install_handler(calls: list[tuple[str, Any]]) -> Any:
    def handler(cmd: str, stdin: Any) -> Any:
        calls.append((cmd, stdin))
        if cmd.startswith("d=$(mktemp"):
            return out("/tmp/ha-kiosk-install.Abcdef123\n")  # noqa: S108
        if cmd.startswith("sha256sum"):
            return out(f"{sha('bundle')}  x\n")
        if "ha-kiosk-setup install" in cmd:
            return out('{"state": "in_progress"}')
        if "ha-kiosk-setup wait" in cmd:
            return out('{"state": "active"}')
        if "ha-kiosk-setup status" in cmd:
            return out('{"state": "active"}')
        return out()

    return handler


async def test_install_passes_the_pinned_size_to_the_tool(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = install_handler(calls)
    await Panel("h").install(b"bundle", sha("bundle"), size=6)
    install = next(c for c, _ in calls if "ha-kiosk-setup install" in c)
    assert "--size 6 " in install
    assert f"--sha256 {sha('bundle')}" in install


async def test_install_defaults_the_size_to_the_download_length(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = install_handler(calls)
    await Panel("h").install(b"bundle", sha("bundle"))
    assert "--size 6 " in next(c for c, _ in calls if "ha-kiosk-setup install" in c)


async def test_install_refuses_a_bundle_of_the_wrong_size_before_any_io(net: Net):
    with pytest.raises(ChecksumMismatch, match="expected 7"):
        await Panel("h").install(b"bundle", sha("bundle"), size=7)
    assert net.connects == 0


# -- unsafe lines of the saved copy are not carried into the root-sourced running copy ------------

SAVED_WITH_EVIL_LINE = 'DAY_LEVEL="9"\nHA_ZOOM=150;touch /tmp/pwned\nKIOSK_MQTT="1"\n'


async def test_set_env_refuses_to_copy_an_unsafe_line_of_the_saved_copy(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED_WITH_EVIL_LINE, calls=calls)
    with pytest.raises(InvalidArgument, match="HA_ZOOM") as err:
        await Panel("h").set_env({"DIM_LEVEL": "3"}, restart=True)
    assert "nothing was changed" in str(err.value)
    assert not any("fcntl.flock" in c or "restart" in c for c, _ in calls)  # nothing written


async def test_set_env_names_every_unsafe_line_and_junk_without_a_key(net: Net):
    base = 'A="1"\nHA_ZOOM=150;id\nrm -rf /\nDAY_LEVEL="$(id)"\n'
    net.conn.handler = env_handler(base=base)
    with pytest.raises(InvalidArgument) as err:
        await Panel("h").set_env({"DIM_LEVEL": "3"})
    for named in ("HA_ZOOM", "line 3", "DAY_LEVEL"):
        assert named in str(err.value)


async def test_set_env_may_replace_or_delete_the_unsafe_line_it_is_told_about(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED_WITH_EVIL_LINE, calls=calls)
    assert await Panel("h").set_env({"HA_ZOOM": "150"}) == ("HA_ZOOM",)
    _, _, stdin = locked_write(calls)
    assert stdin == 'DAY_LEVEL="9"\nHA_ZOOM="150"\nKIOSK_MQTT="1"\n'
    calls.clear()
    assert await Panel("h").set_env({}, ["HA_ZOOM"]) == ("HA_ZOOM",)
    _, _, stdin = locked_write(calls)
    assert "touch" not in stdin


async def test_an_unsafe_line_does_not_block_a_call_that_writes_nothing(net: Net):
    calls: list[tuple[str, Any]] = []
    net.conn.handler = env_handler(base=SAVED_WITH_EVIL_LINE, calls=calls)
    assert await Panel("h").set_env({"DAY_LEVEL": "9"}) == ()
    assert not any("fcntl.flock" in c for c, _ in calls)


async def test_an_unsafe_hand_edited_line_in_the_running_copy_is_not_carried_over(net: Net):
    """The running copy equal to the saved one in value, but with a bare ``;`` line."""
    calls: list[tuple[str, Any]] = []
    saved = 'DAY_LEVEL="9"\nHA_ZOOM=150;id\n'
    net.conn.handler = env_handler(
        base=saved, running='DAY_LEVEL="9"\nHA_ZOOM="150;id"\n', calls=calls
    )
    with pytest.raises(InvalidArgument, match="HA_ZOOM"):
        await Panel("h").set_env({"DIM_LEVEL": "3"})
    assert not any("fcntl.flock" in c for c, _ in calls)


# -- the device id of the stock firmware -------------------------------------------------------


async def test_device_id_is_read_with_brilliants_own_python(net: Net):
    net.conn.handler = lambda c, i: out("device 0123456789abcdef0123\n")
    assert await Panel("h").device_id() == "0123456789abcdef0123"
    command = net.conn.calls[0][0]
    assert command.startswith("cd /data/switch-embedded && env/bin/python -c ")
    assert "/etc/ha-kiosk" not in command  # no file of the kiosk: it works on a stock panel
    assert "/var/run/brilliant/server_socket" in command
    assert "MessageType.HELLO" in command


@pytest.mark.parametrize(
    ("stdout", "status", "stderr"),
    [
        ("", 1, "Traceback\nConnectionRefusedError: nope"),
        ("device \n", 0, ""),
        ("device short\n", 0, ""),
        ("device bad id with spaces\n", 0, ""),
        ("device " + "a" * 65 + "\n", 0, ""),
        ("something else\n", 0, ""),
        ("device 0123456789abcdef0123\n", 3, ""),
        ("", None, ""),
    ],
)
async def test_device_id_unavailable(net: Net, stdout: str, status: int | None, stderr: str):
    from pybrilliantkiosk import DeviceIdUnavailable

    net.conn.handler = lambda c, i: out(stdout, status, stderr)
    with pytest.raises(DeviceIdUnavailable, match="Brilliant device id"):
        await Panel("h").device_id()


async def test_device_id_error_names_the_last_stderr_line(net: Net):
    from pybrilliantkiosk import DeviceIdUnavailable

    net.conn.handler = lambda c, i: out("", 1, "Traceback\nConnectionRefusedError: socket gone\n")
    with pytest.raises(DeviceIdUnavailable, match="socket gone"):
        await Panel("h").device_id()


def test_the_device_id_script_does_the_hello_of_brilliant_bus(tmp_path: Any):
    """Run the script itself against stubs of Brilliant's libraries and a fake bus socket."""
    import socket as socketlib
    import subprocess
    import sys
    import threading

    from pybrilliantkiosk import bus

    lib = tmp_path / "lib"
    pkg = tmp_path / "thrift_types" / "protocol"
    lib.mkdir()
    pkg.mkdir(parents=True)
    (tmp_path / "thrift_types" / "__init__.py").write_text("")
    pkg.joinpath("__init__.py").write_text("")
    lib.joinpath("__init__.py").write_text("")
    lib.joinpath("serialization.py").write_text(
        "import base64, json\n"
        "def serialize(obj):\n"
        "    raw = json.dumps(obj, default=lambda o: vars(o))\n"
        "    return base64.b64encode(raw.encode()).decode()\n"
        "class _N:\n"
        "    def __init__(self, d):\n"
        "        for k, v in d.items():\n"
        "            setattr(self, k, _N(v) if isinstance(v, dict) else v)\n"
        "def deserialize(cls, text):\n"
        "    return _N(json.loads(base64.b64decode(text)))\n"
    )
    pkg.joinpath("ttypes.py").write_text(
        "class _Base:\n"
        "    def __init__(self, **kw):\n"
        "        self.__dict__.update(kw)\n"
        "class Hello(_Base): pass\n"
        "class Message(_Base): pass\n"
        "class MessageBodyUnion(_Base): pass\n"
        "class MessageType:\n"
        "    HELLO = 2\n"
    )
    sock_path = str(tmp_path / "bus.sock")
    script = bus._DEVICE_ID_SCRIPT.replace(bus.SWITCH_DIR, str(tmp_path)).replace(
        bus.BUS_SOCKET, sock_path
    )
    server = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    received: list[bytes] = []

    def serve() -> None:
        conn, _ = server.accept()
        with conn:
            received.append(conn.makefile("rb").readline())
            reply = {"body_union": {"hello": {"domain": "deadbeefcafe0123"}}}
            import base64
            import json

            conn.sendall(base64.b64encode(json.dumps(reply).encode()) + b"\n")

    thread = threading.Thread(target=serve)
    thread.start()
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    thread.join(timeout=10)
    server.close()
    assert done.returncode == 0, done.stderr
    assert bus.parse_device_id(done.stdout) == "deadbeefcafe0123"
    import base64
    import json

    hello = json.loads(base64.b64decode(received[0]))
    assert hello["message_type"] == 2  # HELLO
    assert hello["body_union"]["hello"]["name"] == "ha_kiosk_identity"
