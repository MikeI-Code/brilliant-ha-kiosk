# Changelog

## 0.1.4

- Documentation links (README, PyPI project URLs) point at files that exist; they pointed at the
  `main` branch, where the library is not published yet.

## 0.1.3

- `Panel.identity()` (new) returns `PanelIdentity(device_id, room)`: the device id and Brilliant's
  room name (`display_name` of `art_config_peripheral`), read by the same Python run on the stock
  message bus. The room is stripped of control characters, cut to 64 characters and `None` when
  empty or unreadable; a failure to read it never fails the device id. `Panel.device_id()` is
  unchanged (it delegates).

## 0.1.2

- **Fix:** a command that times out no longer leaves its process running on the panel. `Panel`
  used asyncssh's `conn.run`, which on a timeout abandons the remote process and its channel; for
  a `set_env` / `reconcile_env` write that meant the `ha-kiosk-setup` flock stayed held. The
  process is now created with `create_process`; on a timeout it is sent SIGKILL and its channel
  is closed, and when the channel does not finish closing within 5 seconds the connection is
  dropped (the next command opens a new one). The command still raises `CannotConnect`
  ("command timed out").

## 0.1.1

Security and correctness fixes found in review; one removal.

- **Security:** a password-only `Panel` no longer offers the host's own `~/.ssh/id_*` keys to the
  panel. asyncssh reads `client_keys=[]` as "load the default keypairs"; `None` is passed now
  (also by `fetch_host_key`). Only a configured key is ever offered.
- **Security:** `set_env` refuses to write when the final text holds a line that is not inert when
  a root shell sources it (an unsafe line already in the saved copy, a bare `A=1;cmd`, junk), and
  names the keys (`InvalidArgument`). `EnvFile.set` drops shadowed earlier assignments of a key,
  and `EnvFile.unsafe_lines()` is new.
- **Security:** host key pins accept `SHA256:...` only, compared case-sensitively. MD5 pins are
  never accepted (`is_md5_fingerprint()` lets a caller say why). **Removed:** `HostKeyInfo.md5`
  (the constructor is now `HostKeyInfo(sha256, algorithm="")`).
- `kiosk_env_from_bundle` validates against the `KEYS` of the `ha-kiosk-setup` inside the bundle
  and never drops a setting silently: an unknown key or a value the tool would refuse raises
  `InvalidArgument` naming it. `KNOWN_KEYS` gained `WEATHER_FX` and is tested to equal the tool's.
- `Panel.device_id()` (new) reads Brilliant's device id from the stock message bus, needing no file
  of the kiosk; `DeviceIdUnavailable` (new) when it cannot.
- `Panel.poll()` no longer starts `ha-kiosk-setup`'s Python interpreter: the setup state is built
  from the files its `status` reads (`status_from_files()`). The `unknown` state is gone from polls.
  The heartbeat's `connected` is read as `ha_connected=True`.
- The section markers of the combined read carry a random per-command nonce, so file content cannot
  forge one.
- `install()` waits for the worst case of a failed install, rollback and stock UI restore
  (`WAIT_ROUNDS` 15, was 8) and does not delete its working directory while the detached job may
  still be running.

## 0.1.0

First release: async SSH client for Brilliant panels running the ha-kiosk (health, `kiosk.env`
read and write with reconciliation of the two copies, units, logs, Brilliant message bus, install
and update through `ha-kiosk-setup`, GitHub releases, version ordering), host key pinning and a
credential-less host key scan.
