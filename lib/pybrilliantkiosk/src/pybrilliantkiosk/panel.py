"""SSH client for one Brilliant panel."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import secrets
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
    ConcurrentModification,
    DeviceIdUnavailable,
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
    PanelSnapshot,
)
from .setup import (
    ETC_DIR,
    EXIT_IN_PROGRESS,
    HEARTBEAT_PATH,
    JOB_UNIT,
    RESULT_PATH,
    SECRET_PATHS,
    SETUP_PATH,
    SETUP_PYTHON,
    STATE_IN_PROGRESS,
    VAR_DIR,
    SetupStatus,
    error_for_exit,
    parse_json_object,
    parse_status,
    status_from_files,
)

_LOGGER = logging.getLogger(__name__)

#: Prefix of the per-install ``mktemp -d`` directory on the panel (never a fixed, guessable path).
INSTALL_PREFIX = "/tmp/ha-kiosk-install"  # noqa: S108 - path on the panel, not on this host
_INSTALL_DIR_RE = re.compile(r"^/tmp/ha-kiosk-install\.[A-Za-z0-9]{6,}$")
#: The lock ``ha-kiosk-setup`` takes (``LOCK`` in kiosk/ha-kiosk-setup) for every changing command.
SETUP_LOCK_PATH = "/run/ha-kiosk-setup.lock"
#: Exit status of the kiosk.env write script when stdin was empty or did not hash as expected.
_INCOMPLETE_INPUT = 3
#: Exit status of the write script when kiosk.env changed on the panel after it was read.
_CHANGED_ON_PANEL = 10
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_LOG_LINES = 5000
#: Section markers of the combined read (see :func:`_poll_command`), one full line each:
#: ``#--kl-<nonce>:<name>[:<arg>...]--``. The nonce is random and new for every command, so no
#: file content (kiosk.env is user-editable) can contain a marker by accident or on purpose.
#: Everything before the begin marker (a login banner, profile output, ...) is ignored; a file's
#: text is followed by one ``echo`` newline that the parser removes again, so nothing is added or
#: lost.


def _new_nonce() -> str:
    return secrets.token_hex(8)


def _marker(nonce: str, name: str) -> str:
    return f"#--kl-{nonce}:{name}"


def _begin(nonce: str) -> str:
    return f"{_marker(nonce, 'begin')}--"


def _file_section(nonce: str, name: str, path: str) -> str:
    """Print one kiosk.env copy as a section: ``present:<sha256>`` plus its text, or ``absent``.

    The hash is taken *before* the text is read. If the file changes in between, the hash is of
    older content than what was parsed, so the write that follows sees a mismatch and aborts
    (:class:`ConcurrentModification`) instead of building on a stale view.
    """
    mark = _marker(nonce, name)
    return (
        f"if [ -f {path} ]; then "
        f'echo "{mark}:present:$(sha256sum {path} | cut -d" " -f1)--"; cat {path}; echo; '
        f"else echo '{mark}:absent--'; fi; "
    )


def _state_command(nonce: str) -> str:
    """Reads both copies of kiosk.env (with their hashes) and the runtime overrides."""
    return (
        f"echo '{_begin(nonce)}'; "
        + _file_section(nonce, "config", CONFIG_ENV_PATH)
        + _file_section(nonce, "running", ENV_PATH)
        + f"echo '{_marker(nonce, 'overrides')}--'; cat {OVERRIDES_PATH} 2>/dev/null; echo; true"
    )


SETUP_COMMANDS = ("reapply", "enable", "disable", "rollback", "uninstall", "purge")
WAIT_TIMEOUT = 100
#: Worst case of one detached job, from the timeouts in ``kiosk/ha-kiosk-setup``: activating the
#: new release (``install-local.sh`` up to 400 s, then 60 s for the UI to report in), activating
#: the previous one after a failure (the same), and restoring the stock UI (400 s). The contract
#: (docs/INTEGRATION.md section 3) quotes about 6.5 minutes; the tool's own limits add up to more,
#: so the wait is sized from them, plus a margin for the verify and stage steps and a slow panel.
SETUP_JOB_WORST_CASE_SECS = 2 * (400 + 60) + 400
SETUP_JOB_MARGIN_SECS = 180
WAIT_ROUNDS = math.ceil((SETUP_JOB_WORST_CASE_SECS + SETUP_JOB_MARGIN_SECS) / WAIT_TIMEOUT)


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


@dataclass(slots=True)
class _Job:
    """Whether a detached ``ha-kiosk-setup`` job started by this call may still be running."""

    running: bool = False


@dataclass(frozen=True, slots=True)
class _Result:
    stdout: str
    stderr: str
    exit_status: int | None


@dataclass(frozen=True, slots=True)
class _EnvState:
    """Both copies of kiosk.env (None when a copy does not exist) and the runtime overrides.

    ``config_sha`` / ``running_sha`` are the SHA-256 of each copy as it was read (None when the
    copy does not exist); the write script refuses to run if the live file no longer has it.
    """

    saved: EnvFile | None
    running: EnvFile | None
    overrides: dict[str, str]
    config_sha: str | None = None
    running_sha: str | None = None

    def base(self) -> tuple[EnvFile, tuple[str, ...], tuple[str, ...]]:
        """The file to edit: both copies reconciled (see :func:`pybrilliantkiosk.env.reconcile`).

        With one copy only, that copy; with none, an empty file. Returns ``(base, adopted,
        skipped)`` like ``reconcile``.
        """
        if self.saved is not None and self.running is not None:
            return reconcile(self.saved, self.running)
        return self.saved or self.running or EnvFile(), (), ()

    @property
    def drift(self) -> tuple[str, ...]:
        if self.saved is None or self.running is None:
            return ()
        return drift_keys(self.saved, self.running)

    @property
    def unsafe(self) -> tuple[str, ...]:
        """Running values that cannot be written safely (``reconcile`` would skip them)."""
        return self.base()[2]

    def settings(self, *, mask_secrets: bool) -> dict[str, str]:
        """Effective settings: the reconciled kiosk.env with the overrides applied on top."""
        base = self.base()[0]
        values = base.masked_values() if mask_secrets else base.values()
        return {**values, **self.overrides}


@dataclass(frozen=True, slots=True)
class _Section:
    args: tuple[str, ...]
    body: str


def _split_sections(stdout: str, nonce: str) -> dict[str, _Section]:
    """The marked sections after the begin marker (a missing marker means garbled output)."""
    _, sep, rest = stdout.partition(f"{_begin(nonce)}\n")
    if not sep:
        raise CommandFailed("read the panel", None, "unexpected output while reading the panel")
    section_re = re.compile(rf"^#--kl-{nonce}:([a-z]+)((?::[A-Za-z0-9]+)*)--\n", re.M)
    parts = section_re.split(rest)  # [pre, name, args, body, name, args, body, ...]
    return {
        name: _Section(tuple(args.split(":")[1:]), body)
        for name, args, body in zip(parts[1::3], parts[2::3], parts[3::3], strict=True)
    }


def _state_from(sections: Mapping[str, _Section]) -> _EnvState:
    files: dict[str, tuple[EnvFile | None, str | None]] = {}
    for name in ("config", "running"):
        section = sections.get(name)
        if section is None or section.args[:1] != ("present",):
            files[name] = (None, None)
            continue
        digest = section.args[1] if len(section.args) > 1 else ""
        if not _SHA256_RE.match(digest):
            raise CommandFailed("read kiosk.env", None, f"no hash for the {name} copy")
        files[name] = (EnvFile.parse(section.body.removesuffix("\n")), digest.lower())
    over = EnvFile.parse(sections["overrides"].body).values() if "overrides" in sections else {}
    return _EnvState(
        files["config"][0],
        files["running"][0],
        {k: v for k, v in over.items() if k in OVERRIDE_KEYS},
        files["config"][1],
        files["running"][1],
    )


def _parse_state(stdout: str, nonce: str) -> _EnvState:
    return _state_from(_split_sections(stdout, nonce))


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


def _setup_script(nonce: str) -> str:
    """The files ``ha-kiosk-setup status`` reads, as three sections; no Python is started.

    One line per fact (``first line of the file``, so a file cannot add facts of its own), then the
    two JSON files as they are. :func:`pybrilliantkiosk.setup.status_from_files` turns them into
    the same :class:`SetupStatus` the tool's ``status`` gives.
    """
    first = "head -n 1 {} 2>/dev/null"
    lines = [
        f"echo '{_marker(nonce, 'setup')}--'",
        f"if [ -f {SETUP_PATH} ]; then echo tool=present; else echo tool=absent; fi",
        f'echo "current=$({first.format(f"{VAR_DIR}/current")})"',
        f'echo "state=$({first.format(f"{VAR_DIR}/state")})"',
        f'echo "previous=$({first.format(f"{VAR_DIR}/previous")})"',
        f'echo "mqtt_base=$({first.format(f"{VAR_DIR}/mqtt_base")})"',
        f'echo "qml=$([ -f {ETC_DIR}/native/main.qml ] && echo 1)"',
        f'echo "job=$(systemctl is-active {JOB_UNIT} 2>/dev/null)"',
        f'echo "firmware=$({first.format("/etc/brilliant_release")})"',
        f'echo "os_release=$({first.format("/etc/os-release")})"',
        'echo "now=$(date +%s 2>/dev/null)"',
        'echo "secrets=$(for p in '
        + " ".join(SECRET_PATHS)
        + "; do [ -e $p ] && printf '%s ' $p; done)\"",
    ]
    for name, path in (("heartbeat", HEARTBEAT_PATH), ("result", RESULT_PATH)):
        lines.append(f"echo '{_marker(nonce, name)}--'; cat {path} 2>/dev/null; echo")
    return "\n".join(lines)


def _poll_command(nonce: str) -> str:
    """Health, both kiosk.env copies, overrides and setup state in ONE SSH command.

    One exec per poll instead of four, and nothing that starts a Python interpreter.
    """
    return (
        f"echo '{_begin(nonce)}'; echo '{_marker(nonce, 'health')}--'\n"
        + _health_script()
        + "\n"
        + _state_command(nonce).removeprefix(f"echo '{_begin(nonce)}'; ")
        + "\n"
        + _setup_script(nonce)
    )


def _facts(body: str) -> dict[str, str]:
    """``key=value`` lines of the setup section (the first occurrence of a key counts)."""
    facts: dict[str, str] = {}
    for line in body.splitlines():
        key, sep, value = line.partition("=")
        if sep and key not in facts:
            facts[key] = value.strip()
    return facts


def _write_env_script(
    purge: tuple[str, ...],
    write_base: bool,
    sha256: str = "",
    expected: tuple[str | None, str | None] = (None, None),
) -> str:
    """Shell script (run under the lock) that installs stdin as kiosk.env and purges overrides.

    The new file is installed only when what arrived on stdin hashes to ``sha256``, so a dropped
    or empty stdin can never replace the settings (it once emptied both copies on a panel).
    ``expected`` is the hash of the saved and the running copy as they were read (None: the copy
    did not exist); if either live file differs now, someone else changed it and the script
    exits with status 10 without touching anything.
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
            "changed() {\n"
            '  rm -f "$t"; echo "kiosk.env changed on the panel meanwhile, nothing changed" >&2; '
            f"exit {_CHANGED_ON_PANEL}\n"
            "}\n"
        )
        for path, digest in zip((CONFIG_ENV_PATH, ENV_PATH), expected, strict=True):
            if digest is None:
                script += f"[ ! -e {path} ] || changed\n"
            else:
                script += (
                    f'[ -f {path} ] && [ "$(sha256sum {path} | cut -d" " -f1)" = {digest} ] '
                    "|| changed\n"
                )
        script += (
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


_SHA256_PREFIX = "sha256:"
_MD5_RE = re.compile(r"^(?:md5:)?(?:[0-9a-f]{2}:){15}[0-9a-f]{2}$", re.I)


def _sha256_digest(token: str) -> str | None:
    """The base64 digest of a ``SHA256:...`` token (case-sensitive, padding ignored), else None."""
    if not token.lower().startswith(_SHA256_PREFIX):
        return None
    return token[len(_SHA256_PREFIX) :].rstrip("=")


def _fingerprints_match(pinned: str, sha256: str) -> bool:
    want = _sha256_digest(sha256)
    return want is not None and any(_sha256_digest(token) == want for token in pinned.split())


def is_md5_fingerprint(text: str) -> bool:
    """Whether ``text`` holds an MD5 host key fingerprint (``MD5:aa:bb:...`` or bare colon hex).

    Those are not accepted as pins: MD5 is broken for this purpose, so a pin must be ``SHA256:...``
    (``ssh-keygen -l -E sha256``). Callers use this to tell the user why a pin was refused.
    """
    return any(_MD5_RE.match(token) for token in text.split())


def fingerprint_matches(pinned: str, key: asyncssh.SSHKey) -> bool:
    """Whether a pinned fingerprint matches ``key``.

    ``pinned`` is an OpenSSH ``SHA256:...`` fingerprint as printed by ``ssh-keygen -l``, alone or
    embedded in text such as ``256 SHA256:... host (ED25519)``. The digest is compared exactly
    (base64 is case-sensitive); MD5 fingerprints never match.
    """
    return _fingerprints_match(pinned, key.get_fingerprint("sha256"))


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
        """Whether ``pinned`` (a SHA256 fingerprint, see :func:`fingerprint_matches`) fits."""
        return _fingerprints_match(pinned, self.sha256)


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
        self.info = HostKeyInfo(self.fingerprint, key.algorithm.decode("ascii", "replace"))
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
            # None, not []: asyncssh reads an empty list as "load the default keypairs", which
            # would offer this host's ~/.ssh/id_* keys to the panel.
            client_keys=None,
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
        # Only the configured key is ever offered. With none, ``client_keys`` must be None:
        # asyncssh treats an empty list as "load the default keypairs" (~/.ssh/id_*), which would
        # authenticate to the panel with an identity nobody configured, and reveal it.
        client_keys: list[asyncssh.SSHKey] | None = None
        if self._private_key:
            try:
                client_keys = [
                    asyncssh.import_private_key(self._private_key, passphrase=self._passphrase)
                ]
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

    # -- poll and env -------------------------------------------------------------------

    async def poll(self) -> PanelSnapshot:
        """Health, settings, drift and setup status of the panel in ONE SSH command.

        This is what a coordinator should call on every update: one exec on the panel instead of
        one each for :meth:`health`, :meth:`get_settings`, :meth:`env_drift` and
        :meth:`setup_status`. The setup status is rebuilt from the files ``ha-kiosk-setup status``
        reads (see :func:`pybrilliantkiosk.setup.status_from_files`), so a poll never starts the
        tool's Python interpreter; use :meth:`setup_status` for the full status (enabled flags,
        releases, ...). A garbled reply raises :class:`CommandFailed`.
        """
        nonce = _new_nonce()
        sections = _split_sections((await self._run(_poll_command(nonce))).stdout, nonce)
        health_section = sections.get("health")
        setup = sections.get("setup")
        if health_section is None or setup is None:
            raise CommandFailed("poll", None, "unexpected output while reading the panel")
        state = _state_from(sections)
        health = parse_health(health_section.body)
        # The tool reads the saved copy of kiosk.env, the running one when there is none.
        saved = state.saved
        tool_env = saved if saved is not None and saved.serialize().strip() else state.running
        facts = {**_facts(setup.body), "version": health.version or ""}
        heartbeat, result = sections.get("heartbeat"), sections.get("result")
        return PanelSnapshot(
            health=health,
            setup=status_from_files(
                facts,
                heartbeat.body if heartbeat else "",
                result.body if result else "",
                health.units,
                tool_env.values() if tool_env else {},
            ),
            settings=state.settings(mask_secrets=True),
            drift=state.drift,
            unsafe=state.unsafe,
        )

    async def _read_state(self) -> _EnvState:
        """Both copies of kiosk.env and the runtime overrides (one round trip)."""
        nonce = _new_nonce()
        return _parse_state((await self._run(_state_command(nonce))).stdout, nonce)

    async def get_env(self, *, mask_secrets: bool = True) -> dict[str, str]:
        """Effective ``kiosk.env`` values (last assignment wins); secrets masked by default.

        Both copies are read; where they differ the running one (``/etc/ha-kiosk/kiosk.env``,
        what the panel uses) wins, as in :meth:`set_env`.
        """
        env = (await self._read_state()).base()[0]
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

        Raises :class:`InvalidArgument` naming the keys (nothing written) if a drifted running
        value cannot be written safely: ``kiosk.env`` is sourced by root shell scripts, so an
        unsafe line is never rewritten and the copies would still differ afterwards.
        """
        state, _ = await self._edit({}, ())
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

        Optimistic concurrency: the hash of each copy as it was read is checked by the panel-side
        script, under the lock, before anything is written. If either file changed meanwhile
        nothing is written, the edit is redone once on fresh state, and a second conflict raises
        :class:`ConcurrentModification`. If the running copy holds a value that cannot be written
        safely (see :func:`pybrilliantkiosk.env.reconcile`) and the caller did not set or delete
        that key, :class:`InvalidArgument` names the keys and nothing is written: the saved value
        would otherwise silently replace, say, a hand-set ``HA_URL`` in both copies. The same
        error names any other line of the final text that is not inert when a root shell sources
        it (an unsafe line already in the saved copy is not copied into the running one).

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
        _, changed = await self._edit(changes, delete)
        if restart and changed:
            await self.restart("ha-kiosk")
        return changed

    async def _edit(
        self, changes: Mapping[str, str], delete: tuple[str, ...]
    ) -> tuple[_EnvState, tuple[str, ...]]:
        """Read, edit and write kiosk.env under the op lock; retries once if it changed meanwhile.

        Returns the state the write was based on and the keys whose effective value changed.
        """
        async with self._op_lock:
            await self._ensure_idle()

            async def attempt() -> tuple[_EnvState, tuple[str, ...]]:
                state = await self._read_state()
                if changes:
                    allowed = await self._allowed_keys()
                    for key in changes:
                        validate_setting_key(key, allowed)
                return state, await self._write(state, changes, delete)

            try:
                return await attempt()
            except ConcurrentModification:
                _LOGGER.info(
                    "kiosk.env on %s changed while it was edited; retrying once", self.host
                )
            return await attempt()

    async def _write(
        self, state: _EnvState, changes: Mapping[str, str], delete: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Apply the edit to the reconciled base and write it (caller holds ``_op_lock``).

        Raises :class:`InvalidArgument` when the running copy has an unsafe value that would have
        to be adopted: the saved value would silently replace it in both copies (reverting, say, a
        hand-set ``HA_URL``), and an unsafe line must never be rewritten. A key the caller sets or
        deletes explicitly is not blocked, since its line is replaced on purpose.
        """
        old, adopted, skipped = state.base()
        passed = tuple(dict.fromkeys((*changes, *delete)))
        blocked = tuple(k for k in skipped if k not in passed)
        if blocked:
            raise InvalidArgument(
                f"the running kiosk.env has values that cannot be written safely "
                f"({', '.join(blocked)}); nothing was changed. Fix them on the panel."
            )
        self.last_reconciled = adopted
        if adopted or skipped:
            parts = []
            if adopted:
                parts.append(f"kept the running value of {', '.join(adopted)}")
            if skipped:
                parts.append(f"replaced the unsafe running value of {', '.join(skipped)}")
            _LOGGER.warning(
                "kiosk.env on %s: the running and the saved copy differed; %s",
                self.host,
                "; ".join(parts),
            )
        overrides = state.overrides
        new = old.apply(changes, delete)
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
        # Lines nobody passed are carried over verbatim, into the root-sourced running copy too:
        # one that is not inert (a hand edit, ``A=1;cmd``) must not travel with them.
        if write_base and (unsafe := new.unsafe_lines()):
            raise InvalidArgument(
                f"kiosk.env has lines that cannot be written safely ({', '.join(unsafe)}); "
                "nothing was changed. Fix them on the panel."
            )
        text = new.serialize()
        digest = sha256_hex(text.encode()) if write_base else ""
        script = _write_env_script(purge, write_base, digest, (state.config_sha, state.running_sha))
        await self._locked(script, stdin=text)
        return changed

    async def get_settings(self, *, mask_secrets: bool = True) -> dict[str, str]:
        """Effective settings: ``kiosk.env`` with the runtime ``overrides.env`` applied on top.

        Missing files count as empty, so an uninstalled panel returns ``{}``.
        """
        return (await self._read_state()).settings(mask_secrets=mask_secrets)

    async def _locked(self, script: str, *, stdin: str | None = None) -> _Result:
        """Run a shell script on the panel under ``ha-kiosk-setup``'s flock."""
        command = f"{SETUP_PYTHON} -c {shlex.quote(_LOCKED_RUNNER)} {shlex.quote(script)}"
        result = await self._run(command, stdin=stdin, check=False)
        if result.exit_status == SetupBusy.code:
            raise SetupBusy("ha-kiosk-setup holds the panel lock; try again when it ends")
        if result.exit_status == _CHANGED_ON_PANEL:
            raise ConcurrentModification(
                "kiosk.env changed on the panel while it was being edited; nothing was changed"
            )
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

    async def device_id(self) -> str:
        """Brilliant's device id of the panel, read from the stock firmware's message bus.

        The id is the panel's identity: it never changes, and survives installing, removing or
        re-flashing the kiosk, because it is read with Brilliant's own Python and libraries and
        needs no file of the kiosk. Raises :class:`DeviceIdUnavailable` when the bus does not
        answer (it restarts while the kiosk is switched on or off) or gives something else.
        """
        result = await self._run(bus.device_id_command(), check=False)
        device_id = bus.parse_device_id(result.stdout) if result.exit_status == 0 else None
        if device_id is None:
            detail = result.stderr.strip().splitlines()[-1:] or [f"exit {result.exit_status}"]
            raise DeviceIdUnavailable(
                f"cannot read the Brilliant device id of {self.host}: {detail[0]}"
            )
        return device_id

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

    async def _setup_wait(self, tool: str, job: _Job | None = None) -> SetupStatus:
        """Poll ``wait --timeout 100`` until the detached job ends (exit != 9).

        Waits for up to :data:`WAIT_ROUNDS` rounds, sized from the job's worst case (a failed
        activation, the rollback and a stock UI restore). ``job.running`` is cleared as soon as the
        tool reports a final state, whatever it is, and not before.
        """
        output = ""
        for _ in range(WAIT_ROUNDS):
            result = await self._setup_run(
                f"wait --timeout {WAIT_TIMEOUT}", tool=tool, time_limit=WAIT_TIMEOUT + 30
            )
            output = result.stdout
            if result.exit_status == EXIT_IN_PROGRESS:
                continue
            if job is not None:
                job.running = False
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
        size: int | None = None,
        env: str | None = None,
        token: str | None = None,
    ) -> SetupStatus:
        """Install or update the kiosk with ``ha-kiosk-setup`` (docs/INTEGRATION.md section 0).

        The bundle is verified here and on the panel, the tool is extracted from it and run with
        ``--detach``; the call polls ``wait`` until the job ends. ``env`` / ``token`` text is only
        needed for a first install (the tool keeps the panel's settings and token otherwise).
        ``size`` is the expected size in bytes, handed to the tool as ``--size``; pass the value
        from a source independent of the download (a pinned release) when there is one, the
        length of ``bundle`` is used otherwise. Raises a :class:`SetupError` subclass for the
        tool's exit codes.
        """
        if not _SHA256_RE.match(sha256):
            raise InvalidArgument("sha256 must be 64 hex characters")
        expected = sha256.lower()
        if size is not None and size != len(bundle):
            raise ChecksumMismatch(f"bundle is {len(bundle)} bytes, expected {size}")
        if await asyncio.to_thread(sha256_hex, bundle) != expected:
            raise ChecksumMismatch("bundle does not match the expected sha256")
        async with self._op_lock:
            await self._ensure_idle()
            made = await self._run(
                f'd=$(mktemp -d {INSTALL_PREFIX}.XXXXXXXX) && chmod 700 "$d" && echo "$d"'
            )
            workdir = made.stdout.strip()
            if not _INSTALL_DIR_RE.match(workdir):
                raise CommandFailed("mktemp -d", made.exit_status, f"unexpected output {workdir!r}")
            job = _Job()
            try:
                return await self._install_in(
                    workdir,
                    bundle,
                    expected,
                    len(bundle) if size is None else size,
                    env,
                    token,
                    job,
                )
            finally:
                await self._cleanup_install(workdir, job)

    async def _install_in(
        self,
        workdir: str,
        bundle: bytes,
        expected: str,
        size: int,
        env: str | None,
        token: str | None,
        job: _Job,
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
        args = f"install --bundle {remote} --sha256 {expected} --size {size} --detach"
        for name, text, flag in (("env", env, "--env"), ("token", token, "--token")):
            if text is not None:
                path = f"{workdir}/{name}"
                await self._run(f"umask 077; cat > {path}", stdin=text)
                args += f" {flag} {path}"
        started = await self._setup_run(args, tool=tool)
        if started.exit_status != 0:
            raise error_for_exit(started.exit_status or 1, started.stdout)
        job.running = True  # the job reads the bundle, the tool and the settings from workdir
        return await self._setup_wait(tool, job)

    async def _cleanup_install(self, workdir: str, job: _Job) -> None:
        """Remove the install directory, unless the detached job may still be using it.

        The job runs the copy of the tool, reads the bundle and the settings files from there.
        When the wait ended without a final state (budget used up, connection lost, cancelled) the
        job may be running: the directory (a bundle's worth of the panel's ``/tmp``) stays, and a
        later install makes its own.
        """
        if job.running:
            _LOGGER.warning(
                "ha-kiosk-setup may still be running on %s; leaving %s in place", self.host, workdir
            )
            return
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
    "is_md5_fingerprint",
    "parse_health",
]
