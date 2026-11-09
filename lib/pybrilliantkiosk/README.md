# pybrilliantkiosk

Typed, fully async Python library for managing [Brilliant](https://www.brilliant.tech/) smart
panels that run the `brilliant-ha-kiosk` firmware add-on. It is the only code that talks to a
panel; the Home Assistant integration builds on it.

* `asyncssh` for SSH (password or private key, host-key pinning), no blocking I/O
* `aiohttp` for GitHub releases; you inject the `ClientSession`
* `mypy --strict` clean, ships `py.typed`
* Python 3.13 or newer; published to PyPI by CI with trusted publishing from the tag
  `pybrilliantkiosk-v<version>` (`pip install pybrilliantkiosk`)

```python
from pybrilliantkiosk import Panel, Releases

async with Panel("192.168.1.50", password="...", host_key=None) as panel:
    print(panel.host_key_fingerprint)  # pin this in the config flow
    snapshot = await panel.poll()  # health, settings, drift and setup status: ONE SSH command
    health = snapshot.health
    env = await panel.get_env()  # secrets masked
    await panel.set_env({"DAY_LEVEL": "12"}, restart=True)
    print(await panel.logs("ha-kiosk", 100))

client = Releases(session)
stable = await client.latest_stable()  # the release GitHub marks "Latest"
# Verify against a hash and size you keep yourself (streamed, capped at 50 MiB) ...
bundle, sha = await client.download(stable, sha256=PINNED_SHA256, size=PINNED_SIZE)
await panel.install(bundle, sha, size=PINNED_SIZE)
# ... or, weaker, against the release's own .sha256 asset: client.download(release)
```

## What it does

| Area | API |
|---|---|
| Connect | `Panel(host, password=..., private_key=..., username="root", port=22, host_key="SHA256:...")`, used as an async context manager. `fetch_host_key(host, port=22)` reads a panel's host key fingerprint before any credentials are sent. |
| Poll | `Panel.poll()` returns a `PanelSnapshot` (health, effective settings, settings drift, `ha-kiosk-setup` status) with **one** SSH command. `Panel.health()` for health only. |
| Settings | `get_env()`, `set_env(changes, delete=(), restart=False)`, `env_drift()`, `reconcile_env()`, `check_env(text)`. Both copies of `kiosk.env` on the panel are kept in step; keys are validated against the installed `ha-kiosk-setup`'s allow-list. |
| Services | `restart(unit)`, `start(unit)`, `stop(unit)`, `logs(unit, lines=100)`, `reboot()`. `UNITS` lists the kiosk's systemd units. |
| Identity | `identity()` returns `PanelIdentity(device_id, room)` from the stock firmware's message bus; works with or without the kiosk. `device_id()` returns the id alone. |
| Brilliant bus | `bus_peripherals()`, `bus_get(peripheral)`, `bus_set(peripheral, values)`. |
| Install | `install(bundle, sha256, size=None, env=None, token=None)` verifies the bundle, runs `ha-kiosk-setup install --detach` and waits for it (`env` and `token` only for a first install); `setup_command(cmd)` for `reapply`, `enable`, `disable`, `rollback`, `uninstall` or `purge`; `setup_status()`, `setup_diagnostics()`. |
| Releases | `Releases(session)`: `list()`, `latest_stable()`, `download(release, sha256=None, size=None)` (streamed, capped at 50 MiB). `parse_version()` / `is_newer()` order stable and nightly bundle versions. |
| Bundles | `kiosk_env_from_bundle(bundle, overrides)` renders a first-install `kiosk.env` from the bundle's own example, validated against the bundle's `ha-kiosk-setup`. |

Pin the host key before any credentials are sent, and read the panel's identity (works with or
without the kiosk installed):

```python
from pybrilliantkiosk import fetch_host_key

info = await fetch_host_key("192.168.1.50")
print(info.sha256)  # show this to the user, pin it once confirmed

async with Panel("192.168.1.50", password="...", host_key=info.sha256) as panel:
    who = await panel.identity()  # PanelIdentity(device_id=..., room="Kitchen")
```

`set_env` and `reconcile_env` hold the panel's setup lock, refuse (`InvalidArgument`, naming the
keys, nothing written) when the running `kiosk.env` has a value that cannot be written safely
instead of reverting it, and abort if `kiosk.env` changed on the panel since it was read
(`ConcurrentModification`, retried once). A command that times out is killed on the panel and
raises `CannotConnect`.

Errors: `CannotConnect`, `AuthFailed`, `HostKeyMismatch`, `CommandFailed(exit_status, stderr)`,
`InvalidArgument`, `ChecksumMismatch`, `ConcurrentModification`, `ReleaseError`; all derive from
`PanelError`:

| Exception | Meaning |
|---|---|
| `CannotConnect` | No connection, or a command timed out. |
| `AuthFailed` | Password or key rejected. |
| `HostKeyMismatch` | The panel's host key differs from the pinned fingerprint. |
| `CommandFailed(exit_status, stderr)` | A remote command failed. |
| `InvalidArgument` | A key, value, unit name or fingerprint the library refuses (also a `ValueError`). |
| `ChecksumMismatch` | A downloaded bundle did not match its hash or size. |
| `ConcurrentModification` | `kiosk.env` changed on the panel during a write. |
| `DeviceIdUnavailable` | The Brilliant device id could not be read (the stock bus is restarting). |
| `ReleaseError` | GitHub release listing or download failed. |
| `SetupError` and its subclasses `VerificationFailed`, `SettingsRejected`, `ActivationRolledBack`, `StockUIRestored`, `NotInstalled`, `SetupBusy` | `ha-kiosk-setup` exit codes 3, 4, 5, 6, 7 and 8. |

Host key pins accept `SHA256:...` fingerprints only; `is_md5_fingerprint()` lets a caller
explain why an MD5 pin is refused.

## Development

```
cd lib/pybrilliantkiosk
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy --strict src && pytest  # 100 % coverage gate
```

Source, issues and changelog:
[github.com/MikeI-Code/brilliant-ha-kiosk/tree/nightly/lib/pybrilliantkiosk](https://github.com/MikeI-Code/brilliant-ha-kiosk/tree/nightly/lib/pybrilliantkiosk),
[issues](https://github.com/MikeI-Code/brilliant-ha-kiosk/issues),
[CHANGELOG](https://github.com/MikeI-Code/brilliant-ha-kiosk/blob/nightly/lib/pybrilliantkiosk/CHANGELOG.md).
Apache License 2.0.
