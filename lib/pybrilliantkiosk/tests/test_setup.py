import hashlib
import io
import json
import tarfile
from typing import Any

import asyncssh
import pytest

from pybrilliantkiosk import (
    ActivationRolledBack,
    CannotConnect,
    ChecksumMismatch,
    InvalidArgument,
    NotInstalled,
    Panel,
    PanelError,
    ReleaseError,
    SettingsRejected,
    SetupBusy,
    SetupError,
    StockUIRestored,
    VerificationFailed,
    kiosk_env_from_bundle,
)
from pybrilliantkiosk.panel import INSTALL_PREFIX
from pybrilliantkiosk.setup import error_for_exit, parse_json_object, parse_status
from test_panel import FakeSFTP, Net, out

BUNDLE = b"tarball"
SHA = hashlib.sha256(BUNDLE).hexdigest()
WORKDIR = f"{INSTALL_PREFIX}.AbC123xYz"

STATUS = {
    "state": "active",
    "version": "v1.4.0",
    "previous": "v1.3.0",
    "services": {"ha-kiosk": {"active": True}, "ha-kiosk-voice": {"active": False}, "x": 1},
    "mqtt": {"mode": "kiosk", "base": "brilliant_kiosk"},
    "ui": {"heartbeat_age_s": 3.5, "home_assistant": True},
    "firmware": "1.2",
    "secrets_present": ["/etc/ha-kiosk/token"],
    "last_result": {"command": "install", "exit": 0},
}


def test_parse_status_full_and_minimal():
    status = parse_status(STATUS)
    assert status.state == "active"
    assert status.installed
    assert status.services == {"ha-kiosk": True, "ha-kiosk-voice": False}
    assert status.mqtt_mode == "kiosk"
    assert status.heartbeat_age_s == 3.5
    assert status.ha_connected is True
    assert status.secrets_present == ("/etc/ha-kiosk/token",)
    assert status.last_result == {"command": "install", "exit": 0}
    bare = parse_status({})
    assert bare.state == "not_installed"
    assert not bare.installed
    assert bare.services == {}
    assert bare.heartbeat_age_s is None
    assert bare.ha_connected is None
    assert bare.secrets_present == ()
    odd = parse_status({"state": "failed", "services": "x", "mqtt": 1, "ui": 2, "last_result": 3})
    assert odd.state == "failed"
    assert odd.mqtt_mode is None


def test_parse_json_object_skips_log_lines():
    text = 'ha-kiosk: doing things {not json\n{"a": 1}\n[1]\n{"b": {"c": 2}}\n'
    assert parse_json_object(text) == {"b": {"c": 2}}
    assert parse_json_object("no json here") == {}
    assert parse_json_object("[1, 2]") == {}


@pytest.mark.parametrize(
    ("code", "cls"),
    [
        (3, VerificationFailed),
        (4, SettingsRejected),
        (5, ActivationRolledBack),
        (6, StockUIRestored),
        (7, NotInstalled),
        (8, SetupBusy),
        (1, SetupError),
        (2, SetupError),
    ],
)
def test_error_for_exit(code: int, cls: type[SetupError]):
    err = error_for_exit(code, json.dumps({"error": "bad thing", "exit": code}))
    assert type(err) is cls
    assert err.code == code
    assert "bad thing" in str(err)


def test_error_for_exit_messages():
    from_last = error_for_exit(5, json.dumps({"last_result": {"message": "rolled back"}}))
    assert "rolled back" in str(from_last)
    assert "status 9" in str(error_for_exit(9, ""))
    assert SetupError("x").code == 1


async def test_setup_status_runs_tool_or_reports_not_installed(net: Net):
    net.conn.handler = lambda c, i: out(json.dumps(STATUS))
    status = await Panel("h").setup_status()
    assert status.version == "v1.4.0"
    cmd = net.conn.calls[0][0]
    assert "/data/switch-embedded/env/bin/python /var/lib/ha-kiosk/ha-kiosk-setup status" in cmd
    assert "not_installed" in cmd


async def test_setup_status_error(net: Net):
    net.conn.handler = lambda c, i: out('{"error": "no", "exit": 7}', 7)
    with pytest.raises(NotInstalled):
        await Panel("h").setup_status()
    net.conn.handler = lambda c, i: out("", None)
    with pytest.raises(SetupError):
        await Panel("h").setup_status()


async def test_setup_diagnostics(net: Net):
    net.conn.handler = lambda c, i: out(json.dumps({"state": "active", "logs": ["a"]}))
    assert (await Panel("h").setup_diagnostics())["logs"] == ["a"]
    assert net.conn.calls[0][0].endswith("ha-kiosk-setup diagnostics")
    net.conn.handler = lambda c, i: out("{}", 1)
    with pytest.raises(SetupError):
        await Panel("h").setup_diagnostics()


async def test_check_env(net: Net):
    net.conn.handler = lambda c, i: out('{"ok": false, "errors": ["line 1: bad"]}', 4)
    assert await Panel("h").check_env("X=1\n") == ("line 1: bad",)
    assert net.conn.calls[0][1] == b"X=1\n"  # text goes out as UTF-8 bytes
    net.conn.handler = lambda c, i: out('{"ok": true, "errors": []}')
    assert await Panel("h").check_env("A=1\n") == ()
    net.conn.handler = lambda c, i: out('{"ok": true}')
    assert await Panel("h").check_env("A=1\n") == ()
    net.conn.handler = lambda c, i: out("", 2)
    with pytest.raises(SetupError):
        await Panel("h").check_env("A=1\n")


async def test_setup_command_detaches_and_waits(net: Net):
    cmds: list[str] = []
    waits = iter([9, 9, 0])

    def handler(cmd: str, stdin: Any) -> Any:
        cmds.append(cmd)
        if "wait" in cmd:
            code = next(waits)
            return out(json.dumps(STATUS), code)
        if "--detach" in cmd:
            return out('{"state": "in_progress"}')
        return out('{"state": "active"}')  # the idle probe

    net.conn.handler = handler
    status = await Panel("h").setup_command("rollback")
    assert status.state == "active"
    assert cmds[0].endswith("ha-kiosk-setup status") or " status;" in cmds[0]  # idle check first
    assert cmds[1].endswith("ha-kiosk-setup rollback --detach")
    assert [c.endswith("wait --timeout 100") for c in cmds[2:]] == [True] * 3


async def test_setup_command_errors(net: Net):
    with pytest.raises(InvalidArgument):
        await Panel("h").setup_command("rm -rf")
    net.conn.handler = lambda c, i: out('{"error": "busy", "exit": 8}', 8)
    with pytest.raises(SetupBusy):  # the idle probe itself reports busy
        await Panel("h").setup_command("reapply")

    def busy_on_start(cmd: str, stdin: Any) -> Any:
        return out('{"error": "busy", "exit": 8}', 8) if "--detach" in cmd else out()

    net.conn.handler = busy_on_start  # a job began between the idle probe and the start
    with pytest.raises(SetupBusy, match="busy"):
        await Panel("h").setup_command("reapply")

    def failing_wait(cmd: str, stdin: Any) -> Any:
        if "wait" in cmd:
            return out(json.dumps({"last_result": {"message": "rolled back"}}), 5)
        return out()

    net.conn.handler = failing_wait
    with pytest.raises(ActivationRolledBack, match="rolled back"):
        await Panel("h").setup_command("enable")


async def test_setup_command_never_finishes(net: Net, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("pybrilliantkiosk.panel.WAIT_ROUNDS", 2)
    net.conn.handler = lambda c, i: out("{}", 9 if "wait" in c else 0)
    with pytest.raises(SetupError, match="still running") as err:
        await Panel("h").setup_command("enable")
    assert err.value.code == 9


def install_handler(
    remote_sha: str = SHA, start: int = 0, wait: int = 0, calls: list[str] | None = None
) -> Any:
    def handler(cmd: str, stdin: Any) -> Any:
        if calls is not None:
            calls.append(cmd)
        if cmd.startswith("d=$(mktemp -d"):
            return out(f"{WORKDIR}\n")
        if cmd.startswith("sha256sum"):
            return out(f"{remote_sha}  {WORKDIR}/bundle.tar.gz\n")
        if "ha-kiosk-setup install" in cmd:
            return out('{"state": "in_progress"}' if start == 0 else '{"error": "x"}', start)
        if "ha-kiosk-setup wait" in cmd:
            return out(json.dumps(STATUS), wait)
        return out()

    return handler


async def test_install_via_setup_tool(net: Net):
    cmds: list[str] = []
    net.conn.handler = install_handler(calls=cmds)
    status = await Panel("h").install(BUNDLE, SHA.upper())
    assert status.version == "v1.4.0"
    assert net.conn.sftp.sink == [BUNDLE]
    assert net.conn.sftp.paths == [f"{WORKDIR}/bundle.tar.gz"]
    mktemp = next(c for c in cmds if "mktemp" in c)
    assert mktemp == f'd=$(mktemp -d {INSTALL_PREFIX}.XXXXXXXX) && chmod 700 "$d" && echo "$d"'
    extract = next(c for c in cmds if "tar -xzOf" in c)
    assert "grep -E '^[^/]+/ha-kiosk-setup$'" in extract
    start = next(c for c in cmds if "ha-kiosk-setup install" in c)
    assert (
        f"--bundle {WORKDIR}/bundle.tar.gz --sha256 {SHA} --size {len(BUNDLE)} --detach"
        in start
    )
    assert "--env" not in start
    assert "--token" not in start
    assert f"{WORKDIR}/ha-kiosk-setup install" in start
    assert cmds[-1] == f"rm -rf {WORKDIR}"


async def test_install_passes_env_and_token_by_file(net: Net):
    cmds: list[str] = []
    stdins: list[Any] = []
    base = install_handler(calls=cmds)

    def handler(cmd: str, stdin: Any) -> Any:
        if stdin is not None and cmd.startswith("umask 077"):
            stdins.append((cmd, stdin))
        return base(cmd, stdin)

    net.conn.handler = handler
    await Panel("h").install(BUNDLE, SHA, env="HA_URL=http://x\n", token="s3cr3t-value")
    start = next(c for c in cmds if "ha-kiosk-setup install" in c)
    assert f"--env {WORKDIR}/env --token {WORKDIR}/token" in start
    assert stdins == [
        (f"umask 077; cat > {WORKDIR}/env", b"HA_URL=http://x\n"),
        (f"umask 077; cat > {WORKDIR}/token", b"s3cr3t-value"),
    ]
    assert "s3cr3t-value" not in start


@pytest.mark.parametrize(
    "error",
    [asyncssh.SFTPFailure("no sftp"), asyncssh.ChannelOpenError(1, "no subsystem")],
)
async def test_install_falls_back_to_cat(net: Net, error: BaseException):
    net.conn.sftp = FakeSFTP([], error)
    cat_inputs: list[Any] = []
    base = install_handler()

    def handler(cmd: str, stdin: Any) -> Any:
        if cmd.startswith("cat > "):
            cat_inputs.append((cmd, stdin))
            return out()
        return base(cmd, stdin)

    net.conn.handler = handler
    await Panel("h").install(BUNDLE, SHA)
    assert cat_inputs == [(f"cat > {WORKDIR}/bundle.tar.gz", BUNDLE)]


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
    assert cmds[-1] == f"rm -rf {WORKDIR}"
    assert not any("ha-kiosk-setup install" in c for c in cmds)

    def no_sha(cmd: str, stdin: Any) -> Any:
        if cmd.startswith("d=$(mktemp"):
            return out(f"{WORKDIR}\n")
        return out("") if cmd.startswith("sha256sum") else out()

    net.conn.handler = no_sha
    with pytest.raises(ChecksumMismatch):
        await panel.install(BUNDLE, SHA)


async def test_install_maps_tool_exit_codes(net: Net):
    net.conn.handler = install_handler(start=3)
    with pytest.raises(VerificationFailed):
        await Panel("h").install(BUNDLE, SHA)
    net.conn.handler = install_handler(wait=6)
    with pytest.raises(StockUIRestored):
        await Panel("h").install(BUNDLE, SHA)


async def test_install_cleanup_failure_is_swallowed(net: Net):
    base = install_handler(remote_sha="e" * 64)

    def handler(cmd: str, stdin: Any) -> Any:
        if cmd == f"rm -rf {WORKDIR}":
            return out("", 1, "nope")
        return base(cmd, stdin)

    net.conn.handler = handler
    with pytest.raises(ChecksumMismatch):
        await Panel("h").install(BUNDLE, SHA)


# -- bundle.py ---------------------------------------------------------------------------


def make_bundle(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


EXAMPLE = b"""# comment
KIOSK_MODE=native
HA_URL=http://192.168.1.10:8123/x
DAY_LEVEL=15
BAD_KEY=1
GESTURE_2_TAP=load.toggle
EVIL=$(rm)
DIM_LEVEL=it's
KIOSK_MQTT=1
"""


def test_kiosk_env_from_bundle():
    bundle = make_bundle({"rel/kiosk.env.example": EXAMPLE, "rel/other": b"x"})
    text = kiosk_env_from_bundle(bundle, {"HA_URL": "http://ha:8123", "MQTT_HOST": "broker"})
    lines = text.splitlines()
    # Rendered like ha-kiosk-setup's render_env: the panel sources this file as root.
    assert 'HA_URL="http://ha:8123"' in lines
    assert 'MQTT_HOST="broker"' in lines
    assert 'KIOSK_MODE="native"' in lines
    assert 'GESTURE_2_TAP="load.toggle"' in lines
    assert 'KIOSK_MQTT="1"' in lines
    assert not any("=" in line and not line.split("=", 1)[1].startswith('"') for line in lines)
    assert not any(line.startswith(("BAD_KEY", "EVIL", "DIM_LEVEL")) for line in lines)
    assert text.endswith("\n")


def test_kiosk_env_from_bundle_errors():
    with pytest.raises(ReleaseError, match=r"no kiosk.env.example"):
        kiosk_env_from_bundle(make_bundle({"rel/x": b"1"}), {})
    with pytest.raises(ReleaseError, match="unreadable"):
        kiosk_env_from_bundle(b"not a tarball", {})
    bundle = make_bundle({"rel/kiosk.env.example": EXAMPLE})
    with pytest.raises(InvalidArgument):
        kiosk_env_from_bundle(bundle, {"UNKNOWN": "1"})
    with pytest.raises(InvalidArgument):
        kiosk_env_from_bundle(bundle, {"HA_URL": "a'b"})


def test_kiosk_env_from_bundle_member_without_data(monkeypatch: pytest.MonkeyPatch):
    bundle = make_bundle({"rel/kiosk.env.example": EXAMPLE})
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)
    with pytest.raises(ReleaseError, match=r"no kiosk.env.example"):
        kiosk_env_from_bundle(bundle, {})


def test_kiosk_env_from_bundle_values_with_shell_metacharacters_stay_inert(tmp_path: Any):
    import subprocess

    bundle = make_bundle({"rel/kiosk.env.example": b"KIOSK_MODE=native\n"})
    text = kiosk_env_from_bundle(bundle, {"HA_URL": "http://h/;touch pwned|id&x>y (z)"})
    env_file = tmp_path / "kiosk.env"
    env_file.write_text(text)
    done = subprocess.run(  # noqa: S603
        ["sh", "-c", '. "$1"; printf %s "$HA_URL"', "sh", str(env_file)],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout == "http://h/;touch pwned|id&x>y (z)"
    assert not (tmp_path / "pwned").exists()


# -- install / setup_command concurrency -----------------------------------------------------


async def test_install_uses_a_fresh_directory_each_time_and_removes_it(net: Net):
    cmds: list[str] = []
    net.conn.handler = install_handler(calls=cmds)
    await Panel("h").install(BUNDLE, SHA)
    mktemps = [c for c in cmds if "mktemp" in c]
    assert len(mktemps) == 1
    assert "XXXXXXXX" in mktemps[0]  # a random suffix, never the old fixed path
    assert f"{INSTALL_PREFIX} " not in " ".join(cmds)
    assert cmds[-1] == f"rm -rf {WORKDIR}"


async def test_install_rejects_unexpected_mktemp_output(net: Net):
    cmds: list[str] = []
    base = install_handler(calls=cmds)

    def handler(cmd: str, stdin: Any) -> Any:
        if cmd.startswith("d=$(mktemp"):
            return out("/etc\n")  # must never be used (or deleted)
        return base(cmd, stdin)

    net.conn.handler = handler
    with pytest.raises(PanelError, match="unexpected output"):
        await Panel("h").install(BUNDLE, SHA)
    assert not any(c.startswith("rm -rf") for c in cmds)
    assert not any("ha-kiosk-setup install" in c for c in cmds)


@pytest.mark.parametrize("operation", ["install", "reapply"])
async def test_install_and_setup_command_refuse_while_a_job_runs(net: Net, operation: str):
    cmds: list[str] = []

    def handler(cmd: str, stdin: Any) -> Any:
        cmds.append(cmd)
        if cmd.endswith("ha-kiosk-setup status") or " status;" in cmd:
            return out(json.dumps({"state": "in_progress"}))
        return out()

    net.conn.handler = handler
    panel = Panel("h")
    with pytest.raises(SetupBusy, match="running a job"):
        if operation == "install":
            await panel.install(BUNDLE, SHA)
        else:
            await panel.setup_command("reapply")
    assert len(cmds) == 1  # only the status probe ran: nothing was uploaded or started


async def test_upload_without_connection_is_an_error():
    with pytest.raises(CannotConnect, match="lost"):
        await Panel("h")._upload(b"x", WORKDIR + "/bundle.tar.gz")


async def test_install_hashes_off_the_event_loop(net: Net, monkeypatch: pytest.MonkeyPatch):
    import threading

    from pybrilliantkiosk import panel as panel_module

    threads: list[str] = []
    real = panel_module.sha256_hex

    def spy(data: bytes) -> str:
        threads.append(threading.current_thread().name)
        return real(data)

    monkeypatch.setattr(panel_module, "sha256_hex", spy)
    net.conn.handler = install_handler()
    await Panel("h").install(BUNDLE, SHA)
    assert threads
    assert threading.main_thread().name not in threads


async def test_concurrent_operations_on_one_panel_are_serialised(net: Net):
    import asyncio

    running = 0
    peak = 0
    base = install_handler()

    async def slow_run(command: str, **kw: Any) -> Any:
        nonlocal running, peak
        if "ha-kiosk-setup reapply --detach" in command:
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
        result = base(command, kw.get("input"))
        return result

    net.conn.run = slow_run  # type: ignore[method-assign]
    panel = Panel("h")
    await asyncio.gather(panel.setup_command("reapply"), panel.setup_command("reapply"))
    assert peak == 1
