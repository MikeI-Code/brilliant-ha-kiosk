# Integration contract for external installers

*Rewritten 2026-11-09 against the code on `nightly` (bundle v1.4.0 and later; `kiosk/ha-kiosk-setup`,
`kiosk/ha-kiosk-mqtt.py`, `kiosk/ha-kiosk-bridge.py`, `kiosk/install-local.sh`, `systemd/*`,
`scripts/build-bundle.sh`).*

This document is the interface between the HA kiosk and tools that install and manage it on
Brilliant panels: the `brilliant_kiosk` Home Assistant integration and its library
`pybrilliantkiosk` ([home-assistant-integration.md](home-assistant-integration.md),
[admin-helper-spec.md](admin-helper-spec.md)), fleet scripts, or an operator over SSH. Anything
not written here is internal and may change without notice.

It has two parts:

* **Section 0, Implemented today.** What the code does. Everything in section 0 can be relied on.
  Where section 0 and the rest of the document disagree, section 0 describes the code.
* **Sections 1 to 16, Planned (contract 1).** A design for a later MAJOR release (`v2.0.0`).
  Nothing in those sections exists unless section 0 says so. Each planned section ends with a
  *Current state* note that points back to section 0 where the code already covers the ground.

Hand-operated installation, the `install-local.sh` options and the settings reference are in
[MANUAL.md](MANUAL.md). MUST, SHOULD and MAY are used as in RFC 2119.

---

## 0. Implemented today

### 0.1 Release artifacts

Every GitHub release (stable `vX.Y.Z`, or a `nightly-YYYYMMDD` prerelease) carries two assets:

| Asset | Contents |
|---|---|
| `brilliant-ha-kiosk-<VERSION>.tar.gz` | The bundle. One top-level directory `brilliant-ha-kiosk-<VERSION>/` |
| `brilliant-ha-kiosk-<VERSION>.tar.gz.sha256` | `sha256sum` line of the bundle |

`VERSION` is the stable tag (`v1.4.0`) or, for a nightly, `<last stable>-nightly.<UTC date>.g<short sha>`
(`v1.4.0-nightly.20261108.g89ec239`; `.github/workflows/nightly.yml`). A nightly's *tag* is not
its version. `scripts/build-bundle.sh` also writes `dist/<name>.release.json` =
`{"name": "<name>.tar.gz", "version", "sha256", "size"}` locally; it is not uploaded.

Bundle layout (`scripts/build-bundle.sh`):

| Path in the bundle | Contents |
|---|---|
| `ha-kiosk-setup` | The on-panel tool (also at `ha-kiosk/ha-kiosk-setup`) |
| `install-local.sh` | The file installer the tool drives |
| `VERSION`, `MANIFEST.sha256` | Version string; `sha256sum` of every other file |
| `ha-kiosk/` | `native/` (QML, fonts, `sky/`, `layout.example.json`), `kiosk.qml`, `ha-kiosk-*.py`, `ha-kiosk-*.sh`, `brilliant_bus.py`, `inject_touch.py` |
| `systemd/` | Units, `ha-kiosk-tmpfiles.conf`, `message_bus-ha-kiosk.conf` |
| `kiosk.env.example`, `LICENSE`, `NOTICE` | |

Builds are reproducible: `tar --sort=name --mtime=@<commit time> --owner=0 --group=0
--numeric-owner | gzip -9n`, `MANIFEST.sha256` in `LC_ALL=C` order. The release workflow uploads
without `--clobber`, so a published bundle is never replaced. There is **no signature** and no
`release.json` asset: the root of trust is a SHA-256 and size the installer pins itself
(`custom_components/brilliant_kiosk/pinned.py` for stable releases; a nightly is checked against
its `.sha256` asset only).

### 0.2 Running the tool

`ha-kiosk-setup` is a Python script for the panel's own interpreter
`/data/switch-embedded/env/bin/python` (its shebang). It runs as root and needs `paho-mqtt`
(present in that environment) only for the MQTT notices in 0.11.

First install, from a verified bundle:

```sh
echo "<sha256>  bundle.tar.gz" | sha256sum -c
tar -xzOf bundle.tar.gz brilliant-ha-kiosk-<VERSION>/ha-kiosk-setup > setup.py
/data/switch-embedded/env/bin/python setup.py install --bundle bundle.tar.gz --sha256 <sha256> \
    [--size BYTES] [--env FILE|-] [--token FILE | --token-stdin] [--layout FILE] \
    [--allow-offline] [--detach]
```

After a successful activation the tool copies itself to `/var/lib/ha-kiosk/ha-kiosk-setup`
(0755). Use that copy for everything else; `/var/lib` survives firmware updates, `/etc` may not:

```sh
/data/switch-embedded/env/bin/python /var/lib/ha-kiosk/ha-kiosk-setup <command> [options]
```

### 0.3 Commands and options

| Command | Effect | Changes the panel |
|---|---|---|
| `install --bundle FILE --sha256 HEX [...]` | Verify, unpack to `releases/<VERSION>`, store settings/token/layout, activate, health-check; on failure roll back or restore the stock UI; prune releases other than current and previous | yes |
| `reapply` | Re-activate `current` (after a firmware update rebuilt `/etc`). If the state is `disabled` it does nothing and exits 0 | yes |
| `enable` | Activate `current` again after `disable` | yes |
| `disable` | Publish `kiosk_state=disabled`, hand the screen back to Brilliant's UI (`install-local.sh --uninstall`), state `disabled`. Releases, settings and token stay | yes |
| `rollback` | Activate `previous`; afterwards `previous` is the version rolled away from | yes |
| `uninstall` | Publish `kiosk_state=not_installed`; copy `overrides.env` and `/etc/ha-kiosk/photos` to `/var/lib/ha-kiosk-keep`; run `install-local.sh --uninstall --purge`; delete `/etc/ha-kiosk` and `/var/lib/ha-kiosk`; exit 1 if a secret file is still present | yes |
| `purge` | `uninstall` without the keep copy, and `/var/lib/ha-kiosk-keep` removed | yes |
| `wait [--timeout SECS]` | Wait for a detached job (default 100 s, at most 3600), then print `status` | no |
| `status` | Status JSON (0.5) | no |
| `diagnostics` | Status JSON plus redacted recent logs (0.13) | no |
| `check-env FILE` or `check-env -` | Validate a settings file without installing; `{"ok": bool, "errors": [...]}` | no |
| `-h`, `--help`, `help` | Usage text | no |

| Option | Commands | Meaning |
|---|---|---|
| `--bundle FILE` | install | Bundle tarball (required) |
| `--sha256 HEX` | install | Expected SHA-256, 64 hex characters (required) |
| `--size BYTES` | install | Expected size; mismatch is exit 3 |
| `--env FILE` or `--env -` | install | Settings to install (file or stdin). Without it an existing `/etc/ha-kiosk/kiosk.env` is adopted on a first `install` |
| `--token FILE`, `--token-stdin` | install | Home Assistant long-lived token (file, or first line of stdin). Without it an existing `/etc/ha-kiosk/token` is adopted |
| `--layout FILE` | install | Local fallback `layout.json`, stored as `config/layout.json` |
| `--allow-offline` | install, reapply, enable, rollback | Health check passes without a Home Assistant connection |
| `--detach` | every changing command | Run as a transient systemd unit and return at once (0.6) |
| `--timeout SECS` | wait | Maximum wait |

`--env -` and `--token-stdin` together are exit 2 (both read stdin). `--detach` with either is exit 2.
Every option value is positional after its flag; unknown words are exit 2. Changing commands
must run as root (exit 2 otherwise).

### 0.4 Exit codes

| Code | Name | Meaning | Panel state after |
|---|---|---|---|
| 0 | ok | Command completed | as requested |
| 1 | error | Internal error, a failed helper command (`install-local.sh`, `systemctl`), `rollback` with no previous version, or secrets left after `uninstall` | see `status` |
| 2 | usage | Missing or unknown command/option, bad `--sha256` format, not root, stdin conflicts | unchanged |
| 3 | verify | Bundle size or SHA-256 mismatch, unsafe tar member, not exactly one top-level directory, missing or invalid `VERSION`, `MANIFEST.sha256` mismatch | unchanged |
| 4 | config | Settings rejected (`--env`, adopted `/etc/ha-kiosk/kiosk.env`, or `check-env`), or token not matching `^[A-Za-z0-9._-]{20,4096}$` | unchanged (install); `check-env` changes nothing anyway |
| 5 | rolled_back | The new version did not come up; the previous one was activated (with `--allow-offline`) and is running | `active`, running `previous` |
| 6 | stock_restored | The new version did not come up and there was no usable previous version (or it failed too); Brilliant's UI was restored | `failed` |
| 7 | not_installed | `reapply`, `enable`, `disable`, `rollback`: no `current` release on the panel; or a release directory named by `activate` is missing. `uninstall` and `purge` do not need an installed kiosk | unchanged |
| 8 | busy | A detached job is running, or another `ha-kiosk-setup` holds the lock | unchanged |
| 9 | in_progress | `wait` only: the job is still running when the timeout ends | job continues |

`status`, `diagnostics` and `check-env` never return 5 to 9. `wait` returns 9, or the exit code
recorded by the job (`last_result.exit`), or 0 when there is no recorded result.

The library maps these to typed exceptions (`pybrilliantkiosk.exceptions`: `VerificationFailed` 3,
`SettingsRejected` 4, `ActivationRolledBack` 5, `StockUIRestored` 6, `NotInstalled` 7, `SetupBusy` 8,
`SetupError` for the rest).

### 0.5 Output

Stdout always carries exactly one JSON object; log lines go to stderr prefixed `ha-kiosk-setup:`
and are appended to `/var/lib/ha-kiosk/setup.log` while that directory exists.

| Situation | Stdout |
|---|---|
| Changing command succeeded, `status`, `wait` | The status object below |
| `--detach` accepted | `{"state": "in_progress", "job": "ha-kiosk-setup-job", "next": "ha-kiosk-setup wait"}` |
| Any failure (exit 1 to 8) | `{"error": "<message>", "exit": <code>}` |
| `check-env` | `{"ok": true|false, "errors": ["line 3: unknown setting FOO", ...]}` |
| `diagnostics` | The status object plus `"logs": [...]` |

Status object (`schema` 1; fields are only added within a schema):

| Field | Type | Meaning |
|---|---|---|
| `schema` | int | `1` |
| `state` | string | `not_installed`, `unmanaged`, `active`, `disabled`, `failed`, `in_progress` (see below) |
| `version` | string or null | `current` release; else `/etc/ha-kiosk/VERSION` (an install made by hand with `install-local.sh`) |
| `previous` | string or null | Rollback target |
| `releases` | list | Release directories under `/var/lib/ha-kiosk/releases` that contain `install-local.sh`, sorted |
| `ui.heartbeat_age_s` | number or null | Seconds since the UI's last heartbeat (0.8) |
| `ui.home_assistant` | string or null | The UI's HA connection status from the heartbeat (`connected` when it is) |
| `services.<unit>` | `{"active": bool, "enabled": bool}` | For `ha-kiosk`, `ha-kiosk-bridge`, `ha-kiosk-display`, `ha-kiosk-voice`, `ha-kiosk-intercom`, `ha-kiosk-capture`, `switch_ui_app` |
| `mqtt.mode` | string | `KIOSK_MQTT` of the saved settings (running copy when there is none), default `"1"` |
| `mqtt.base` | string or null | `/var/lib/ha-kiosk/mqtt_base`, written by the bridge on each MQTT connect (`<MQTT_BASE>/<node>`) |
| `firmware` | string or null | `/etc/brilliant_release`, else the first line of `/etc/os-release` |
| `secrets_present` | list of paths | Those of `/etc/ha-kiosk/token`, `/etc/ha-kiosk/kiosk.env`, `/var/lib/ha-kiosk/config/token`, `/var/lib/ha-kiosk/config/kiosk.env` that exist. Empty after `uninstall` |
| `last_result` | object or null | `/run/ha-kiosk/setup-result.json`: `{"command", "exit", "message", "time"}` of the last changing command (not of `status`, `diagnostics`, `wait`, `check-env`); cleared at boot (`/run`) and when a `--detach` job starts |

How `state` is derived: the file `/var/lib/ha-kiosk/state`; when it does not exist,
`unmanaged` if `/etc/ha-kiosk/native/main.qml` exists (installed without the tool), else
`not_installed`. Then: the job unit active (and the caller is not the job itself) gives
`in_progress`; a stored `activating` (interrupted by a power cut) gives `failed`; a stored
`active` while `ha-kiosk.service` is not active gives `failed`.

### 0.6 Detach, wait and locking

Turning the kiosk on or off restarts Brilliant's `message_bus`, which alone takes 60 to 90 s, and
`install-local.sh` may take up to 400 s before the tool gives up on it. A caller with a
per-command time limit MUST run changing commands with `--detach` and then call `wait`:

```sh
setup install --bundle /tmp/x/bundle.tar.gz --sha256 ... --env /tmp/x/kiosk.env --detach
setup wait --timeout 100      # repeat while the exit code is 9
```

* `--detach` re-runs the same command as the transient unit `ha-kiosk-setup-job`
  (`systemd-run --unit=ha-kiosk-setup-job --setenv=HA_KIOSK_SETUP_JOB=1`), after making
  `--bundle`, `--env`, `--token`, `--layout` paths absolute and deleting the previous
  `setup-result.json`. The files must stay in place until the job ends.
* While that unit is active every other changing command exits 8, and `status` reports
  `in_progress`. `wait` polls the unit every 2 s.
* Changing commands take an exclusive non-blocking `fcntl.flock` on `/run/ha-kiosk-setup.lock`;
  a second caller exits 8. `status`, `diagnostics`, `wait` and `check-env` take no lock.
* The worst case of one job, from the tool's own timeouts, is 2 × (400 s + 60 s) + 400 s = 1320 s
  (activation, re-activation of `previous`, stock-UI restore); `pybrilliantkiosk` waits up to
  15 rounds of `wait --timeout 100` for that reason.

The library uses the same lock (via Python `fcntl`, `/run/ha-kiosk-setup.lock`, exit 8 when held)
for its own `kiosk.env` writes, so they never interleave with a setup run.

### 0.7 On-panel layout

| Path | Contents | Mode | Written by |
|---|---|---|---|
| `/var/lib/ha-kiosk/releases/<VERSION>/` | Unpacked bundle; `current` and `previous` are kept, others pruned after each `install` | dir 0700 (`releases/`) | setup |
| `/var/lib/ha-kiosk/releases/.staging/` | Extraction area during `install`; removed afterwards | | setup |
| `/var/lib/ha-kiosk/config/kiosk.env` | Saved, validated settings (0.9) | 0600 in 0700 | setup |
| `/var/lib/ha-kiosk/config/token` | Saved HA token, one line | 0600 | setup |
| `/var/lib/ha-kiosk/config/layout.json` | Saved `--layout` | | setup |
| `/var/lib/ha-kiosk/current`, `previous`, `state` | Plain text: version, version, `activating`/`active`/`disabled`/`failed` | | setup |
| `/var/lib/ha-kiosk/ha-kiosk-setup` | Copy of the tool from the active release | 0755 | setup |
| `/var/lib/ha-kiosk/setup.log` | Append-only log of every run | | setup |
| `/var/lib/ha-kiosk/mqtt_base` | `<MQTT_BASE>/<node>` of the last MQTT connection | | bridge |
| `/var/lib/ha-kiosk/overrides.env` | Runtime overrides from MQTT (0.12) | | bridge |
| `/var/lib/ha-kiosk/ha-photos/`, `security.json` | HA photo mirror; firmware on which a panel disarm last worked | | bridge |
| `/var/lib/ha-kiosk-keep/` | `overrides.env` and `photos/` kept by `uninstall`, restored by the next `install`, deleted by `purge` | 0700 | setup |
| `/var/lib/ha-kiosk-swap/swapfile` | Swap file (`SWAP_MB`) | 0600 | `ha-kiosk-swap.sh` |
| `/etc/ha-kiosk/` | The **running copy**: code (`native/`, `*.py`, `*.sh`), `kiosk.env` (0600), `token` (0600), `VERSION`, `install-local.sh`, `photos/` | | `install-local.sh` |
| `/etc/systemd/system/ha-kiosk*.service`, `ha-kiosk.service.d/native.conf` | Units from the release's `systemd/` | | `install-local.sh` |
| `/etc/systemd/system/message_bus.service.d/ha-kiosk-display.conf` | Display drop-in (bind-mounts dummy backlight files for Brilliant's services) | | `install-local.sh` |
| `/etc/tmpfiles.d/ha-kiosk.conf` | Creates `/run/ha-kiosk-shadow/` at boot | | `install-local.sh` |
| `/run/ha-kiosk/heartbeat.json` | `{"time": <epoch>, "ha": "<status>"}`, written by the bridge from the UI's POST | 0700 dir | bridge |
| `/run/ha-kiosk/setup-result.json` | Last result (0.5) | | setup |
| `/run/ha-kiosk-setup.lock` | Lock file (0.6) | | setup |
| `/run/ha-kiosk-display/state`, `levels`, `photos.txt` | Display state (`awake`, `photos`, `dim`, `asleep`), smart-dim levels, screensaver playlist | | `ha-kiosk-display.sh` |
| `/run/ha-kiosk-shadow/brightness`, `bl_power`, `blank` | Dummy backlight files seen by Brilliant's services | 0666 | tmpfiles |

`/etc/ha-kiosk` is rebuilt from `releases/<current>` and `config/` by `reapply`, `enable`,
`rollback` and `install`. The running copy and the saved copy of `kiosk.env` can therefore drift
when the running copy is edited by hand; the library detects that (`Panel.env_drift`) and the
integration raises a repair issue.

### 0.8 Install, activation and health check

`install` runs these steps in order; nothing on the panel changes before step 5.

1. Validate `--sha256` format; compare `--size` (exit 3); hash the bundle in 1 MB chunks (exit 3).
2. Read and validate `--env` (0.9) and the token (0.10) (exit 4).
3. Unpack into `releases/.staging/`, rejecting members with an absolute path, a `..` component,
   or that are not a regular file or directory (exit 3; note that `tarfile.extractall` is used,
   without `--no-same-owner`-style filtering). Require one top-level directory, a `VERSION`
   matching `^[A-Za-z0-9._+-]{1,64}$`, and `sha256sum -c MANIFEST.sha256` to pass (exit 3).
4. Move the directory to `releases/<VERSION>` (replacing it if `<VERSION>` is the current one).
5. Write `config/kiosk.env` (rendered, 0.9) and `config/token` if given; otherwise, on a first
   install, adopt `/etc/ha-kiosk/kiosk.env` (validated; exit 4 if it fails) and `/etc/ha-kiosk/token`.
   Copy `--layout` to `config/layout.json`. Restore `/var/lib/ha-kiosk-keep` if present.
6. **Activate** (shared with `reapply`, `enable`, `rollback`): delete the heartbeat file, write
   `state=activating`, run `/bin/sh releases/<VERSION>/install-local.sh --env config/kiosk.env
   --token config/token --layout config/layout.json` (each flag only if the file exists), timeout
   400 s. `install-local.sh` copies the code and units, installs the display drop-in (restarting
   `message_bus` only when the drop-in changed), disables `switch_ui_app`, enables and restarts
   `ha-kiosk-swap`, `ha-kiosk-bridge`, `ha-kiosk`, `ha-kiosk-display`, and starts voice, intercom
   and capture according to `KIOSK_VOICE`, `KIOSK_INTERCOM`, `KIOSK_CAPTURE`, `ALLOW_MIC`,
   `ALLOW_CAMERA`.
7. **Health check**: for up to 60 s after `install-local.sh` returns, every 2 s, read
   `/run/ha-kiosk/heartbeat.json`. Pass when `time` is later than the start of activation,
   `ha-kiosk.service` is active, and `ha == "connected"` (or `--allow-offline`).
8. On success: `previous` ← old `current` (if different), `current` ← `VERSION`, `state=active`,
   copy the tool to `/var/lib/ha-kiosk/ha-kiosk-setup`.
9. On failure: if `previous` exists and differs, activate it with offline allowed; success is
   exit 5 (`state=active`). Otherwise restore the stock UI (`install-local.sh --uninstall` of the
   running copy, else of each release newest first, else `systemctl enable --now switch_ui_app`),
   `state=failed`, publish `kiosk_state=failed`, exit 6.
10. Always: prune releases other than `current` and `previous`.

**Heartbeat.** `native/main.qml` POSTs `{"ha": <Ha.status>}` to the bridge's localhost HTTP
server (`BRIDGE_PORT`, default 8765) every 10 s, starting immediately. The bridge writes
`/run/ha-kiosk/heartbeat.json` = `{"time": <epoch seconds>, "ha": "<status, 20 chars max>"}`
atomically (temp file + rename). The heartbeat says the UI's event loop is running and what its
HA connection state is; it does not say that a frame was rendered.

There is no transaction log and no boot-time recovery: an interruption during activation leaves
`state=activating`, which `status` reports as `failed`; re-running `install` or `reapply` is safe.

### 0.9 Settings: `kiosk.env`

The tool **parses** settings, it never sources them. `parse_env` in `ha-kiosk-setup` (and the
copy of the same rules in `ha-kiosk-mqtt.py`):

* A line is `KEY=value` after stripping surrounding whitespace, key `^[A-Z][A-Z0-9_]*$`, no
  spaces around `=`. Empty lines and lines starting with `#` are skipped; `#` elsewhere is data.
* One pair of matching surrounding quotes (`"` or `'`) is removed from the value.
* The key must be in the allowlist below or match `GESTURE_[23]_(TAP|SWIPE_(LEFT|RIGHT|UP|DOWN))`;
  anything else is an error ("unknown setting"), not a no-op.
* The value may not contain `"`, `'`, backtick, `$`, backslash, or a control character
  (0x00 to 0x1f, 0x7f); and may be at most 1024 characters. Spaces and `; | & > ( )` are allowed.
* The last assignment of a key wins.
* Cross-field checks: `HA_URL` must match `^https?://[^\s/]+`; `MQTT_PORT`, `VOICE_PORT`,
  `INTERCOM_PORT`, `CAPTURE_PORT` must be integers 1 to 65535; `KIOSK_MQTT` must be one of
  `1 0 on off true false yes no kiosk` or empty.
* Any error rejects the whole file (exit 4; `check-env` lists up to all errors, `install`
  reports the first ten).

Allowlist (`KEYS` in `kiosk/ha-kiosk-setup`; `pybrilliantkiosk` parses this list out of the
installed tool so a newer bundle can add keys):

```
KIOSK_MODE HA_URL HA_ZOOM LAYOUT_DASHBOARD SLEEP_START SLEEP_END DAY_LEVEL DIM_LEVEL MIN_LEVEL
IDLE_DIM_SECS NIGHT_WAKE_SECS NIGHT_MODE SCREENSAVER SCREENSAVER_WEATHER PHOTO_LEVEL PHOTO_SECS
HA_PHOTO_FOLDER PHOTO_SYNC_MINS PHOTO_DIRS STOCK_ART TZ TZ_LOCAL MOTION_WAKE MOTION_CLEAR_SECS
CPU_AWAKE_GOV CPU_IDLE_GOV CPU_SLEEP_GOV SWAP_MB SWAPPINESS SMART_DIM SUN_DARK_ELEV SUN_BRIGHT_ELEV
AMBIENT_SECS LIGHT_SENSOR LUX_DARK LUX_BRIGHT LOAD_ENTITY ROOM_SPEAKERS ROOM_LIGHTS TOUCH_DEVICE
KIOSK_MQTT MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASSWORD MQTT_BASE MQTT_DISCOVERY_PREFIX MQTT_DEVICE_ID
DIAG_SECS KIOSK_VOICE VOICE_PORT VOICE_NAME VOICE_WAKE VOICE_ALLOW VOICE_BIND VOICE_LISTEN_SECS
VOICE_TTS_ENGINE VOICE_MIC VOICE_SPEAKER VOICE_DEBUG KIOSK_INTERCOM INTERCOM_MIC INTERCOM_PORT
INTERCOM_MAX_SECS INTERCOM_MIC_DEVICE INTERCOM_SPEAKER INTERCOM_BIND KIOSK_CAPTURE CAPTURE_VIDEO
CAPTURE_PORT CAPTURE_ALLOW CAPTURE_BIND CAPTURE_MAX_SECS CAPTURE_TTL CAPTURE_MIC CAPTURE_UPLOAD
CAPTURE_UPLOAD_DIR CAMERA_LIVE CAMERA_LIVE_ON CLIMATE_CONTROLLER CLIMATE_THERMOSTATS DOORBELL
DOORBELL_CAMERAS DOORBELL_MOTION DOORBELL_SECS SECURITY_TAB SECURITY_ACCESS SECURITY_DEVICES
PANEL_GROUP ALLOW_MIC ALLOW_CAMERA WEATHER_FX
```

**Rendering.** The saved copy is rewritten from the parsed dict as one header comment
(`# Written by ha-kiosk-setup from validated settings. Edit the source file, not this one.`)
followed by `KEY="value"` lines in first-appearance order. Because the forbidden characters keep a
double-quoted shell string inert, the file is safe for the consumers that still source it:
`ha-kiosk-display.sh` and `ha-kiosk-swap.sh` (`. /etc/ha-kiosk/kiosk.env`), and systemd
`EnvironmentFile=` in every unit (`ha-kiosk.service` without the `-` prefix, so a missing file
stops the UI). `ha-kiosk-bridge.py` (`read_kiosk_env`, photo sync) has a simpler reader (split at
the first `=`, strip `"`); `ha-kiosk-mqtt.py` uses the tool's rules.

`/etc/ha-kiosk/kiosk.env` is a copy of the saved file made by `install-local.sh` (`cp`, then
`chmod 600`). `install-local.sh --no-mqtt` appends a bare `KIOSK_MQTT=0` line; the tool does not
use that option.

### 0.10 Token

`--token FILE` (whole file, stripped) or `--token-stdin` (first line). It must match
`^[A-Za-z0-9._-]{20,4096}$` (exit 4). It is written to `/var/lib/ha-kiosk/config/token` with a
trailing newline, 0600 in a 0700 directory, through a temp file and `os.replace`;
`install-local.sh` copies it to `/etc/ha-kiosk/token` and `chmod 600`s it (a short window at the
default umask). Without `--token`, a first `install` adopts an existing `/etc/ha-kiosk/token`.
`uninstall` and `purge` delete both copies and fail with exit 1 if either remains. Deletion on
eMMC is best effort: the operator SHOULD revoke the token in Home Assistant when a panel leaves.

The token is an operator-created long-lived access token. The UI, `ha-kiosk-ambient.py`,
`ha-kiosk-photos.py` and `ha-kiosk-voice.py` use it against `HA_URL`; the voice service attaches it
only to URLs whose scheme, host and port equal those of `HA_URL`.

### 0.11 MQTT

MQTT runs inside `ha-kiosk-bridge.py`, which loads `/etc/ha-kiosk/ha-kiosk-mqtt.py` at start. It
is off when `MQTT_HOST` is empty or `KIOSK_MQTT` is `0`, `off`, `false` or `no`.

| `KIOSK_MQTT` | Mode | Publishes |
|---|---|---|
| unset, `1`, `on`, `true`, `yes` | full (built-in) | Everything in the table below |
| `kiosk` | kiosk | Only the rows marked *kiosk*; on connect it first publishes empty retained payloads for the *panel* rows (per gangbox) to withdraw an earlier full-mode discovery |
| `0`, `off`, `false`, `no` | off | Nothing |

In both modes the bridge subscribes `<base>/+/set`, `<base>/wake/press` and
`<prefix>/status` (HA birth message: discovery is re-sent on `online`). A `load/set` command is
honoured in kiosk mode too; the subscription list is not narrowed.

**Namespace.** `node = brilliant_<first 12 characters of the Brilliant device id>` (read from the
message bus at start); `base = <MQTT_BASE, default brilliant_kiosk>/<node>`;
`prefix = <MQTT_DISCOVERY_PREFIX, default homeassistant>`. Client id `<node>_kiosk`, keepalive
30 s, will `<base>/availability = offline` (retained). Discovery topics are
`<prefix>/<component>/<node>/<key>/config` with `unique_id = object_id = <node>_<key>`,
`availability_topic = <base>/availability` and a `device` block:

* without `MQTT_DEVICE_ID`: `{"identifiers": ["<node>"], "name": "<room> Brilliant panel",
  "manufacturer": "Brilliant", "model": "Brilliant Control (HA kiosk)"}` (`room` falls back to `Brilliant`);
* with `MQTT_DEVICE_ID`: `{"identifiers": ["<MQTT_DEVICE_ID>"]}` only, so the entities join
  another integration's device without renaming it.

The `brilliant_kiosk` integration never claims these identifiers; its own device is
`(brilliant_kiosk, <node>)` and points at the MQTT device with `via_device`.

**Entities and topics** (`<key>` under `<base>/`; states are retained and published on change):

| Component / key | Owner | State topic | Command topic and payload |
|---|---|---|---|
| `light/load` | panel | `load/state` JSON `{"state": "ON"/"OFF", "brightness": 0-100}` | `load/set` JSON `{"state": "ON"/"OFF"}` or `{"brightness": n}` |
| `sensor/load_power[_N]` (W), `sensor/load_temperature[_N]` (°C, diagnostic) | panel | same name | |
| `sensor/min_dim_level[_N]`, `max_dim_level[_N]`, `dimming_edge[_N]`, `dim_calibration[_N]` (diagnostic, disabled by default) | panel | same name | |
| `sensor/faceplate_temperature`, `sensor/cpu_temperature` (diagnostic) | panel | same name | |
| `sensor/firmware_version`, `sensor/boot_time` (diagnostic) | panel | same name | |
| `binary_sensor/homekit_paired`, `homekit_enabled` (if `homekit_peripheral` exists) | panel | same name, `ON`/`OFF` | |
| `sensor/shelly_ble` (if `ble_peripheral` exists) | panel | `shelly_ble` count, `shelly_ble/attributes` JSON | |
| `binary_sensor/motion` | panel | `motion`, `ON`/`OFF` | |
| `sensor/display_state` | kiosk | `display_state`: `awake`, `photos`, `dim`, `asleep`, `unknown` | |
| `sensor/backlight` (diagnostic) | kiosk | `backlight` 0-15 | |
| `sensor/room_light` (%) | kiosk | `room_light` | |
| `switch/screensaver`, `night_mode`, `motion_wake`, `smart_dim` (config) | kiosk | same name, `ON`/`OFF` | `<key>/set` `ON`/`OFF` |
| `number/day_level` (config, 1-15) | kiosk | `day_level` | `day_level/set` number |
| `button/wake` | kiosk | | `wake/press` any payload |
| `event/gesture` | kiosk | `gesture` JSON `{"event_type": "<2|3>_tap|<2|3>_swipe_<dir>"}`, not retained | |
| `sensor/privacy` (diagnostic, enum) | kiosk | `privacy`: `open`, `mic_locked`, `camera_locked`, `mic_and_camera_locked`; `privacy/attributes` JSON `{mic, camera, group}` | |
| `sensor/ring_alarm`, `event/ring_doorbell`, `sensor/lock_<name>`, `sensor/security_controls` | kiosk, only on the panel that owns Brilliant's Ring or Schlage integration | same name | |

`_N` suffixes appear for `gangbox_peripheral_N` with N ≥ 1 (the first gangbox has no suffix).
`load_power` is published in watts (the bus reports deciwatts). Panel rows are polled every second
for the bridge's own load and every `DIAG_SECS` (default 60) for the rest; kiosk display rows are
polled every second from `/run/ha-kiosk-display/` and `kiosk.env` + `overrides.env`.

**Plain-text notices for installers** (all retained under `<base>/`):

| Topic | Payload | Published by |
|---|---|---|
| `availability` | `online` on connect; `offline` as the will, and by the tool after `disable`, `uninstall`, `purge` | bridge, setup |
| `kiosk_state` | `active` on every connect; `disabled`, `not_installed` (also for `purge`), `failed` from the tool | bridge, setup |
| `discovery` | JSON `{"state": <as kiosk_state>, "device_id": "<full id, or the 12-character node suffix when the tool publishes>", "room": "<name>" or null, "host": "<LAN IPv4>"}`; `host` is omitted when unknown (always when the tool publishes). Keys sorted | bridge, setup |

The tool publishes with client id `ha_kiosk_setup` using `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`,
`MQTT_PASSWORD` from the saved settings and the base from `/var/lib/ha-kiosk/mqtt_base`; it is
best effort (a broker that is down never blocks an uninstall) and it publishes `failed` only when
the stock UI had to be restored (exit 6), not after a rollback (exit 5). The integration's
manifest subscribes to `brilliant_kiosk/+/kiosk_state` and `brilliant_kiosk/+/discovery` and
starts a discovery flow only for `state = active`.

**Not withdrawn.** Neither the bridge nor the tool clears the retained
`<prefix>/<component>/<node>/<key>/config` topics on `disable`, `uninstall` or `purge`; the
entities stay in Home Assistant as unavailable until the operator removes the MQTT device or
clears the retained topics. Switching to `KIOSK_MQTT=kiosk` is the only case where discovery is
withdrawn (panel rows).

### 0.12 Display control

The bridge handles these commands by writing `/var/lib/ha-kiosk/overrides.env` and restarting
`ha-kiosk-display.service`:

| Command topic | Payload | Written override |
|---|---|---|
| `<base>/screensaver/set` | `ON`/`OFF` | `SCREENSAVER="1"`/`"0"` |
| `<base>/night_mode/set` | `ON`/`OFF` | `NIGHT_MODE="1"`/`"0"` |
| `<base>/motion_wake/set` | `ON`/`OFF` | `MOTION_WAKE="day"`/`"off"` |
| `<base>/smart_dim/set` | `ON`/`OFF` | `SMART_DIM="1"`/`"0"` |
| `<base>/day_level/set` | number | `DAY_LEVEL="<1-15>"` |
| `<base>/wake/press` | any | none; one sync event is written to `/dev/input/event0` |

`overrides.env` is rewritten whole, sorted, as `KEY="value"` lines under a header comment, with
the tool's value rules (`safe_override`); unsafe lines already in the file are dropped on rewrite.
It is sourced by `ha-kiosk-display.sh` after `kiosk.env`, so it wins. The display script re-reads
configuration only when restarted, and its waits are up to `IDLE_DIM_SECS` long, so an override
takes effect at the restart, not before. There is no screen power command and no SSH display
command; `pybrilliantkiosk.Panel.set_env` purges an override when the same key is set in
`kiosk.env`.

### 0.13 Diagnostics

`diagnostics` returns the status object plus `logs`: the last 150 lines (`-o cat`) of
`journalctl -u ha-kiosk -u ha-kiosk-bridge -u ha-kiosk-display`, then the last 6000 characters of
`setup.log`, cut to the last 250 lines. Before output, every occurrence of `MQTT_PASSWORD` and the
token is replaced by `**redacted**`, and `Bearer <token>` / `access_token=<token>` patterns are
masked. Nothing else is scrubbed: hostnames, `HA_URL` and IP literals may appear. The integration
applies its own redaction on top (`custom_components/brilliant_kiosk/redact.py`).

### 0.14 How the Home Assistant integration uses this

For reference; the full behaviour is in [home-assistant-integration.md](home-assistant-integration.md).

* **Poll** (every 60 s): one SSH command that reads `/proc` facts, both `kiosk.env` copies with
  their SHA-256, `overrides.env`, and the files `status` reads (`current`, `state`, `previous`,
  `mqtt_base`, the job unit, heartbeat, last result). `Panel.poll` rebuilds the status object from
  them without starting the tool's interpreter (`setup.status_from_files`).
* **Install/update**: verify the bundle locally against the pinned SHA-256 and size, upload to a
  `mktemp -d /tmp/ha-kiosk-install.XXXXXXXX` directory (0700), `sha256sum` it on the panel,
  extract `ha-kiosk-setup` from it, run `install ... --detach`, then `wait --timeout 100` up to
  15 times. `env` and `token` are passed as files in that directory on a first install only.
* **Other commands**: `reapply`, `enable`, `disable`, `rollback`, `uninstall`, `purge` run with
  `--detach` + `wait`; `check-env -` validates settings text; `set_env` edits both copies of
  `kiosk.env` under the setup lock with the tool's rules.

### 0.15 Not implemented

Everything in sections 1 to 16 that is not listed above, in particular: signatures and
`release.json` assets; `version`/`display` commands; `--json`, `--config-merge`, `--mqtt`,
`--health-timeout`, `--no-activate`, `--require-tested-firmware`, `--allow-downgrade`; preflight
checks; `txn.json` and boot recovery units; a `rendered` flag in the heartbeat; narrowed MQTT
subscriptions in kiosk mode; discovery withdrawal on uninstall; `<base>/display/...` topics and
screen power; the `schema/` directory and per-key validators; a `run.env` and the end of shell
sourcing; redaction of URLs and IPs in diagnostics.

---

# Planned: contract 1

**Contract version:** 1 · **Status:** design draft (revision 3, 2026-11-09) · **Target:** `v2.0.0`
(a breaking change, so a new MAJOR). Items marked **TO VERIFY** are assumptions a single-panel
pilot must confirm (section 15).

## 1. Scope and roles

| Role | Responsibility |
|---|---|
| **Kiosk** (this repo) | Signed release bundles, the on-panel `ha-kiosk-setup` tool, the UI and its services, the health check, rollback, stock-UI restoration, kiosk-owned MQTT entities. |
| **Integration** | The pinned release table and trusted keys (the root of trust), download and verification, copying the bundle over SSH, running `ha-kiosk-setup`, providing config and token, an opt-in setting in its own UI, diagnostics collection, OTA re-apply, and the panel's non-kiosk MQTT entities. |
| **Operator** | Creates a dedicated non-admin HA user and its token, and per-panel MQTT credentials; opts each panel in; revokes credentials after uninstall. |

Out of scope for contract 1: auto-provisioning HA users, fleet scheduling, and `KIOSK_MODE=web`.
Contract-1 units drop the `web` branch and every `QTWEBENGINE_*` variable from `ha-kiosk.service`.

* **Current state:** the integration side exists (0.14) with a pinned table but no keys.
  `KIOSK_MODE=web` is still accepted and `ha-kiosk.service` still carries the QtWebEngine variables.

## 2. Release artifacts and trust

### 2.1 Artifacts

| Artifact | Name |
|---|---|
| Bundle | `brilliant-ha-kiosk-<version>.tar.gz` |
| Release manifest | `release.json` + `release.json.minisig` |
| Digest (convenience only) | `brilliant-ha-kiosk-<version>.tar.gz.sha256` |

The tarball adds `CONTRACT` and `schema/` (`config.v1.json`, `status.v1.json`) to the layout of 0.1.

```json
{ "name": "brilliant-ha-kiosk", "version": "v2.0.0", "contract": 1,
  "sha256": "<64 hex>", "size": 412345, "unpacked_size": 1900000,
  "min_version": "v2.0.0",
  "firmware": { "min": "<tbd>", "tested": ["<tbd>"] }, "arch": ["armv7l"] }
```

CI builds twice and fails if the digests differ.

* **Current state:** reproducible build and no-clobber upload are done (0.1). `release.json` is
  produced locally with four fields and not uploaded; no signature, `CONTRACT` or `schema/`.

### 2.2 Root of trust and keys

The **integration's pinned table** `{version: {sha256, size}}` is the root of trust. A valid
signature never admits a version that is not in the table; installing one requires an explicit
operator override in the integration UI.

`release.json` is signed with **minisign** (Ed25519). The integration ships its trusted public
key(s); `keys/` in this repo is informational. Rotation and revocation happen by an integration
release that changes its vendored keys and table. The signing job runs only on tags from protected
branches, in a GitHub environment with required reviewers, and passes the secret key to minisign on
stdin.

* **Current state:** the pinned table exists (`pinned.py`, v1.4.0). Nightlies are verified against
  the `.sha256` asset only and only when the nightly channel is chosen. No keys.

### 2.3 Verification

Integration side, before anything is copied:

1. Verify `release.json.minisig` with a vendored key.
2. Require `name == "brilliant-ha-kiosk"`, `version ==` the pinned version, and `contract` supported.
3. Stream-hash the bundle; require `sha256 == pinned == release.json.sha256` and `size` equal.

Panel side, `ha-kiosk-setup install --bundle-tar FILE --sha256 HEX`:

4. Copy `FILE` into a fresh `mktemp -d` under `/var/lib/ha-kiosk/incoming/` (0700), hash that copy,
   compare with `HEX`; exit 4 on mismatch.
5. Extract that same copy only, with `--no-same-owner --no-same-permissions`, rejecting absolute
   paths, `..`, symlinks, hardlinks, device and FIFO members, and any member outside
   `brilliant-ha-kiosk-<ver>/`. Then check `MANIFEST.sha256`.

* **Current state:** steps 3 to 5 exist in simpler form (0.8: hash and size check, member filter
  for absolute paths, `..` and non-file members, `MANIFEST.sha256`). No `incoming/`, no symlink
  or hardlink filter, no `release.json`.

## 3. `ha-kiosk-setup` CLI

Runs on the panel as root. A small `sh` shim that execs `/data/switch-embedded/env/bin/python -I`,
exits 3 if that Python is missing, and sets `umask 077` first.

```
ha-kiosk-setup install   --bundle-tar FILE --sha256 HEX
                         [--config FILE | --config-merge FILE | --config -]
                         [--token-file FILE | --token-stdin] [--layout FILE]
                         [--mqtt kiosk|full|off] [--health-timeout SECS] [--allow-offline]
                         [--require-tested-firmware] [--allow-downgrade] [--no-activate]
                         [--detach] [--json]
ha-kiosk-setup reapply   [--json]                 re-activate releases/<current>, no staging
ha-kiosk-setup enable    [--health-timeout SECS] [--allow-offline] [--detach] [--json]
ha-kiosk-setup disable | rollback | uninstall | purge   [--detach] [--json]
ha-kiosk-setup wait      [--timeout SECS] --json  wait for a detached transaction
ha-kiosk-setup status    [--json]                 lock-free
ha-kiosk-setup diagnostics --json                 lock-free (section 12)
ha-kiosk-setup display   get --json | set <function> <value>   (section 10)
ha-kiosk-setup version   --json                   {"tool_version": "...", "contracts_supported": [1]}
internal (not contract): recover, verify
```

Rules:

* **Version negotiation.** The integration first runs `/etc/ha-kiosk/ha-kiosk-setup version --json`.
  Missing file or exit 127 means not installed. If contract 1 is not listed, the integration offers
  only `install` (a newer bundle) or a manual purge.
* **No secrets in argv or environment.** The token comes from `--token-stdin` (preferred) or
  `--token-file` located in a `mktemp -d` under `/var/lib/ha-kiosk/incoming/`. `--config -` and
  `--token-stdin` together exit 2. The tool deletes `incoming/` on success, failure and in `recover`.
* **Config ownership.** `--config` replaces `kiosk.env` and clears `overrides.env` entries for keys it
  sets. `--config-merge` replaces only the keys present. Precedence: `overrides.env` > `kiosk.env`.
* **SSH drops.** Every state-changing command re-execs itself under
  `systemd-run --unit=ha-kiosk-setup-txn --collect` and waits, so SIGHUP does not stop it.
  `--detach` returns at once (exit 0, `pending_transaction` set); `wait` gives the final result.
  Worst case duration: preflight + 2 × health timeout + 30 s (≈ 6.5 min at the maximum timeout).
* **Locking.** `fcntl.flock` on `/etc/ha-kiosk/state/setup.lock` opened with `O_NOFOLLOW`; a second
  caller exits 6. `status` and `diagnostics` never take the lock.
* `--json` prints exactly one object matching `schema/status.v1.json` on stdout for every exit code
  except 2; logs go to stderr.
* `--allow-offline` is operator-only: the integration MUST ask for confirmation. It is recorded as
  `offline_install: true`.

### 3.1 Exit codes

These differ from the implemented codes in 0.4 (notably 6 and 8); a caller MUST check the
contract version before interpreting them.

| Code | Meaning | State after | Integration action |
|---|---|---|---|
| 0 | Success | as requested | — |
| 1 | Internal error | see `status` | show repair issue with diagnostics |
| 2 | Usage error | unchanged | bug in caller |
| 3 | Preflight failed; `last_error.reason` = `arch\|firmware\|space\|memory\|python\|path:<p>\|binary:<b>` | unchanged | show reason |
| 4 | Bundle invalid (digest, manifest, member, contract, below `min_version`) | unchanged | do not retry same bundle |
| 5 | Config or token invalid; `config_errors` lists all | unchanged | show errors |
| 6 | Lock held | unchanged | `wait`, then retry |
| 7 | Nothing to roll back to | unchanged | — |
| 8 | Not installed (`enable`/`disable`/`rollback`/`reapply`) | unchanged | offer install |
| 10 | Activation failed; rolled back to `previous`, healthy | `rolled-back` | report |
| 11 | Activation failed; stock UI restored | `failed` | republish own display entities (9.4) |
| 12 | Stock-UI restoration failed | `failed` | repair issue; recommend reboot |
| 13 | Stock UI restored by boot recovery; `reason` = `integrity\|crash-loop\|firmware` | `failed` | republish own display entities; report |

### 3.2 Status JSON

The authority is `schema/status.v1.json`. Fields (only ever added within contract 1):

`contract`, `tool_contract`, `tool_version`, `generated_at`, `boot_id`, `state`
(`not-installed|installed-disabled|active|failed|rolled-back`), `current`, `previous` (or `null`),
`releases[] {version, state: current|previous|bad, sha256}`, `stock_ui {enabled, active}`,
`health {rendered, ha_connected, layout_source, layout_error, heartbeat_age_s, checked_at}`,
`services {...}` (only units whose file exists, incl. `ha-kiosk-swap`), `mqtt {mode, node, base,
discovery_prefix, device_id, connected}`, `discovery_topics[]`, `discovery_cleanup_pending[]`,
`listeners[] {proto, port, bind, allow}`, `token_present`, `secret_files[]`, `config_sha256`
(normalised), `config_errors[]`, `firmware {version, supported, tested}`, `offline_install`,
`ha_url_cleartext_nonlocal`, `pending_transaction`, `last_error {code, reason, at}`.

`last_error` never contains config values.

* **Current state:** the commands, options, exit codes and status object of section 0 (0.3 to
  0.6). JSON is always printed (no `--json`). `install-local.sh` remains the file installer
  underneath and keeps its own options (MANUAL.md, section 8).

## 4. On-panel layout and transactions

### 4.1 Layout

| Path | Contents | Mode |
|---|---|---|
| `/etc/ha-kiosk/releases/<ver>/` | One release: code **and** its own `systemd/` units and drop-in | dirs 0555, files 0444 (0555 executables), root |
| `/etc/ha-kiosk/current`, `previous` | Symlinks to `releases/<ver>` | root |
| `/etc/ha-kiosk/kiosk.env`, `token` | Validated config, HA token | 0600 root |
| `/etc/ha-kiosk/run.env` | Normalised config rendered by setup for systemd (7) | 0600 root |
| `/etc/ha-kiosk/ha-kiosk-setup` | Copy of the tool from `current` | 0555 root |
| `/etc/ha-kiosk/state/` | `txn.json`, `state.json` (release digests, `min_version` floor, boot-guard counter), `setup.lock` | 0700 root |
| `/var/lib/ha-kiosk/` | `layout.json`, `layout-cache.json`, `overrides.env`, `photos/`, `ha-photos/` (capped by `PHOTO_SYNC_MAX_MB`), `incoming/` (0700) | 0700 root |
| `/run/ha-kiosk/` | `health.json` (written by the bridge, 5) | 0700 |
| `/run/ha-kiosk-display/`, `/run/ha-kiosk-shadow/` | Display state; dummy backlight for Brilliant | — |
| `/etc/systemd/system/…` | Units and `message_bus.service.d/ha-kiosk-display.conf`, copied from the target release | — |

State lives under `/etc` so that a full `/var` (photos) cannot break transactions or boot recovery.
All units get `ProtectSystem=strict` with explicit `ReadWritePaths=`. Running `qmlscene` as an
unprivileged user is desirable but needs framebuffer, GPU and input access (TO VERIFY).

* **Current state:** the layout of 0.7: releases and saved config under `/var/lib/ha-kiosk`, the
  running copy flat in `/etc/ha-kiosk` with code, config and `photos/` mixed, paths hardcoded in
  the services and units, units without `ProtectSystem`. Two releases are kept.
* **Migration:** the first contract-1 install moves user data out, deletes the flat code, and keeps
  **no** `previous` (the legacy tree is unsigned and shell-sources config). A failed first install
  exits 11.

### 4.2 States

| State | Meaning |
|---|---|
| `not-installed` | No `current`. Stock UI runs. |
| `installed-disabled` | Release present; kiosk units disabled; drop-in absent; stock UI enabled and running. |
| `active` | Kiosk units enabled and running; stock UI disabled; drop-in present. |
| `failed` | Stock UI restored after a failed activation (11, 13) or restoration failed (12). |
| `rolled-back` | Behaves like `active`; `last_error` explains. Cleared by the next successful `install`/`enable`. |

**Invariant**, enforced by `recover` at every boot before `message_bus` starts: drop-in present ⇔
`ha-kiosk.service` enabled ⇔ `switch_ui_app` disabled.

* **Current state:** the states of 0.5 (`not_installed`, `unmanaged`, `active`, `disabled`,
  `failed`, `in_progress`), spelled with underscores; `rolled_back` is an exit code, not a state.
  No invariant check at boot.

### 4.3 Transactional install

Each step is written to `state/txn.json` (temp file, `fsync`, `rename`, `fsync` of the directory)
before it runs.

1. **Lock**, else exit 6.
2. **Preflight** (exit 3, nothing changed): arch `armv7l`; firmware ≥ `firmware.min` (not in
   `tested` is a warning, or exit 3 with `--require-tested-firmware`), read from the same
   `current_release_tag` that `ha-kiosk-mqtt.py` reports as `firmware_version`; present:
   Brilliant Python, `/usr/bin/qmlscene`, `message_bus.service`, `switch_ui_app.service`,
   `/sys/class/backlight/backlight_lcd`, `/dev/input/event0`, `tar`, `systemd-run`; free space on
   `/etc` ≥ `unpacked_size` × 2 + 5 MB, and 64 MB on `/var`; `MemAvailable` + RSS of
   `switch_ui_app` ≥ 120 MB. Thresholds TO VERIFY.
3. **Verify** bundle (2.3; also `version` ≥ the recorded `min_version` floor unless
   `--allow-downgrade`), config and token (exit 4 / 5).
4. **Stage** into `releases/<ver>.tmp`, set modes, `fsync`, rename. Disk full: `.tmp` removed, exit 3.
5. **Write** config, `run.env` and token as `*.new` (`O_CREAT|O_EXCL`, 0600), `fsync`, rename; old
   copies kept as `*.prev` until commit.
6. **Withdraw** handled by the integration beforehand (9.4).
7. **Switch**: `previous` ← old `current` (only if it is a verified release); `current` ←
   new via `os.symlink` to `current.new` + `os.replace`.
8. **Activate**: install units and drop-in from `current/systemd/`, `daemon-reload`, restart
   `message_bus` only if the drop-in changed, disable and stop `switch_ui_app`, delete
   `/run/ha-kiosk/health.json`, enable and start kiosk units.
9. **Health check** (5).
10. **Commit**: delete `*.prev` and `incoming/`, prune releases other than `current`/`previous`,
    record the release digest and raise the `min_version` floor in `state.json`, create swap
    (after health, not before), state `active`.

**On failure at 9:** if `previous` exists, re-verify its tree against `MANIFEST.sha256` and the
digest in `state.json`, switch back, restore `*.prev`, activate, health-check → exit 10. Otherwise,
or if that fails, **restore the stock UI**: disable and stop `ha-kiosk*`, remove the drop-in,
`daemon-reload`, restart `message_bus`, `pkill -x fbi`, enable and start `switch_ui_app` → exit 11.
Exit 12 if 15 s later `switch_ui_app` is not active, `ha-kiosk.service` is not disabled, the drop-in
still exists, or `message_bus` was not restarted after the drop-in was removed.

**Boot recovery** is split, because a unit ordered before the kiosk cannot wait for its heartbeat:

* `ha-kiosk-recover.service` (oneshot, `Before=message_bus.service ha-kiosk.service
  switch_ui_app.service`) runs `ha-kiosk-setup recover`: filesystem repair only. Deletes `*.new`,
  `*.tmp`, `incoming/`; if `txn.json` is unfinished, before step 7 leaves the old state, from step 7
  on switches back to `previous` and `*.prev` (or marks stock UI); an unreadable `txn.json` falls back
  to the last committed `state.json`, and if that is unreadable too, to stock UI. Re-installs units
  from the target release and enforces the 4.2 invariant.
* `ha-kiosk-verify.service` (`After=ha-kiosk.service`) runs the health check with `rendered` only.
  Boot guard: if `rendered` is not seen within 180 s on two consecutive boots, it restores the stock
  UI with code 13 `crash-loop`.

Re-running `install` after any interruption is always safe.

* **Current state:** steps 1, 3 (digest, size, manifest), 4 (staging directory, plain rename), 5
  (temp file + rename, no `fsync`), 7, 8 (through `install-local.sh`), 9 (heartbeat health check),
  10 (prune only), and the failure path (rollback to `previous`, else stock UI) exist as described
  in 0.8. No `txn.json`, preflight, `min_version`, boot recovery or exit 12 check.

## 5. Health check

Within `--health-timeout` (default 60 s, max 180 s) of step 8:

1. `ha-kiosk`, `ha-kiosk-display`, `ha-kiosk-bridge` are `active` and `ha-kiosk`'s `NRestarts` has
   not changed;
2. `/run/ha-kiosk/health.json` exists with `version` equal to `current`, `rendered: true`, and
   (install/enable only, unless `--allow-offline`) `ha_connected: true`;
3. its `ts` is later than both the activation time and `ExecMainStartTimestamp` of `ha-kiosk`, and
   no older than 15 s. A JSON parse error means "retry next poll".

**How the heartbeat is written.** A `Timer` in `main.qml` POSTs `{rendered, ha_connected,
layout_source}` every 10 s to the bridge's localhost HTTP server. `rendered` becomes true on the
first `Window.frameSwapped` after the layout is set (TO VERIFY that the Qt version on the image
supports `frameSwapped` from QML). The bridge adds `version` and `ts` and writes the file
atomically. The layout cache moves to the bridge as well, and `QML_XHR_ALLOW_FILE_WRITE` is dropped.

Outside an operator-initiated install/enable, `ha_connected: false` never triggers rollback or
stock-UI restore; it is reported in `status`.

* **Current state:** the heartbeat of 0.8: `main.qml` POSTs `{"ha": <status>}` every 10 s, the
  bridge writes `/run/ha-kiosk/heartbeat.json` = `{"time", "ha"}`; the check is heartbeat newer
  than activation, `ha-kiosk.service` active, `ha == "connected"` unless `--allow-offline`; fixed
  60 s. No `rendered`, `version` or `NRestarts`. `ha-kiosk.service` still sets
  `QML_XHR_ALLOW_FILE_WRITE=1` for the layout cache.

## 6. Disable, rollback, uninstall, purge

| Item | disable | rollback | uninstall | purge |
|---|---|---|---|---|
| Kiosk discovery and `offline` availability | withdrawn first | kept | withdrawn first | withdrawn first |
| Kiosk units | stopped, disabled | restarted on previous | removed | removed |
| `message_bus` drop-in, tmpfiles conf | removed, `message_bus` restarted | from previous | removed | removed |
| Stock UI | enabled + started | stopped | enabled + started | enabled + started |
| Voice firewall rule | removed | kept | removed | removed |
| `releases/`, `current`, `previous` | kept | `previous` → `current`, `previous` = null | removed | removed |
| `token`, `token.prev`, `token.new` | kept | kept | **deleted** | deleted |
| `kiosk.env`, `run.env` and `*.prev`/`*.new`, `incoming/` | kept | kept | **deleted** | deleted |
| `overrides.env` | kept | kept | deleted | deleted |
| `layout.json`, own `photos/` | kept | kept | kept | deleted |
| Synced HA photos, layout cache, swap | kept | kept | deleted | deleted |
| `state/`, `/etc/ha-kiosk/ha-kiosk-setup` | kept | kept | deleted last | deleted |

Deletion is best effort on eMMC, so the integration MUST prompt the operator to revoke the HA
token and the panel's MQTT credentials after uninstall. The `uninstall`/`purge` JSON output includes
`discovery_cleanup_pending[]`, which the integration MUST act on.

Turning the kiosk off in an integration maps to `uninstall`. `disable` is for temporary hand-back.

* **Current state (0.3, 0.11):** `disable` keeps releases, settings and token and publishes
  `kiosk_state=disabled` + `availability=offline`; `rollback` swaps `current` and `previous`
  (`previous` is not nulled); `uninstall` deletes every secret and verifies that, keeps
  `overrides.env` and own photos in `/var/lib/ha-kiosk-keep` but **not** `layout.json`; `purge`
  removes the keep directory too; `install-local.sh --uninstall --purge` removes units, drop-in,
  tmpfiles conf, swap and `/run/ha-kiosk-shadow`. Retained discovery is never withdrawn and
  voice/capture firewall rules are removed by the units' `ExecStopPost`.

## 7. Configuration

`/etc/ha-kiosk/kiosk.env`: ASCII, one `KEY=value` per line, `#` comments only at line start, blank
lines allowed, max 16 KB.

* Key matches `^[A-Z][A-Z0-9_]{0,63}$` and is in `schema/config.v1.json`, which MUST list every key
  the code reads (a CI test greps `env.get`, `ENV.get`, `os.environ` and `${VAR` against it).
* Value: everything after the first `=`, literally. Forbidden anywhere in a value: bytes outside
  0x20–0x7E, `\`, `"`, `'`, backtick, `$`. No quote stripping.
* Duplicate keys are an error.
* Validators are anchored full matches.

**Invariant:** no value is ever interpolated into a shell command line. Values reach programs only
as argv elements, environment variables, or JSON. The file is **never shell-sourced**.
`ha-kiosk-setup` renders the validated config to `run.env`; shell services read values through a
Python helper that prints single-quote-escaped assignments. QML reads `run.env` with the same rules.

`overrides.env` accepts only `SCREENSAVER`, `NIGHT_MODE`, `MOTION_WAKE`, `SMART_DIM`, `DAY_LEVEL`,
with the same validators, and is rate-limited to one write per second.

Validators (excerpt; `config.v1.json` is the authority and also marks each key `diag: safe` or not):

| Key | Rule |
|---|---|
| `HA_URL` | `http(s)://host[:port]`, host = DNS name, IPv4 or `[IPv6]`; no path. `http` to a non-RFC1918/non-ULA host is accepted with a warning and `ha_url_cleartext_nonlocal: true` |
| `HA_TLS_VERIFY` | `0`/`1`, default `1` |
| `KIOSK_MODE` | `native` (`web` → exit 5 "not supported in contract 1") |
| `KIOSK_MQTT` | `kiosk`, `full`, `off` (`1`/`0` accepted as `full`/`off`) |
| `MQTT_HOST` | DNS name, IPv4 or IPv6 literal, or empty |
| `*_PORT` (`MQTT`, `VOICE`, `INTERCOM`, `BRIDGE`) | integer 1–65535 |
| `MQTT_USER`, `MQTT_PASSWORD` | printable per the rules above (`#` allowed), ≤ 128 / 256 |
| `MQTT_BASE`, `MQTT_DISCOVERY_PREFIX` | `^[A-Za-z0-9_-]{1,64}(/[A-Za-z0-9_-]{1,64})*$` |
| `MQTT_DEVICE_ID` | `^[A-Za-z0-9_-]{1,64}$` |
| `VOICE_BIND`, `INTERCOM_BIND` | IPv4 literal, not `0.0.0.0`, not loopback |
| `VOICE_ALLOW` | 1–16 comma-separated IPv4 addresses or CIDRs; required when `KIOSK_VOICE=1` |
| `CPU_*_GOV` | enum of governors present in `/sys` |
| `TOUCH_DEVICE` | `^/dev/input/event[0-9]+$` |
| `SUN_*_ELEV` | integer −90–90 |
| `*_LEVEL` | integer 0–15 |
| `*_SECS`, `*_MINS`, `SWAP_MB`, `SWAPPINESS`, `LUX_*`, `PHOTO_SYNC_MAX_MB` | integer in a per-key range |
| `SLEEP_START`, `SLEEP_END` | `HH:MM` |
| boolean keys | `0`/`1` |
| `MOTION_WAKE` | `day`, `always`, `off` |
| entity keys (`LOAD_ENTITY`, `LIGHT_SENSOR`, `ROOM_*`) | `^[a-z_]+\.[a-z0-9_]+$`, comma-separated where plural |
| `GESTURE_*` | `load.toggle\|load.on\|load.off\|page:<n>\|<domain>.<service>[:<entity_id>]` |
| `PHOTO_DIRS` | under `/var/lib/ha-kiosk/photos` or Brilliant media subtrees; `realpath` must not leave them; symlinks not followed |

**Token.** `/etc/ha-kiosk/token`, 0600, one line, `^[A-Za-z0-9._-]{20,1024}$`; invalid input is
rejected (exit 5), not cleaned. The MQTT password stays in `kiosk.env` in contract 1.

* **Current state (0.9, 0.10):** allowlist, character rules, length 1024, quote stripping, last
  assignment wins, three cross-field checks, `KEY="value"` rendering. `kiosk.env` and
  `overrides.env` are still sourced by `ha-kiosk-display.sh`/`ha-kiosk-swap.sh` as root and read by
  systemd `EnvironmentFile=`; the rendering makes that safe. Token rule `{20,4096}`. No per-key
  validators, no `run.env`, no 16 KB cap, duplicates allowed, no `diag: safe` marking,
  `overrides.env` not rate-limited.

## 8. Home Assistant token

Contract 1 uses an operator-created long-lived token for a **dedicated, non-admin, local-only** HA
user. HA tokens have no scopes: a non-admin token can still read every entity and call any service.
Panel compromise equals that user's power. `GESTURE_*` service calls also run with it.

| API | Where | Non-admin |
|---|---|---|
| WS `auth`, `get_states`, `subscribe_events` `state_changed` | `Ha.qml` | Expected yes; TO VERIFY |
| WS `subscribe_events` `lovelace_updated` | `Ha.qml` | TO VERIFY |
| WS `lovelace/config` | `main.qml` | TO VERIFY; dashboards with `require_admin` will fail |
| WS `call_service` (tiles, gestures, `weather.get_forecasts`) | QML | Expected yes; TO VERIFY |
| WS `media_player/browse_media` | `MusicPage.qml` | TO VERIFY |
| WS `media_source/browse_media`, `resolve_media` | `ha-kiosk-photos.py` | TO VERIFY |
| REST `GET /api/states/<entity>` | `ha-kiosk-ambient.py` | Expected yes |
| TTS / media fetch | `ha-kiosk-voice.py` | TO VERIFY |

If `lovelace/config` is denied, the UI uses the cached or file layout: `rendered` stays true,
`layout_source: cache`, `layout_error: forbidden`. Polling fallback: re-read `lovelace/config` on
reconnect, every `LAYOUT_POLL_SECS` (default 300) comparing a hash, and on `…/layout/reload`.

* **Current state:** the token type is not enforced; the table is unverified. `ha-kiosk-voice.py`
  attaches the token only when scheme, host and port all match `HA_URL`.

## 9. MQTT and network

### 9.1 Modes

| `KIOSK_MQTT` | Publishes |
|---|---|
| `off` | Nothing. |
| `kiosk` | Only kiosk-owned entities (9.3). **Required for the integration.** |
| `full` | The built-in device, for standalone use. |

In `kiosk` mode the bridge subscribes an explicit list of command topics (no wildcard, no
`load/set`).

* **Current state (0.11):** the three modes exist, spelled `0`/`1`/`kiosk` (`full` and `off` are
  not accepted by the validator). The subscription is `<base>/+/set` in every mode. The
  `brilliant_kiosk` integration works with either provider (its `mqtt_provider` option).

### 9.2 Namespace and device

* Base `<MQTT_BASE>/<node>`, `MQTT_BASE` default `brilliant_kiosk`,
  `node = brilliant_<first 12 chars of the device id>`; reported in `status.mqtt`.
* Discovery `<prefix>/<component>/<node>/<key>/config`, retained, `unique_id = <node>_<key>`. The
  integration MUST NOT use the `brilliant_<12>_` prefix, and the kiosk publishes nothing outside its
  node.
* With `MQTT_DEVICE_ID` set, the kiosk's `device` block contains only `identifiers`.
* The kiosk publishes retained `<base>/kiosk_state` (the status `state`) and retained
  `<base>/discovery` JSON `{"state", "host", "device_id", "room"}`.

* **Current state:** implemented as written (0.11); `status.mqtt` has `mode` and `base` only.

### 9.3 Entity ownership in `kiosk` mode

| Entity | Published by kiosk |
|---|---|
| Display state, backlight level, screen power, brightness, screensaver, night mode, motion wake, smart dim, wake | yes (section 10) |
| Room light level, gesture event, layout name and reload | yes |
| Voice (if `KIOSK_VOICE=1`), intercom (if `KIOSK_INTERCOM=1`) | yes; voice volume is suppressed if the integration exposes `output_volume` |
| Wired light, load power, temperatures, motion, firmware, boot, calibration, HomeKit, Shelly BLE | **no** (integration) |

* **Current state:** the ownership split of 0.11 (no screen power, brightness, layout name or
  reload entities; privacy and Ring/Schlage entities are kiosk-owned).

### 9.4 Discovery hand-off

Integration display entities (screen power, brightness, motion wake, screensaver) are published
**iff** `status.state ∈ {not-installed, installed-disabled, failed}`. The integration re-evaluates
after every command result and on changes of `<base>/kiosk_state` or `<base>/availability`.

1. Before `install`/`enable`, the integration withdraws its display discovery.
2. On start in `kiosk` mode, the kiosk clears any retained `full`-mode topics under its node.
3. `disable`/`uninstall`/`purge` run `ha-kiosk-mqtt.py --withdraw` before stopping units: empty
   retained payloads for every topic in `discovery_topics` (including voice and intercom) and
   `offline` availability, with a timeout. Topics not cleared go to `discovery_cleanup_pending`.
4. After exit 11, 12 or 13, or a stock-UI state at boot, the integration republishes its display
   discovery.

* **Current state:** step 2 exists. The `brilliant_kiosk` integration publishes no MQTT entities
  of its own. No `--withdraw`.

### 9.5 Network listeners and broker ACL

| Listener | Bind | Firewall | Allowed | Auth |
|---|---|---|---|---|
| Bridge HTTP `BRIDGE_PORT` (8765) | 127.0.0.1 | none needed | local processes | none (root-only box) |
| Voice Wyoming TCP `VOICE_PORT` (10700) | `VOICE_BIND` (default: LAN address toward HA) | runtime `nft insert rule inet firewall filter-input`, removed on stop (`--close-firewall`) | `VOICE_ALLOW` (default: HA host) | none |
| Capture clip server TCP `CAPTURE_PORT` (10720) | `CAPTURE_BIND` (default: LAN address toward HA) | runtime `nft` rule as above | `CAPTURE_ALLOW` (default: HA host) | per-clip token in the URL |
| Capture local control TCP `CAPTURE_PORT+1` | 127.0.0.1 | — | local | none |
| Intercom UDP `INTERCOM_PORT` (10710) | `INTERCOM_BIND` (default: LAN address toward the broker) | TO VERIFY whether Brilliant's ruleset admits it | source IPs in retained `<base>/intercom/peers/#` | **none; LAN trust only** |
| Intercom control TCP `INTERCOM_PORT+1` | 127.0.0.1 | — | local | none |

Required broker ACL (template to ship in the docs): each panel's user reads and writes only
`<base>/<node>/#`, `<base>/intercom/peers/<node>`, and its own discovery topics, and reads
`<base>/intercom/peers/#`; the integration's user may write `<base>/<node>/display/+/set`. Without
per-panel credentials, any broker client can register an intercom peer or blank screens.
`status.listeners[]` and diagnostics report open ports.

* **Current state:** the listeners are as in the table (defaults from `kiosk/ha-kiosk-*.py`).
  `status` does not report them. MANUAL's "nothing new is exposed" holds only with voice,
  intercom and capture off.

## 10. Display control for the integration

The kiosk owns the backlight while active (Brilliant's writes go to `/run/ha-kiosk-shadow`), so the
integration MUST control the screen through this interface. While `state` is not active or
rolled-back, the kiosk answers nothing and the integration owns these controls.

**MQTT** under `<base>/<node>/display/…`: commands QoS 1, not retained; states retained. Every
command produces a state echo within 2 s, or `display/error` with `{"cmd", "reason"}`.

| Function | Command (payload) | State |
|---|---|---|
| Screen power | `power/set` `ON`/`OFF` (OFF = asleep until touch, allowed motion, or ON) | `power` |
| Brightness | `brightness/set` 1–15 | `brightness` 0–15 |
| Motion wake | `motion_wake/set` `day`/`always`/`off` | `motion_wake` |
| Screensaver | `screensaver/set` `ON`/`OFF`; `screensaver/start` | `screensaver` |
| Wake | `wake` | — |
| Display state | — | `state` (`awake`/`photos`/`dim`/`asleep`) |

**SSH:** `ha-kiosk-setup display get --json` and `display set <function> <value>`, which call the
bridge's localhost HTTP server (`POST /display`).

Settings persist in `overrides.env` (7); `power`, `wake`, `screensaver/start` are transient. The
display loop is rewritten so every wait is ≤ 2 s and it applies commands from
`/run/ha-kiosk-display/cmd`, or moved to Python with `select` on touch input and a control pipe.
Old topics stay as aliases in contract 1.

* **Current state:** the topics of 0.12 with restart semantics; no screen power, no SSH display
  command, no error topic.

## 11. OTA

* **Current state (UNVERIFIED):** MANUAL says OTA replaces the read-only image and keeps `/etc` and
  `/var`. Not confirmed; nor whether OTA re-enables `switch_ui_app` or changes `message_bus.service`.
  `reapply` exists for the case where `/etc` is rebuilt; releases and saved settings are under `/var`.
* **Planned:**
  * At boot, `recover` (4.3) checks the `current` tree against `MANIFEST.sha256` and the digest in
    `state.json` (mismatch → stock UI, code 13 `integrity`), and firmware against `firmware.min`
    (below → stock UI, code 13 `firmware`). If state is `active` but units, drop-in or the
    `switch_ui_app` state are wrong, it runs `reapply`.
  * The integration keeps the desired state (enabled, pinned version) and cached verified bundle.
    After a firmware change or reboot it runs `status`; exit 127 or a missing tool with kiosk
    desired → `install` from the cached bundle.
  * Pilot: one OTA with the kiosk active, recording surviving paths and `systemctl cat
    message_bus` before and after.

## 12. Diagnostics

`ha-kiosk-setup diagnostics --json`, lock-free, ≤ 256 KB, `state: not-installed` on a bare panel:

* `status`; `VERSION`/`CONTRACT` of each release; `txn.json` step names only;
* config by **allowlist**: keys marked `diag: safe` with values, others as `{"set": true, "len": n}`;
  entity ids hashed with a per-panel salt;
* `journalctl -u 'ha-kiosk*'` last 200 lines, with every output field scrubbed by: JWT
  `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`, `Bearer \S+`, `authSig=[^&\s]+`, literal
  search-and-replace of the token, MQTT password and the values of `HA_URL`, `MQTT_HOST`,
  `VOICE_BIND`, `VOICE_ALLOW`, `INTERCOM_BIND`, then IP literals;
* health, display state, firmware, uptime, memory, disk, listeners, `switch_ui_app` state.

Excluded: photos, layout content, entity lists.

Logging rule for all services: never log full URLs with query strings; strip `authSig=`.

* **Current state (0.13):** status plus 250 log lines of three units and `setup.log`, with the
  token, `MQTT_PASSWORD` and `Bearer`/`access_token` patterns redacted; nothing else. `Ha.qml`
  logs `base=<HA_URL>`, MQTT logs the broker host, voice and photos log fetched URLs that can carry
  signed `authSig` links. `scripts/logs.sh` is unredacted; the integration's `logs` action is
  redacted on the HA side.

## 13. Compatibility

* Contract version is an integer in `CONTRACT` and `release.json`. Within a contract, commands,
  flags, exit codes, JSON fields, config keys and topics are only added.
* A MAJOR release accompanies a contract bump; each release supports one contract.
* Downgrades within a contract need `--allow-downgrade` and are always refused below the
  `min_version` floor recorded on the panel.
* Deprecated aliases (`install-local.sh`, old topics, `KIOSK_MQTT=1/0`) stay for one MAJOR.

* **Current state:** the status object carries `schema: 1`; nothing else of this section. The
  integration refuses downgrades on its own side (`pybrilliantkiosk.versions.is_newer`); the tool
  accepts any `VERSION`.

## 14. Phasing

1. **Installer, security, rollback:** sections 2–8 (signing, `ha-kiosk-setup` shim and `--json`,
   release layout and path refactor, transactions, boot recovery, `rendered` heartbeat, config
   schema, uninstall table, non-admin token).
2. **MQTT and display:** sections 9–10 (`full`/`off` spellings, narrowed subscriptions, withdraw,
   display loop rewrite).
3. **Single-panel pilot.**

## 15. Pilot checklist

* Offline wired-load operation with HA and the broker down.
* Power cut at each step of 4.3; SSH drop mid-install; disk full during staging.
* Uninstall: `secret_files` empty, discovery withdrawn, stock UI owns the backlight.
* `message_bus` restart effect on dependents and the wired load.
* OTA: surviving paths, `switch_ui_app` state, `message_bus.service` unit.
* Non-admin token table (8), including `lovelace_updated` and `require_admin` dashboards.
* Preflight thresholds, firmware version source, Qt `frameSwapped`, unprivileged `qmlscene`.
* `nft list ruleset` for the intercom port.

## 16. Open questions for the integration

1. Is `--allow-offline` acceptable for production installs, or must HA be reachable?
2. Speaker volume owner when both sides can expose `output_volume`.
3. MQTT password in its own file in contract 1 rather than 2?
4. Minimum HA version (for `weather.get_forecasts` with `return_response`).
5. Will the integration provide per-panel MQTT credentials and apply the 9.5 ACL template?
6. Default `--health-timeout` and the SSH timeout the integration will use (3).
