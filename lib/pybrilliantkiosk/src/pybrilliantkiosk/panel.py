"""SSH client for one Brilliant panel."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, cast

import asyncssh

from . import bus
from .bundle import sha256_hex
from .env import (
    KNOWN_KEYS,
    EnvFile,
    drift_keys,
    format_value,
    parse_setup_keys,
    reconcile,
    validate_key,
    validate_setting_key,
)
from .exceptions import (
    AuthFailed,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    HostKeyMismatch,
    InvalidArgument,
    PanelError,
    SetupBusy,
    SetupError,
)
from .models import (
    BACKLIGHT_DIR,
    CONFIG_ENV_PATH,
    DISPLAY_STATE_PATH,
    ENV_PATH,
    OVERRIDE_KEYS,
    OVERRIDES_PATH,
    UNITS,
    VERSION_PATH,
    BusListing,
    BusVariable,
    PanelHealth,
)
from .setup import (
    EXIT_IN_PROGRESS,
    SETUP_PATH,
    SETUP_PYTHON,
    STATE_IN_PROGRESS,
    SetupStatus,
    error_for_exit,
    parse_json_object,
    parse_status,
)

_LOGGER = logging.getLogger(__name__)

#: Prefix of the per-install ``mktemp -d`` directory on the panel (never a fixed, guessable path).
INSTALL_PREFIX = "/tmp/ha-kiosk-install"  # noqa: S108 - path on the panel, not on this host
_INSTALL_DIR_RE = re.compile(r"^/tmp/ha-kiosk-install\.[A-Za-z0-9]{6,}$")
#: The lock ``ha-kiosk-setup`` takes (``LOCK`` in kiosk/ha-kiosk-setup) for every changing command.
SETUP_LOCK_PATH = "/run/ha-kiosk-setup.lock"
#: Exit status of the kiosk.env write script when stdin was empty or did not hash as expected.
_INCOMPLETE_INPUT = 3
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_LOG_LINES = 5000
#: Section markers of the combined settings read (see ``_STATE_COMMAND``); one full line each.
_BEGIN = "#--kl:begin--"
_SECTION_RE = re.compile(r"^#--kl:(config|running|overrides)(?::(present|absent))?--\n", re.M)
#: Reads both copies of kiosk.env and the runtime overrides in one round trip. Everything before
#: the begin marker (a login banner, profile output, ...) is ignored; a file's text is followed
#: by one ``echo`` newline that the parser removes again, so nothing is added or lost.
_STATE_COMMAND = (
    f"echo '{_BEGIN}'; "
    f"if [ -f {CONFIG_ENV_PATH} ]; then echo '#--kl:config:present--'; cat {CONFIG_ENV_PATH}; "
    "echo; else echo '#--kl:config:absent--'; fi; "
    f"if [ -f {ENV_PATH} ]; then echo '#--kl:running:present--'; cat {ENV_PATH}; "
    "echo; else echo '#--kl:running:absent--'; fi; "
    f"echo '#--kl:overrides--'; cat {OVERRIDES_PATH} 2>/dev/null; true"
)
SETUP_COMMANDS = ("reapply", "enable", "disable", "rollback", "uninstall", "purge")
WAIT_TIMEOUT = 100
WAIT_ROUNDS = 8


#: Runs ``sys.argv[1]`` with ``sh -c`` while holding the setup tool's flock; exit 8 when busy.
#: Python's fcntl is used because the panel's BusyBox may lack a ``flock`` applet.
_LOCKED_RUNNER = (
    "import fcntl, os, subprocess, sys\n"
    f"fd = os.open({SETUP_LOCK_PATH!r}, os.O_CREAT | os.O_RDWR, 0o600)\n"
    "try:\n"
    "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except OSError:\n"
    "    sys.exit(8)\n"
    "sys.exit(subprocess.call(['sh', '-c', sys.argv[1]]))\n"
)


@dataclass(frozen=True, slots=True)
class _Result:
    stdout: str
    stderr: str
    exit_status: int | None


@dataclass(frozen=True, slots=True)
class _EnvState:
    """Both copies of kiosk.env (None when a copy does not exist) and the runtime overrides."""

    saved: EnvFile | None
    running: EnvFile | None
    overrides: dict[str, str]

    @property
    def drift(self) -> tuple[str, ...]:
        if self.saved is None or self.running is None:
            return ()
        return drift_keys(self.saved, self.running)


def _parse_state(stdout: str) -> _EnvState:
    _, sep, rest = stdout.partition(f"{_BEGIN}\n")
    if not sep:
        raise CommandFailed("read kiosk.env", None, "unexpected output while reading settings")
    parts = _SECTION_RE.split(rest)  # [pre, name, state, body, name, state, body, ...]
    files: dict[str, EnvFile | None] = {}
    over: dict[str, str] = {}
    for name, state, body in zip(parts[1::3], parts[2::3], parts[3::3], strict=True):
        if name == "overrides":
            over = EnvFile.parse(body).values()
        else:
            files[name] = EnvFile.parse(body.removesuffix("\n")) if state == "present" else None
    return _EnvState(
        files.get("config"),
        files.get("running"),
        {k: v for k, v in over.items() if k in OVERRIDE_KEYS},
    )


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


def _write_env_script(purge: tuple[str, ...], write_base: bool, sha256: str = "") -> str:
    """Shell script (run under the lock) that installs stdin as kiosk.env and purges overrides.

    The new file is installed only when what arrived on stdin hashes to ``sha256``, so a dropped
    or empty stdin can never replace the settings (it once emptied both copies on a panel).
    """
    script = "set -e\n"
    if write_base:
        if not _SHA256_RE.match(sha256):
            raise InvalidArgument("the sha256 of the new kiosk.env is required")
        script += (
            "umask 077\n"
            f"t={shlex.quote(ENV_PATH)}.tmp.$$\n"
            'cat > "$t"\n'
            'if [ ! -s "$t" ] || '
            f'[ "$(sha256sum "$t" | cut -d" " -f1)" != {sha256.lower()} ]; then\n'
            '  rm -f "$t"; echo "kiosk.env: incomplete input, nothing changed" >&2; '
            f"exit {_INCOMPLETE_INPUT}\n"
            "fi\n"
            f"for f in {CONFIG_ENV_PATH} {ENV_PATH}; do\n"
            '  [ -d "$(dirname "$f")" ] || continue\n'
            '  if [ -f "$f" ]; then cp -p "$f" "$f.bak"; chmod 600 "$f.bak"; fi\n'
            '  cp "$t" "$f.new"; chmod 600 "$f.new"; mv "$f.new" "$f"\n'
            "done\n"
            'rm -f "$t"\n'
        )
    if purge:
        expr = " ".join(f"-e '/^{key}=/d'" for key in purge)
        script += (
            "umask 022\n"
            f"o={OVERRIDES_PATH}\n"
            'if [ -f "$o" ]; then\n'
            f'  sed {expr} "$o" > "$o.tmp.$$" && mv "$o.tmp.$$" "$o"\n'
            "fi\n"
        )
    return script


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


def _fingerprints_match(pinned: str, sha256: str, md5: str) -> bool:
    candidates = {_normalise_fp(token) for token in pinned.split()}
    sha, md = _normalise_fp(sha256), _normalise_fp(md5)
    return sha in candidates or md in candidates or md.removeprefix("md5:") in candidates


def fingerprint_matches(pinned: str, key: asyncssh.SSHKey) -> bool:
    """Whether a pinned fingerprint matches ``key``.

    ``pinned`` may be an OpenSSH ``SHA256:...`` or ``MD5:aa:bb:...`` fingerprint (as printed by
    ssh-keygen or plink), alone or embedded in text such as ``ssh-ed25519 255 SHA256:...``.
    """
    return _fingerprints_match(pinned, key.get_fingerprint("sha256"), key.get_fingerprint("md5"))


#: Host-key algorithms in OpenSSH's / PuTTY's order of preference. Panels offer ED25519, ECDSA and
#: RSA keys; without a fixed order the negotiated key (and so its fingerprint) can differ from the
#: one ssh-keyscan, ssh or plink show and pin, which made a correctly pinned panel look tampered.
HOST_KEY_ALGS: tuple[str, ...] = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "rsa-sha2-512",
    "rsa-sha2-256",
    "ssh-rsa",
)


_ALG_LABELS = (
    ("ssh-ed25519", "ED25519"),
    ("ecdsa-", "ECDSA"),
    ("ssh-rsa", "RSA"),
    ("rsa-sha2", "RSA"),
    ("ssh-dss", "DSA"),
)


@dataclass(frozen=True, slots=True)
class HostKeyInfo:
    """A panel's SSH host key as seen before any credentials were sent."""

    sha256: str
    md5: str
    #: SSH name of the key type, e.g. ``ssh-ed25519`` (empty when unknown).
    algorithm: str = ""

    @property
    def label(self) -> str:
        """Short key type name as ssh-keygen prints it: ED25519, ECDSA, RSA, ..."""
        for prefix, name in _ALG_LABELS:
            if self.algorithm.startswith(prefix):
                return name
        return self.algorithm.upper()

    @property
    def display(self) -> str:
        """``ED25519 SHA256:...`` (just the fingerprint when the key type is unknown)."""
        return f"{self.label} {self.sha256}" if self.algorithm else self.sha256

    def matches(self, pinned: str) -> bool:
        """Whether ``pinned`` (SHA256 or MD5 fingerprint, see :func:`fingerprint_matches`) fits."""
        return _fingerprints_match(pinned, self.sha256, self.md5)


class _PinningClient(asyncssh.SSHClient):
    """Validates the host key before authentication and records its fingerprint.

    With ``scan_only`` the key is recorded and then *rejected*, so the connection ends before
    any credential is sent (used to show a fingerprint for trust-on-first-use confirmation).
    """

    def __init__(self, pinned: str | None, *, scan_only: bool = False) -> None:
        self._pinned = pinned
        self._scan_only = scan_only
        self.fingerprint: str | None = None
        self.info: HostKeyInfo | None = None

    def validate_host_public_key(
        self, host: str, addr: str, port: int, key: asyncssh.SSHKey
    ) -> bool:
        self.fingerprint = key.get_fingerprint("sha256")
        self.info = HostKeyInfo(
            self.fingerprint, key.get_fingerprint("md5"), key.algorithm.decode("ascii", "replace")
        )
        if self._scan_only:
            return False
        return self._pinned is None or fingerprint_matches(self._pinned, key)


async def fetch_host_key(
    host: str, *, port: int = 22, connect_timeout: float = 15.0
) -> HostKeyInfo:
    """Read a panel's SSH host key without authenticating (no password or key is sent).

    The key exchange completes, the key is recorded and the connection is then refused, so a
    password typed into a config flow only ever goes to a host whose fingerprint the user
    confirmed (trust on first use). Raises :class:`CannotConnect` if the host is unreachable.
    """
    client = _PinningClient(None, scan_only=True)
    try:
        conn = await asyncssh.connect(
            host,
            port=port,
            client_factory=lambda: client,
            known_hosts=cast(Any, ([], [], [])),
            server_host_key_algs=list(HOST_KEY_ALGS),
            config=[],
            agent_path=None,
            client_keys=[],
            connect_timeout=connect_timeout,
            login_timeout=connect_timeout,
        )
    except asyncssh.HostKeyNotVerifiable:
        if client.info is None:  # pragma: no cover - defensive: rejected before a key was seen
            raise CannotConnect(f"{host} did not present an SSH host key") from None
        return client.info
    except (OSError, asyncssh.Error, TimeoutError) as err:
        raise CannotConnect(f"cannot connect to {host}: {err}") from err
    conn.close()  # pragma: no cover - the scan always rejects the key
    raise CannotConnect(f"unexpected SSH session to {host}")  # pragma: no cover


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
        #: (connection, allowed setting names) of the panel's setup tool; see ``_allowed_keys``.
        self._keys: tuple[asyncssh.SSHClientConnection | None, frozenset[str]] | None = None
        #: Keys the last :meth:`set_env` / :meth:`reconcile_env` took from the running copy.
        self.last_reconciled: tuple[str, ...] = ()
        self._fingerprint: str | None = None
        self._connect_lock = asyncio.Lock()
        #: Serialises install / setup commands / set_env of this panel.
        self._op_lock = asyncio.Lock()

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
        credentials are sent. Concurrent callers share one connection attempt.
        """
        async with self._connect_lock:
            if self._conn is not None:
                assert self._fingerprint is not None
                return self._fingerprint
            return await self._open()

    async def _open(self) -> str:
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
                server_host_key_algs=list(HOST_KEY_ALGS),
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
        async with self._connect_lock:
            conn = self._conn
            if conn is not None:
                await self._drop(conn)

    async def _drop(self, conn: asyncssh.SSHClientConnection) -> None:
        """Forget ``conn`` (if still current) and close it, so the socket is not leaked."""
        if self._conn is conn:
            self._conn = None
        conn.close()
        with contextlib.suppress(OSError, asyncssh.Error):
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
        if conn is None:  # dropped by a concurrent failure between connect() and here
            raise CannotConnect(f"connection to {self.host} lost")
        # Without input, close stdin right away so nothing waits on it. Empty input counts as
        # none: asyncssh ignores ``input=b""`` (falsy) and would leave stdin open until timeout.
        stdin_args: dict[str, Any] = (
            {"input": stdin.encode() if isinstance(stdin, str) else stdin}
            # The session is binary (encoding=None), so text must be encoded here.
            if stdin
            else {"stdin": asyncssh.DEVNULL}
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
            await self._drop(conn)
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

    async def _read_state(self) -> _EnvState:
        """Both copies of kiosk.env and the runtime overrides (one round trip)."""
        return _parse_state((await self._run(_STATE_COMMAND)).stdout)

    @staticmethod
    def _base(state: _EnvState) -> tuple[EnvFile, tuple[str, ...], tuple[str, ...]]:
        """The file to edit: both copies reconciled (see :func:`pybrilliantkiosk.env.reconcile`).

        With one copy only, that copy; with none, an empty file. Returns ``(base, adopted,
        skipped)`` like ``reconcile``.
        """
        if state.saved is not None and state.running is not None:
            return reconcile(state.saved, state.running)
        return state.saved or state.running or EnvFile(), (), ()

    async def get_env(self, *, mask_secrets: bool = True) -> dict[str, str]:
        """Effective ``kiosk.env`` values (last assignment wins); secrets masked by default.

        Both copies are read; where they differ the running one (``/etc/ha-kiosk/kiosk.env``,
        what the panel uses) wins, as in :meth:`set_env`.
        """
        env, _, _ = self._base(await self._read_state())
        return env.masked_values() if mask_secrets else env.values()

    async def env_drift(self) -> tuple[str, ...]:
        """Keys whose value differs between the running and the saved copy of ``kiosk.env``.

        ``ha-kiosk-setup`` (install, reapply) rebuilds ``/etc/ha-kiosk/kiosk.env`` from the saved
        copy in ``/var/lib/ha-kiosk/config``, so anything applied only to the running copy by hand
        is lost then. Empty when the copies agree or when one of them does not exist. Fix with
        :meth:`reconcile_env`.
        """
        return (await self._read_state()).drift

    async def reconcile_env(self) -> tuple[str, ...]:
        """Write the reconciled settings to both copies; returns the keys that had drifted.

        Raises :class:`InvalidArgument` (nothing written) if a drifted value cannot be written
        safely, because the copies would still differ afterwards.
        """
        async with self._op_lock:
            await self._ensure_idle()
            state = await self._read_state()
            _, _, skipped = self._base(state)
            if skipped:
                raise InvalidArgument(f"cannot reconcile unsafe values for {', '.join(skipped)}")
            await self._write(state, {}, ())
            return state.drift

    async def _allowed_keys(self) -> frozenset[str]:
        """Settings the panel's installed ``ha-kiosk-setup`` accepts (cached per connection).

        Parsed from the tool itself, so a nightly with new settings is not held back by this
        library's list; the bundled list is the fallback when the tool cannot be read or parsed.
        """
        await self.connect()
        conn = self._conn
        if self._keys is not None and self._keys[0] is conn:
            return self._keys[1]
        result = await self._run(f"cat {SETUP_PATH} 2>/dev/null", check=False)
        parsed = parse_setup_keys(result.stdout) if result.exit_status == 0 else None
        keys = parsed if parsed is not None else KNOWN_KEYS
        self._keys = (conn, keys)
        return keys

    async def _ensure_idle(self) -> None:
        """Refuse to touch the panel's settings or install while ``ha-kiosk-setup`` is working."""
        try:
            status = await self.setup_status()
        except SetupBusy:
            raise
        except SetupError:
            return  # a broken or missing tool is not "busy"; the flock still guards the write
        if status.state == STATE_IN_PROGRESS:
            raise SetupBusy("ha-kiosk-setup is running a job on the panel; try again when it ends")

    async def set_env(
        self,
        changes: Mapping[str, str],
        delete: Iterable[str] = (),
        *,
        restart: bool = False,
    ) -> tuple[str, ...]:
        """Merge settings into ``kiosk.env`` (like ``--config-merge``), atomically.

        ``ha-kiosk-setup`` v1.4.0 has no merge command, so this edits both copies it keeps
        (``/var/lib/ha-kiosk/config/kiosk.env`` and the running ``/etc/ha-kiosk/kiosk.env``),
        validating keys against the allowlist of the panel's installed ``ha-kiosk-setup`` (the
        bundled list when it cannot be read) and values against its charset rules; values are
        written ``KEY="value"`` like setup does, because the files are sourced by root shell
        scripts. Comments and ordering are preserved.

        The copies can diverge (a setting applied by hand to the running one only). The edit is
        made on both reconciled: the saved copy plus any key only the running copy has, with the
        running values winning (they are what the panel uses). The result goes to both copies,
        a warning is logged and :attr:`last_reconciled` lists the keys taken from the running
        copy, so a later ``ha-kiosk-setup reapply`` cannot drop them.

        Runtime overrides (``overrides.env``, written by ``ha-kiosk-mqtt.py``) win over
        ``kiosk.env``, so the change is evaluated against the *effective* view (overrides merged
        in), and every override for a key passed in ``changes`` or ``delete`` is purged, as
        ``--config`` does. Returns the keys whose effective value changed; when neither the
        effective values nor the overrides need to change nothing is written. ``restart``
        restarts ``ha-kiosk.service`` after a write.

        The panel-side script holds the ``ha-kiosk-setup`` flock, so it cannot interleave with a
        setup run (:class:`SetupBusy`); calls on one :class:`Panel` are serialised. KNOWN RACE:
        ``ha-kiosk-mqtt.py`` rewrites ``overrides.env`` (read, modify, ``os.replace``) without
        that lock; a switch toggled in Home Assistant in the same few milliseconds as the purge
        can be lost (the purge itself is an atomic rename, so the file is never torn).
        """
        delete = tuple(delete)
        for key in (*changes, *delete):
            validate_key(key)
        for value in changes.values():
            format_value(value)
        async with self._op_lock:
            await self._ensure_idle()
            state = await self._read_state()
            if changes:
                allowed = await self._allowed_keys()
                for key in changes:
                    validate_setting_key(key, allowed)
            changed = await self._write(state, changes, delete)
            if restart and changed:
                await self.restart("ha-kiosk")
            return changed

    async def _write(
        self, state: _EnvState, changes: Mapping[str, str], delete: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Apply the edit to the reconciled base and write it (caller holds ``_op_lock``)."""
        old, adopted, skipped = self._base(state)
        self.last_reconciled = adopted
        if adopted or skipped:
            parts = []
            if adopted:
                parts.append(f"kept the running value of {', '.join(adopted)}")
            if skipped:
                parts.append(f"left out unsafe values of {', '.join(skipped)}")
            _LOGGER.warning(
                "kiosk.env on %s: the running and the saved copy differed; %s",
                self.host,
                "; ".join(parts),
            )
        overrides = state.overrides
        new = old.apply(changes, delete)
        passed = tuple(dict.fromkeys((*changes, *delete)))
        purge = tuple(k for k in passed if k in overrides)
        # What the panel uses now: the running copy (the saved one when it is the only copy).
        current = state.running if state.running is not None else state.saved
        before = {**(current.values() if current else {}), **overrides}
        after = {**new.values(), **{k: v for k, v in overrides.items() if k not in purge}}
        changed = tuple(
            k for k in dict.fromkeys((*before, *after)) if before.get(k) != after.get(k)
        )
        copies = [c for c in (state.saved, state.running) if c is not None]
        write_base = any(c.values() != new.values() for c in copies) or (
            not copies and bool(new.values())
        )
        if not write_base and not purge:
            return ()
        text = new.serialize()
        digest = sha256_hex(text.encode()) if write_base else ""
        await self._locked(_write_env_script(purge, write_base, digest), stdin=text)
        return changed

    async def get_settings(self, *, mask_secrets: bool = True) -> dict[str, str]:
        """Effective settings: ``kiosk.env`` with the runtime ``overrides.env`` applied on top.

        Missing files count as empty, so an uninstalled panel returns ``{}``.
        """
        state = await self._read_state()
        base, _, _ = self._base(state)
        values = base.masked_values() if mask_secrets else base.values()
        return {**values, **state.overrides}

    async def _locked(self, script: str, *, stdin: str | None = None) -> _Result:
        """Run a shell script on the panel under ``ha-kiosk-setup``'s flock."""
        command = f"{SETUP_PYTHON} -c {shlex.quote(_LOCKED_RUNNER)} {shlex.quote(script)}"
        result = await self._run(command, stdin=stdin, check=False)
        if result.exit_status == SetupBusy.code:
            raise SetupBusy("ha-kiosk-setup holds the panel lock; try again when it ends")
        if result.exit_status == _INCOMPLETE_INPUT:
            raise ChecksumMismatch(
                "the new kiosk.env did not arrive intact on the panel; nothing was changed"
            )
        if result.exit_status != 0:
            raise CommandFailed(script, result.exit_status, result.stderr)
        return result

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

    # -- setup tool ---------------------------------------------------------------------

    async def _setup_run(
        self, args: str, *, tool: str = SETUP_PATH, time_limit: float | None = None
    ) -> _Result:
        return await self._run(f"{SETUP_PYTHON} {tool} {args}", time_limit=time_limit, check=False)

    async def setup_status(self) -> SetupStatus:
        """``ha-kiosk-setup status``; a panel without the tool reports ``not_installed``."""
        script = (
            f"if [ -f {SETUP_PATH} ]; then {SETUP_PYTHON} {SETUP_PATH} status; "
            'else echo \'{"state": "not_installed"}\'; fi'
        )
        result = await self._run(script, check=False)
        if result.exit_status != 0:
            raise error_for_exit(result.exit_status or 1, result.stdout)
        return parse_status(parse_json_object(result.stdout))

    async def setup_diagnostics(self) -> dict[str, Any]:
        """``ha-kiosk-setup diagnostics`` (status plus recent logs, secrets redacted)."""
        result = await self._setup_run("diagnostics")
        if result.exit_status != 0:
            raise error_for_exit(result.exit_status or 1, result.stdout)
        return parse_json_object(result.stdout)

    async def check_env(self, text: str) -> tuple[str, ...]:
        """Validate settings text with the panel's own ``check-env``; returns the errors."""
        result = await self._run(
            f"{SETUP_PYTHON} {SETUP_PATH} check-env -", stdin=text, check=False
        )
        if result.exit_status not in (0, 4):
            raise error_for_exit(result.exit_status or 1, result.stdout)
        errors = parse_json_object(result.stdout).get("errors")
        return tuple(str(e) for e in errors) if isinstance(errors, list) else ()

    async def setup_command(self, command: str) -> SetupStatus:
        """Run reapply, enable, disable, rollback, uninstall or purge, detached, and wait."""
        if command not in SETUP_COMMANDS:
            raise InvalidArgument(f"setup command not allowed: {command!r}")
        async with self._op_lock:
            await self._ensure_idle()
            started = await self._setup_run(f"{command} --detach")
            if started.exit_status != 0:
                raise error_for_exit(started.exit_status or 1, started.stdout)
            return await self._setup_wait(SETUP_PATH)

    async def _setup_wait(self, tool: str) -> SetupStatus:
        """Poll ``wait --timeout 100`` until the detached job ends (exit != 9)."""
        output = ""
        for _ in range(WAIT_ROUNDS):
            result = await self._setup_run(
                f"wait --timeout {WAIT_TIMEOUT}", tool=tool, time_limit=WAIT_TIMEOUT + 30
            )
            output = result.stdout
            if result.exit_status == EXIT_IN_PROGRESS:
                continue
            if result.exit_status != 0:
                raise error_for_exit(result.exit_status or 1, output)
            return parse_status(parse_json_object(output))
        raise SetupError("ha-kiosk-setup job is still running", EXIT_IN_PROGRESS)

    # -- install ------------------------------------------------------------------------

    async def install(
        self,
        bundle: bytes,
        sha256: str,
        *,
        env: str | None = None,
        token: str | None = None,
    ) -> SetupStatus:
        """Install or update the kiosk with ``ha-kiosk-setup`` (docs/INTEGRATION.md section 0).

        The bundle is verified here and on the panel, the tool is extracted from it and run with
        ``--detach``; the call polls ``wait`` until the job ends. ``env`` / ``token`` text is only
        needed for a first install (the tool keeps the panel's settings and token otherwise).
        Raises a :class:`SetupError` subclass for the tool's exit codes.
        """
        if not _SHA256_RE.match(sha256):
            raise InvalidArgument("sha256 must be 64 hex characters")
        expected = sha256.lower()
        if await asyncio.to_thread(sha256_hex, bundle) != expected:
            raise ChecksumMismatch("bundle does not match the expected sha256")
        async with self._op_lock:
            await self._ensure_idle()
            made = await self._run(
                f"d=$(mktemp -d {INSTALL_PREFIX}.XXXXXXXX) && chmod 700 \"$d\" && echo \"$d\""
            )
            workdir = made.stdout.strip()
            if not _INSTALL_DIR_RE.match(workdir):
                raise CommandFailed("mktemp -d", made.exit_status, f"unexpected output {workdir!r}")
            try:
                return await self._install_in(workdir, bundle, expected, env, token)
            finally:
                await self._cleanup_install(workdir)

    async def _install_in(
        self, workdir: str, bundle: bytes, expected: str, env: str | None, token: str | None
    ) -> SetupStatus:
        remote = f"{workdir}/bundle.tar.gz"
        tool = f"{workdir}/ha-kiosk-setup"
        await self._upload(bundle, remote)
        check = await self._run(f"sha256sum {remote}")
        remote_sha = check.stdout.split()[0].lower() if check.stdout.split() else ""
        if remote_sha != expected:
            raise ChecksumMismatch("bundle checksum on the panel does not match")
        await self._run(
            f"cd {workdir} && m=$(tar -tzf bundle.tar.gz | grep -E '^[^/]+/ha-kiosk-setup$'"
            f' | head -n 1) && [ -n "$m" ] && tar -xzOf bundle.tar.gz "$m" > ha-kiosk-setup'
        )
        args = f"install --bundle {remote} --sha256 {expected} --size {len(bundle)} --detach"
        for name, text, flag in (("env", env, "--env"), ("token", token, "--token")):
            if text is not None:
                path = f"{workdir}/{name}"
                await self._run(f"umask 077; cat > {path}", stdin=text)
                args += f" {flag} {path}"
        started = await self._setup_run(args, tool=tool)
        if started.exit_status != 0:
            raise error_for_exit(started.exit_status or 1, started.stdout)
        return await self._setup_wait(tool)

    async def _cleanup_install(self, workdir: str) -> None:
        try:
            await self._run(f"rm -rf {workdir}")
        except PanelError:
            return

    async def _upload(self, data: bytes, remote: str) -> None:
        """SFTP upload; falls back to ``cat >`` when the panel has no sftp subsystem."""
        conn = self._conn
        if conn is None:
            raise CannotConnect(f"connection to {self.host} lost")
        try:
            async with conn.start_sftp_client() as sftp, sftp.open(remote, "wb") as handle:
                await handle.write(data)
        except (asyncssh.SFTPError, asyncssh.ChannelOpenError):
            await self._run(f"cat > {shlex.quote(remote)}", stdin=data, time_limit=300)
        except (OSError, asyncssh.Error) as err:
            await self._drop(conn)
            raise CannotConnect(f"upload to {self.host} failed: {err}") from err


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


__all__ = [
    "INSTALL_PREFIX",
    "HostKeyInfo",
    "Panel",
    "fetch_host_key",
    "fingerprint_matches",
    "parse_health",
]
