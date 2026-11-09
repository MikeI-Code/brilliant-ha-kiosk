"""SSH client for one Brilliant panel."""

from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, cast

import asyncssh

from . import bus
from .env import EnvFile, format_value, validate_key
from .exceptions import (
    AuthFailed,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    HostKeyMismatch,
    InvalidArgument,
    PanelError,
)
from .models import (
    BACKLIGHT_DIR,
    DISPLAY_STATE_PATH,
    ENV_PATH,
    UNITS,
    VERSION_PATH,
    BusListing,
    BusVariable,
    PanelHealth,
)

INSTALL_DIR = "/tmp/ha-kiosk-install"  # noqa: S108 - fixed path on the panel, not on this host
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_LOG_LINES = 5000


@dataclass(frozen=True, slots=True)
class _Result:
    stdout: str
    stderr: str
    exit_status: int | None


def _health_script() -> str:
    lines = [
        "echo \"uptime=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)\"",
        "echo \"loadavg=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)\"",
        "echo \"memtotal=$(sed -n 's/^MemTotal: *\\([0-9]*\\) kB.*/\\1/p' /proc/meminfo)\"",
        "echo \"memavail=$(sed -n 's/^MemAvailable: *\\([0-9]*\\) kB.*/\\1/p' /proc/meminfo)\"",
        f'echo "version=$(head -n 1 {VERSION_PATH} 2>/dev/null)"',
        f'echo "backlight=$(cat {BACKLIGHT_DIR}/brightness 2>/dev/null)"',
        f'echo "backlight_max=$(cat {BACKLIGHT_DIR}/max_brightness 2>/dev/null)"',
        f'echo "display=$(cat {DISPLAY_STATE_PATH} 2>/dev/null)"',
    ]
    lines += [f'echo "unit.{u}=$(systemctl is-active {u} 2>/dev/null)"' for u in UNITS]
    lines.append("true")
    return "\n".join(lines)


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def parse_health(output: str) -> PanelHealth:
    """Parse the output of the health script."""
    data: dict[str, str] = {}
    units: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key.startswith("unit."):
            units[key[5:]] = value.strip() or "unknown"
        else:
            data[key] = value.strip()
    load = data.get("loadavg", "").split()
    load += [""] * (3 - len(load))
    return PanelHealth(
        uptime_seconds=_to_float(data.get("uptime")),
        load_1=_to_float(load[0]),
        load_5=_to_float(load[1]),
        load_15=_to_float(load[2]),
        mem_total_kb=_to_int(data.get("memtotal")),
        mem_available_kb=_to_int(data.get("memavail")),
        version=data.get("version") or None,
        units=units,
        backlight=_to_int(data.get("backlight")),
        backlight_max=_to_int(data.get("backlight_max")),
        display_state=data.get("display") or None,
    )


def _normalise_fp(fingerprint: str) -> str:
    return fingerprint.strip().rstrip("=").lower()


def fingerprint_matches(pinned: str, key: asyncssh.SSHKey) -> bool:
    """Whether a pinned fingerprint matches ``key``.

    ``pinned`` may be an OpenSSH ``SHA256:...`` or ``MD5:aa:bb:...`` fingerprint (as printed by
    ssh-keygen or plink), alone or embedded in text such as ``ssh-ed25519 255 SHA256:...``.
    """
    candidates = {_normalise_fp(token) for token in pinned.split()}
    sha = _normalise_fp(key.get_fingerprint("sha256"))
    md5 = _normalise_fp(key.get_fingerprint("md5"))
    return sha in candidates or md5 in candidates or md5.removeprefix("md5:") in candidates


class _PinningClient(asyncssh.SSHClient):
    """Validates the host key before authentication and records its fingerprint."""

    def __init__(self, pinned: str | None) -> None:
        self._pinned = pinned
        self.fingerprint: str | None = None

    def validate_host_public_key(
        self, host: str, addr: str, port: int, key: asyncssh.SSHKey
    ) -> bool:
        self.fingerprint = key.get_fingerprint("sha256")
        return self._pinned is None or fingerprint_matches(self._pinned, key)


class Panel:
    """Async SSH client for one panel (connects lazily; use as an async context manager)."""

    def __init__(
        self,
        host: str,
        *,
        password: str | None = None,
        private_key: str | None = None,
        passphrase: str | None = None,
        username: str = "root",
        port: int = 22,
        host_key: str | None = None,
        timeout: float = 30.0,
        connect_timeout: float = 15.0,
    ) -> None:
        self.host = host
        self._password = password
        self._private_key = private_key
        self._passphrase = passphrase
        self._username = username
        self._port = port
        self._host_key = host_key
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._conn: asyncssh.SSHClientConnection | None = None
        self._fingerprint: str | None = None

    # -- connection ---------------------------------------------------------------------

    @property
    def host_key_fingerprint(self) -> str | None:
        """SHA256 fingerprint of the host key seen on the last connect attempt."""
        return self._fingerprint

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> str:
        """Open the SSH connection (no-op when already open); returns the host key fingerprint.

        With ``host_key=None`` any key is accepted so a config flow can show and pin the returned
        fingerprint; otherwise a different key raises :class:`HostKeyMismatch` before any
        credentials are sent.
        """
        if self._conn is not None:
            assert self._fingerprint is not None
            return self._fingerprint
        client_keys: list[asyncssh.SSHKey] = []
        if self._private_key:
            try:
                client_keys.append(
                    asyncssh.import_private_key(self._private_key, passphrase=self._passphrase)
                )
            except (asyncssh.KeyImportError, asyncssh.KeyEncryptionError) as err:
                raise AuthFailed(f"cannot use private key: {err}") from err
        client = _PinningClient(self._host_key)
        try:
            self._conn = await asyncssh.connect(
                self.host,
                port=self._port,
                username=self._username,
                password=self._password,
                client_keys=client_keys,
                client_factory=lambda: client,
                # An all-empty tuple trusts nothing itself, so validate_host_public_key decides
                # and asyncssh never reads ~/.ssh/known_hosts or ~/.ssh/config.
                known_hosts=cast(Any, ([], [], [])),
                config=[],
                agent_path=None,
                connect_timeout=self._connect_timeout,
                login_timeout=self._connect_timeout,
                keepalive_interval=30,
            )
        except asyncssh.HostKeyNotVerifiable as err:
            raise HostKeyMismatch(
                f"host key of {self.host} does not match the pinned key", client.fingerprint
            ) from err
        except asyncssh.PermissionDenied as err:
            raise AuthFailed(f"authentication failed for {self._username}@{self.host}") from err
        except (OSError, asyncssh.Error, TimeoutError) as err:
            raise CannotConnect(f"cannot connect to {self.host}: {err}") from err
        self._fingerprint = client.fingerprint
        assert self._fingerprint is not None
        return self._fingerprint

    async def close(self) -> None:
        """Close the connection (safe to call repeatedly)."""
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
            await conn.wait_closed()

    async def _run(
        self,
        command: str,
        *,
        stdin: str | bytes | None = None,
        time_limit: float | None = None,
        check: bool = True,
    ) -> _Result:
        await self.connect()
        conn = self._conn
        assert conn is not None
        # Without input, close stdin right away so nothing waits on it.
        stdin_args: dict[str, Any] = (
            {"stdin": asyncssh.DEVNULL} if stdin is None else {"input": stdin}
        )
        try:
            done = await conn.run(
                command,
                check=False,
                **stdin_args,
                encoding=None,
                timeout=time_limit or self._timeout,
            )
        except TimeoutError as err:
            raise CannotConnect(f"command timed out on {self.host}") from err
        except (OSError, asyncssh.Error) as err:
            self._conn = None
            raise CannotConnect(f"connection to {self.host} lost: {err}") from err
        result = _Result(_decode(done.stdout), _decode(done.stderr), done.exit_status)
        if check and result.exit_status != 0:
            raise CommandFailed(command, result.exit_status, result.stderr)
        return result

    # -- health -------------------------------------------------------------------------

    async def health(self) -> PanelHealth:
        """Uptime, load, memory, VERSION, unit states, backlight; one SSH round trip."""
        result = await self._run(_health_script())
        return parse_health(result.stdout)

    # -- env ----------------------------------------------------------------------------

    async def _read_env(self) -> EnvFile:
        return EnvFile.parse((await self._run(f"cat {ENV_PATH}")).stdout)

    async def get_env(self, *, mask_secrets: bool = True) -> dict[str, str]:
        """Effective ``kiosk.env`` values (last assignment wins); secrets masked by default."""
        env = await self._read_env()
        return env.masked_values() if mask_secrets else env.values()

    async def set_env(
        self,
        changes: Mapping[str, str],
        delete: Iterable[str] = (),
        *,
        restart: bool = False,
    ) -> tuple[str, ...]:
        """Edit ``kiosk.env`` atomically (tmp + mv), keeping ``kiosk.env.bak`` and mode 600.

        Comments and ordering are preserved. Returns the keys whose effective value changed;
        when nothing changed nothing is written and no restart happens. ``restart`` restarts
        ``ha-kiosk.service`` after a write.
        """
        delete = tuple(delete)
        for key in (*changes, *delete):
            validate_key(key)
        for value in changes.values():
            format_value(value)
        old = await self._read_env()
        new = old.apply(changes, delete)
        before, after = old.values(), new.values()
        changed = tuple(
            k for k in dict.fromkeys((*before, *after)) if before.get(k) != after.get(k)
        )
        if not changed:
            return ()
        path = shlex.quote(ENV_PATH)
        script = (
            "set -e\n"
            f"f={path}\n"
            'if [ -f "$f" ]; then cp -p "$f" "$f.bak"; chmod 600 "$f.bak"; fi\n'
            'cat > "$f.tmp"\n'
            'chmod 600 "$f.tmp"\n'
            'mv "$f.tmp" "$f"\n'
        )
        await self._run(script, stdin=new.serialize())
        if restart:
            await self.restart("ha-kiosk")
        return changed

    # -- units --------------------------------------------------------------------------

    @staticmethod
    def _unit(unit: str) -> str:
        name = unit if unit.endswith(".service") else f"{unit}.service"
        if name not in UNITS:
            raise InvalidArgument(f"unit not allowed: {unit!r}")
        return name

    async def restart(self, unit: str) -> None:
        """Restart an allowlisted unit."""
        await self._run(f"systemctl restart {shlex.quote(self._unit(unit))}")

    async def start(self, unit: str) -> None:
        """Start an allowlisted unit."""
        await self._run(f"systemctl start {shlex.quote(self._unit(unit))}")

    async def stop(self, unit: str) -> None:
        """Stop an allowlisted unit."""
        await self._run(f"systemctl stop {shlex.quote(self._unit(unit))}")

    async def logs(self, unit: str, lines: int = 100) -> str:
        """The last ``lines`` journal lines of an allowlisted unit."""
        if not 1 <= lines <= MAX_LOG_LINES:
            raise InvalidArgument(f"lines must be between 1 and {MAX_LOG_LINES}")
        name = shlex.quote(self._unit(unit))
        result = await self._run(f"journalctl -u {name} -n {int(lines)} --no-pager -o short-iso")
        return result.stdout

    async def reboot(self) -> None:
        """Reboot the panel; the connection is closed afterwards."""
        await self._run("nohup sh -c 'sleep 1; systemctl reboot' >/dev/null 2>&1 &")
        await self.close()

    # -- message bus --------------------------------------------------------------------

    async def bus_peripherals(self) -> BusListing:
        """List message-bus peripherals."""
        return bus.parse_listing((await self._run(bus.list_command())).stdout)

    async def bus_get(self, peripheral: str) -> tuple[BusVariable, ...]:
        """Read a peripheral's variables."""
        return bus.parse_variables((await self._run(bus.get_command(peripheral))).stdout)

    async def bus_set(
        self, peripheral: str, values: Mapping[str, str | int | float | bool]
    ) -> tuple[BusVariable, ...]:
        """Write externally settable variables; returns the variables that changed."""
        result = await self._run(bus.set_command(peripheral, values))
        return bus.parse_set_result(result.stdout)

    # -- install ------------------------------------------------------------------------

    async def install(self, bundle: bytes, sha256: str) -> str:
        """Upload a release bundle, verify it and run ``install-local.sh``; returns the log.

        The checksum is verified locally and again on the panel. ``install-local.sh`` runs
        without ``--env``/``--token``, so the panel keeps its current settings and token.
        """
        if not _SHA256_RE.match(sha256):
            raise InvalidArgument("sha256 must be 64 hex characters")
        expected = sha256.lower()
        if hashlib.sha256(bundle).hexdigest() != expected:
            raise ChecksumMismatch("bundle does not match the expected sha256")
        remote = f"{INSTALL_DIR}/bundle.tar.gz"
        await self._run(f"rm -rf {INSTALL_DIR} && mkdir -m 700 {INSTALL_DIR}")
        try:
            await self._upload(bundle, remote)
            check = await self._run(f"sha256sum {remote}")
            remote_sha = check.stdout.split()[0].lower() if check.stdout.split() else ""
            if remote_sha != expected:
                raise ChecksumMismatch("bundle checksum on the panel does not match")
            script = (
                f"cd {INSTALL_DIR} && rm -rf bundle && mkdir bundle"
                " && tar xzf bundle.tar.gz -C bundle"
                " && d=$(find bundle -maxdepth 2 -name install-local.sh | head -n 1)"
                ' && [ -n "$d" ] && sh "$d" 2>&1\n'
                "rc=$?\n"
                f"cd / && rm -rf {INSTALL_DIR}\n"
                "exit $rc\n"
            )
            result = await self._run(script, time_limit=300, check=False)
        except PanelError:
            await self._cleanup_install()
            raise
        if result.exit_status != 0:
            raise CommandFailed(
                "install-local.sh", result.exit_status, result.stdout + result.stderr
            )
        return result.stdout

    async def _cleanup_install(self) -> None:
        try:
            await self._run(f"rm -rf {INSTALL_DIR}")
        except PanelError:
            return

    async def _upload(self, data: bytes, remote: str) -> None:
        """SFTP upload; falls back to ``cat >`` when the panel has no sftp subsystem."""
        conn = self._conn
        assert conn is not None
        try:
            async with conn.start_sftp_client() as sftp, sftp.open(remote, "wb") as handle:
                await handle.write(data)
        except (asyncssh.SFTPError, asyncssh.ChannelOpenError):
            await self._run(f"cat > {shlex.quote(remote)}", stdin=data, time_limit=300)
        except (OSError, asyncssh.Error) as err:
            self._conn = None
            raise CannotConnect(f"upload to {self.host} failed: {err}") from err


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


__all__ = ["INSTALL_DIR", "Panel", "fingerprint_matches", "parse_health"]
