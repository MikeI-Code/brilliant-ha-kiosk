# pybrilliantkiosk

Typed, fully async Python library for managing [Brilliant](https://www.brilliant.tech/) smart
panels that run the `brilliant-ha-kiosk` firmware add-on. It is the only code that talks to a
panel; the Home Assistant integration builds on it.

* `asyncssh` for SSH (password or private key, host-key pinning), no blocking I/O
* `aiohttp` for GitHub releases; you inject the `ClientSession`
* `mypy --strict` clean, ships `py.typed`

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

`set_env` and `reconcile_env` hold the panel's setup lock, refuse (`InvalidArgument`, naming the
keys, nothing written) when the running `kiosk.env` has a value that cannot be written safely
instead of reverting it, and abort if `kiosk.env` changed on the panel since it was read
(`ConcurrentModification`, retried once).

Errors: `CannotConnect`, `AuthFailed`, `HostKeyMismatch`, `CommandFailed(exit_status, stderr)`,
`InvalidArgument`, `ChecksumMismatch`, `ConcurrentModification`, `ReleaseError`; all derive from
`PanelError`.

## Development

```
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy --strict src && pytest  # 100 % coverage gate
```
