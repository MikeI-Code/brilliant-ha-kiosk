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
    health = await panel.health()
    env = await panel.get_env()  # secrets masked
    await panel.set_env({"DAY_LEVEL": "12"}, restart=True)
    print(await panel.logs("ha-kiosk", 100))

releases = await Releases(session).list()
bundle, sha = await Releases(session).download(releases[0])
await panel.install(bundle, sha)
```

Errors: `CannotConnect`, `AuthFailed`, `HostKeyMismatch`, `CommandFailed(exit_status, stderr)`,
`InvalidArgument`, `ChecksumMismatch`, `ReleaseError`; all derive from `PanelError`.

## Development

```
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
ruff check . && mypy --strict src && pytest
```
