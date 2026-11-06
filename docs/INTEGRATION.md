# Integration contract for external installers

**Contract version:** 1 · **Status:** draft for review (revision 2) · **First release:** `v2.0.0`
(contract 1 is a breaking change, so it starts a new MAJOR)

This document defines the interface between the HA kiosk and tools that install and manage it on
Brilliant panels, such as a Home Assistant integration that deploys components over SSH. Anything
not written here is internal and may change without notice.

Markers:

* **Current state:** what the code in this repository does today.
* **Planned:** what contract 1 requires. Nothing marked Planned exists yet.
* **TO VERIFY:** an assumption the single-panel pilot must confirm (section 15).

MUST, SHOULD and MAY are used as in RFC 2119.

---

## 1. Scope and roles

| Role | Responsibility |
|---|---|
| **Kiosk** (this repo) | Signed release bundles, the on-panel `ha-kiosk-setup` tool, the UI and its services, the health check, rollback, stock-UI restoration, kiosk-owned MQTT entities. |
| **Integration** | The pinned release table and trusted keys (the root of trust), download and verification, copying the bundle over SSH, running `ha-kiosk-setup`, providing config and token, an opt-in (default-off) setting in its own UI, diagnostics collection, OTA re-apply, and the panel's non-kiosk MQTT entities (load, power, motion, temperatures, Brilliant diagnostics). |
| **Operator** | Creates a dedicated non-admin HA user and its token, and per-panel MQTT credentials; opts each panel in; revokes credentials after uninstall. |

Out of scope for contract 1: auto-provisioning HA users, fleet scheduling, and `KIOSK_MODE=web`.
Contract-1 units drop the `web` branch and every `QTWEBENGINE_*` variable from `ha-kiosk.service`,
so no config value can start an unsandboxed root browser.

---

## 2. Release artifacts and trust

### 2.1 Artifacts

| Artifact | Name |
|---|---|
| Bundle | `brilliant-ha-kiosk-<version>.tar.gz` |
| Release manifest | `release.json` + `release.json.minisig` |
| Digest (convenience only) | `brilliant-ha-kiosk-<version>.tar.gz.sha256` |

`<version>` is `vMAJOR.MINOR.PATCH`. Pre-releases are used only when pinned explicitly. The tarball
has one top-level directory `brilliant-ha-kiosk-<version>/` with `ha-kiosk-setup`,
`install-local.sh`, `VERSION`, `CONTRACT`, `MANIFEST.sha256`, `ha-kiosk/`, `systemd/`,
`schema/` (`config.v1.json`, `status.v1.json`), `kiosk.env.example`, `LICENSE`.

```json
{ "name": "brilliant-ha-kiosk", "version": "v2.0.0", "contract": 1,
  "sha256": "<64 hex>", "size": 412345, "unpacked_size": 1900000,
  "min_version": "v2.0.0",
  "firmware": { "min": "<tbd>", "tested": ["<tbd>"] }, "arch": ["armv7l"] }
```

Builds are reproducible: `tar --sort=name --mtime=<commit date> --owner=0 --group=0
--numeric-owner | gzip -n`. CI builds twice and fails if the digests differ. Release assets are
never replaced: the workflow fails if an asset already exists.

* **Current state:** `scripts/build-bundle.sh` uses a plain `tar -czf` (digest changes on every
  rebuild). `.github/workflows/release.yml` no longer uses `--clobber`, so an existing asset is
  never replaced. No signature, `CONTRACT`, `schema/` or `release.json`.
* **Planned:** all of 2.1.

### 2.2 Root of trust and keys

The **integration's pinned table** `{version: {sha256, size}}` is the root of trust. A valid
signature never admits a version that is not in the table; installing one requires an explicit
operator override in the integration UI.

`release.json` is signed with **minisign** (Ed25519). The integration ships its trusted public
key(s) in its own release; `keys/` in this repo is informational. Rotation and revocation happen by
an integration release that changes its vendored keys and table. The signing job runs only on tags
from protected branches, in a GitHub environment with required reviewers, and passes the secret key
to minisign on stdin, never in argv or logs.

### 2.3 Verification

Integration side, before anything is copied:

1. Verify `release.json.minisig` with a vendored key.
2. Require `name == "brilliant-ha-kiosk"`, `version ==` the pinned version, and `contract` supported.
3. Stream-hash the bundle; require `sha256 == pinned == release.json.sha256` and `size` equal.

Panel side, `ha-kiosk-setup install --bundle-tar FILE --sha256 HEX` (the digest is not secret, so
argv is fine):

4. Copy `FILE` into a fresh `mktemp -d` under `/var/lib/ha-kiosk/incoming/` (0700), hash that copy,
   compare with `HEX`; exit 4 on mismatch.
5. Extract that same copy only, with `--no-same-owner --no-same-permissions`, rejecting absolute
   paths, `..`, symlinks, hardlinks, device and FIFO members, and any member outside
   `brilliant-ha-kiosk-<ver>/`. Then check `MANIFEST.sha256`.

The tool that runs `install` is the copy at `/etc/ha-kiosk/ha-kiosk-setup` if present, else the one
just extracted from the verified tarball. There is no `--bundle DIR`.

---

## 3. `ha-kiosk-setup` CLI

Runs on the panel as root. A small `sh` shim that execs `/data/switch-embedded/env/bin/python -I`
(the interpreter the bridge already needs), so locking, atomic renames, `fsync` and JSON do not
depend on BusyBox. If that Python is missing, the shim exits 3 using `sh` only. It sets `umask 077`
first.

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

| Command | Effect |
|---|---|
| `install` | Preflight, verify, stage, switch, activate, health-check; on failure roll back or restore the stock UI (4.3). Same version: re-applies config and units, never rotates `previous`. |
| `reapply` | Re-installs units, drop-in and config for `current` and activates it. Used after OTA. |
| `enable` | Activate `current` and health-check it. |
| `disable` | Withdraw kiosk discovery, stop and disable kiosk units, restore stock UI. Keeps releases, config, token. |
| `rollback` | `current` ← `previous`, activate, health-check. Afterwards `previous` is `null` and the rolled-from release is marked `bad`. |
| `uninstall` | `disable`, then remove code, units and every secret (6). Keeps non-secret user data. |
| `purge` | `uninstall`, then remove everything the kiosk created. |

Rules:

* **Version negotiation.** The integration first runs `/etc/ha-kiosk/ha-kiosk-setup version --json`.
  Missing file or exit 127 means not installed. If contract 1 is not listed, the integration offers
  only `install` (a newer bundle) or a manual purge.
* **No secrets in argv or environment.** The token comes from `--token-stdin` (preferred) or
  `--token-file` located in a `mktemp -d` under `/var/lib/ha-kiosk/incoming/`. `--config -` and
  `--token-stdin` together exit 2; use `--token-file` with `--config -`. The tool deletes
  `incoming/` on success, failure and in `recover`, so an interrupted run leaves no copy.
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
| 10 | Activation failed; rolled back to `previous`, healthy | `rolled-back` | republish nothing; report |
| 11 | Activation failed; stock UI restored | `failed` | republish own display entities (9.4) |
| 12 | Stock-UI restoration failed | `failed` | repair issue; recommend reboot |
| 13 | Stock UI restored by boot recovery; `reason` = `integrity\|crash-loop\|firmware` | `failed` | republish own display entities; report |

`status` exits 0 with `state: not-installed` on a bare panel.

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

* **Current state:** `kiosk/install-local.sh` with `--env`, `--token FILE`, `--layout`, `--no-mqtt`,
  `--no-restart`, `--uninstall [--purge]`, `--version`; exit 0 or 1, plain logs, no status, no lock.
  It copies the token then `chmod 600`, leaving a short window at the default umask.
* **Planned:** `ha-kiosk-setup` as above. `install-local.sh` stays as a wrapper for one MAJOR
  (`--uninstall` → `disable`, `--uninstall --purge` → `purge`, `--no-mqtt` → `--mqtt off`,
  `--no-restart` → `--no-activate`).

---

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
All units get `ProtectSystem=strict` with explicit `ReadWritePaths=`; release trees are therefore
read-only to every kiosk service even though they run as root. Running `qmlscene` as an
unprivileged user is desirable but needs framebuffer, GPU and input access (TO VERIFY).

* **Current state:** flat layout in `/etc/ha-kiosk/` with code, config and user data mixed
  (`native/layout.json` and `photos/` sit in the code tree). Paths are hardcoded in
  `ha-kiosk-bridge.py` (`sys.path`, `ha-kiosk-mqtt.py`, `ha-kiosk-photos.py`), `ha-kiosk-voice.py`
  and `ha-kiosk-intercom.py` (`sys.path`), `ha-kiosk-display.sh` (`ha-kiosk-ambient.py`), every unit
  (`ExecStart`, voice `ExecStopPost`, swap `ConditionPathExists`), and `main.qml`
  (`Qt.resolvedUrl("layout.json")`). No previous version is kept.
* **Planned (Phase 1 code change):** code resolves sibling files from its own directory; units use
  `/etc/ha-kiosk/current/`; user data moves to `/var/lib/ha-kiosk/`. **Migration:** the first
  contract-1 install moves user data out, deletes the flat code, and keeps **no** `previous`: the
  legacy tree is unsigned and shell-sources config, so it is never a rollback target. A failed first
  install exits 11.

### 4.2 States

| State | Meaning |
|---|---|
| `not-installed` | No `current`. Stock UI runs. |
| `installed-disabled` | Release present; kiosk units disabled; drop-in absent; stock UI enabled and running. |
| `active` | Kiosk units enabled and running; stock UI disabled; drop-in present. |
| `failed` | Stock UI restored after a failed activation (11, 13) or restoration failed (12). |
| `rolled-back` | Behaves like `active`; `last_error` explains. Cleared by the next successful `install`/`enable`. |

**Invariant**, enforced by `recover` at every boot before `message_bus` starts: drop-in present ⇔
`ha-kiosk.service` enabled ⇔ `switch_ui_app` disabled. Otherwise Brilliant's backlight writes go to
the shadow directory while the stock UI runs, or both UIs race for the screen.

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
* `ha-kiosk-verify.service` (`After=ha-kiosk.service`, ordered before nothing) runs the health check
  with `rendered` only (never `ha_connected`, since HA or Wi-Fi is often still down after a power
  cut). Boot guard: if `rendered` is not seen within 180 s on two consecutive boots, it restores the
  stock UI with code 13 `crash-loop`.

Re-running `install` after any interruption is always safe.

* **Current state:** files copied in place; the only check is `sleep 3; systemctl is-active
  ha-kiosk.service`, which on failure exits 1 **without** restoring the stock UI. Restoring today
  stops but does not disable `ha-kiosk.service` (`WantedBy=multi-user.target`).
* **Planned:** all of 4.3.

---

## 5. Health check

Within `--health-timeout` (default 60 s, max 180 s) of step 8:

1. `ha-kiosk`, `ha-kiosk-display`, `ha-kiosk-bridge` are `active` and `ha-kiosk`'s `NRestarts` has
   not changed;
2. `/run/ha-kiosk/health.json` exists with `version` equal to `current`, `rendered: true`, and
   (install/enable only, unless `--allow-offline`) `ha_connected: true`;
3. its `ts` is later than both the activation time and `ExecMainStartTimestamp` of `ha-kiosk`, and
   no older than 15 s. A JSON parse error means "retry next poll".

**How the heartbeat is written.** QML cannot get its PID and can write files only through XHR with
`QML_XHR_ALLOW_FILE_WRITE=1`, which is not atomic. So the UI does not write the file: a `Timer` in
`main.qml` POSTs `{rendered, ha_connected, layout_source}` every 10 s to the bridge's existing
localhost HTTP server (`BRIDGE_PORT`, default 8765). `rendered` becomes true on the first
`Window.frameSwapped` after the layout is set; `ha_connected` is `Ha.connected`. The bridge adds
`version` and `ts` and writes the file atomically. The layout cache moves to the bridge as well, and
`QML_XHR_ALLOW_FILE_WRITE` is dropped (TO VERIFY that the Qt version on the image supports
`frameSwapped` from QML).

Outside an operator-initiated install/enable, `ha_connected: false` never triggers rollback or
stock-UI restore; it is reported in `status`. After commit, only the boot guard (4.3) acts on its
own.

* **Current state:** no heartbeat. `ha-kiosk.service` sets `QML_XHR_ALLOW_FILE_WRITE=1` so
  `main.qml` can write `layout-cache.json`.
* **Planned:** as above.

---

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

Deletion is best effort: on eMMC with wear-levelling, removed data may persist in flash. The real
remedy is revocation, so the integration MUST prompt the operator to revoke the HA token and the
panel's MQTT credentials after uninstall. `status` reports `secret_files[]`, which the pilot asserts
is empty after uninstall. The `uninstall`/`purge` JSON output includes
`discovery_cleanup_pending[]`; since the tool deletes itself, that output is the only record and the
integration MUST act on it.

Turning the kiosk off in an integration maps to `uninstall`. `disable` is for temporary hand-back (for
example troubleshooting) and keeps credentials on the panel.

* **Current state:** `--uninstall` stops units, removes the drop-in, restarts `message_bus`, starts
  `switch_ui_app`, and leaves the token, MQTT password and retained discovery. `--purge` removes
  files, units and swap. No rollback.
* **Planned:** the table above.

---

## 7. Configuration

`/etc/ha-kiosk/kiosk.env`: ASCII, one `KEY=value` per line, `#` comments only at line start, blank
lines allowed, max 16 KB.

* Key matches `^[A-Z][A-Z0-9_]{0,63}$` and is in `schema/config.v1.json`, which MUST list every key
  the code reads (a CI test greps `env.get`, `ENV.get`, `os.environ` and `${VAR` against it).
  Unknown keys are an error, except during legacy migration where they are warnings for one MAJOR.
* Value: everything after the first `=`, literally. Forbidden anywhere in a value: bytes outside
  0x20–0x7E, `\`, `"`, `'`, backtick, `$`. `#` mid-value is data, not a comment. No quote stripping.
* Duplicate keys are an error.
* Validators are anchored full matches.

**Invariant:** no value is ever interpolated into a shell command line or a command string. Values
reach programs only as argv elements, environment variables, or JSON. The file is **never
shell-sourced**. `ha-kiosk-setup` renders the validated config to `run.env`; because of the
forbidden characters, systemd's `EnvironmentFile=` reads it literally. Shell services read values
through a Python helper that prints single-quote-escaped assignments. QML reads `run.env` with the
same literal rules.

`overrides.env` (written by the bridge from MQTT and `display set`) accepts only `SCREENSAVER`,
`NIGHT_MODE`, `MOTION_WAKE`, `SMART_DIM`, `DAY_LEVEL`, with the same validators, and is rate-limited
to one write per second. It can never set `HA_URL`, `MQTT_*`, `VOICE_*`, `INTERCOM_*`,
`PHOTO_DIRS`, `GESTURE_*` or `TOUCH_DEVICE`.

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

* **Current state:** four parsers. `ha-kiosk-display.sh` and `ha-kiosk-swap.sh` shell-source
  `kiosk.env` and `overrides.env` **as root**; units use `EnvironmentFile=` (`ha-kiosk.service`
  without `-`, then expands `KIOSK_MODE` in `sh -c`); `ha-kiosk-mqtt.py` `read_env` and the QML
  regexes cut values at `#` or `"` (an `MQTT_PASSWORD` containing `#` is silently truncated); the
  bridge's photo sync has its own parser. `Ha.qml` strips invalid token characters. The installer
  copies `--env` unvalidated.
* **Planned:** schema, validators, `run.env`, helper, no sourcing.

---

## 8. Home Assistant token

Contract 1 uses an operator-created long-lived token for a **dedicated, non-admin, local-only** HA
user. HA tokens have no scopes. A non-admin token only removes admin-API exposure: it can still read
every entity and call any service (locks, alarms, scripts, `shell_command`). Panel compromise equals
that user's power. `GESTURE_*` service calls also run with it.

| API | Where | Non-admin |
|---|---|---|
| WS `auth`, `get_states`, `subscribe_events` `state_changed` | `Ha.qml` | Expected yes (`state_changed` is in HA's non-admin subscribe allowlist); TO VERIFY |
| WS `subscribe_events` `lovelace_updated` | `Ha.qml` | **TO VERIFY.** Believed to be in the same allowlist (`homeassistant/auth/permissions/events.py`). The current "must be an admin" log is an assumption, not a test result. |
| WS `lovelace/config` | `main.qml` | TO VERIFY; dashboards with `require_admin` will fail |
| WS `call_service` (tiles, gestures, `weather.get_forecasts`) | QML | Expected yes; TO VERIFY |
| WS `media_player/browse_media` | `MusicPage.qml` | TO VERIFY |
| WS `media_source/browse_media`, `resolve_media` | `ha-kiosk-photos.py` | TO VERIFY |
| REST `GET /api/states/<entity>` | `ha-kiosk-ambient.py` | Expected yes |
| TTS / media fetch | `ha-kiosk-voice.py` | TO VERIFY |

If `lovelace/config` is denied, the UI uses the cached or file layout: `rendered` stays true,
`layout_source: cache`, `layout_error: forbidden`. Polling fallback (only if `lovelace_updated`
turns out to need admin): re-read `lovelace/config` on reconnect, every `LAYOUT_POLL_SECS` (default
300) comparing a hash, and on `…/layout/reload`.

The token is attached only when the parsed scheme, host and port of a URL equal those of `HA_URL`.

* **Current state:** `docs/MANUAL.md` asks for an admin token; `scripts/publish_layout.py` creates
  an admin-only dashboard. `ha-kiosk-voice.py` attaches the token only when scheme, host and
  port all match `HA_URL` (`same_origin()`).
* **Planned:** non-admin user documented, `publish_layout.py` creates a non-admin dashboard,
  table verified in the pilot.

---

## 9. MQTT and network

### 9.1 Modes

| `KIOSK_MQTT` | Publishes |
|---|---|
| `off` | Nothing. |
| `kiosk` | Only kiosk-owned entities (9.3). **Required for the integration.** |
| `full` | Today's behaviour, for standalone use. |

In `kiosk` mode the bridge subscribes an explicit list of command topics (no wildcard, no
`load/set`).

* **Current state:** MQTT runs inside `ha-kiosk-bridge.py` (it loads `ha-kiosk-mqtt.py`); it is off
  for `0/off/false/no` and on otherwise, publishes load, power, temperatures, motion and
  diagnostics, and subscribes `<base>/+/set`. Voice publishes its own discovery and availability.
* **Planned:** `kiosk` mode.

### 9.2 Namespace and device

* Base `<MQTT_BASE>/<node>`, `MQTT_BASE` default `brilliant_kiosk`,
  `node = brilliant_<first 12 chars of the device id>`; reported in `status.mqtt`.
* Discovery `<prefix>/<component>/<node>/<key>/config`, retained, `unique_id = <node>_<key>`. The
  integration MUST NOT use the `brilliant_<12>_` prefix, and the kiosk publishes nothing outside its
  node.
* With `MQTT_DEVICE_ID` set, the kiosk's `device` block contains only `identifiers`, so entities
  join the integration's device without overwriting its name, model or firmware.
* The kiosk publishes retained `<base>/kiosk_state` (the status `state`) so the integration sees
  boot-time changes without SSH.

### 9.3 Entity ownership in `kiosk` mode

| Entity | Published by kiosk |
|---|---|
| Display state, backlight level, screen power, brightness, screensaver, night mode, motion wake, smart dim, wake | yes (section 10) |
| Room light level, gesture event, layout name and reload | yes |
| Voice (if `KIOSK_VOICE=1`), intercom (if `KIOSK_INTERCOM=1`) | yes; voice volume is suppressed if the integration exposes `output_volume` |
| Wired light, load power, temperatures, motion, firmware, boot, calibration, HomeKit, Shelly BLE | **no** (integration) |

### 9.4 Discovery hand-off

Integration display entities (screen power, brightness, motion wake, screensaver) are published
**iff** `status.state ∈ {not-installed, installed-disabled, failed}`. The integration re-evaluates
after every command result and on changes of `<base>/kiosk_state` or `<base>/availability`.

1. Before `install`/`enable`, the integration withdraws its display discovery.
2. On start in `kiosk` mode, the kiosk clears any retained `full`-mode topics under its node.
3. `disable`/`uninstall`/`purge` run `ha-kiosk-mqtt.py --withdraw` before stopping units: empty
   retained payloads for every topic in `discovery_topics` (including voice and intercom) and
   `offline` availability, with a timeout. Topics not cleared go to `discovery_cleanup_pending`,
   which the integration clears with its own credentials.
4. After exit 11, 12 or 13, or a stock-UI state at boot, the integration republishes its display
   discovery.

### 9.5 Network listeners and broker ACL

| Listener | Bind | Firewall | Allowed | Auth |
|---|---|---|---|---|
| Bridge HTTP `BRIDGE_PORT` (8765) | 127.0.0.1 | none needed | local processes | none (root-only box) |
| Voice Wyoming TCP `VOICE_PORT` | `VOICE_BIND` (default: LAN address toward HA) | runtime `nft` accept rule in `inet firewall filter-input`, removed on stop (`--close-firewall`) | `VOICE_ALLOW` (default: HA host) | none |
| Intercom UDP `INTERCOM_PORT` | `INTERCOM_BIND` (default: LAN address toward broker) | TO VERIFY whether Brilliant's ruleset admits it | source IPs in retained `<base>/intercom/peers/#` | **none; LAN trust only**, spoofable |
| Intercom control TCP `INTERCOM_PORT+1` | 127.0.0.1 | — | local | none |

Required broker ACL (template shipped in the docs): each panel's user reads and writes only
`<base>/<node>/#`, `<base>/intercom/peers/<node>`, and its own discovery topics, and reads
`<base>/intercom/peers/#`; the integration's user may write `<base>/<node>/display/+/set`. Without
per-panel credentials, any broker client can register an intercom peer or blank screens.
`status.listeners[]` and diagnostics report open ports.

* **Current state:** as in the table; MANUAL's "nothing new is exposed" is true only with voice and
  intercom off.

---

## 10. Display control for the integration

The kiosk owns the backlight while active (Brilliant's writes go to `/run/ha-kiosk-shadow`), so the
integration MUST control the screen through this interface. While `state` is not active or
rolled-back, the kiosk answers nothing and the integration owns these controls.

**MQTT** under `<base>/<node>/display/…`: commands QoS 1, not retained; states retained. Every
command produces a state echo within 2 s, or `display/error` with `{"cmd", "reason"}`.

| Function | Command (payload) | State |
|---|---|---|
| Screen power | `power/set` `ON`/`OFF` (OFF = asleep until touch, allowed motion, or ON) | `power` |
| Brightness | `brightness/set` 1–15 (HA `number`, min 1, max 15, step 1) | `brightness` 0–15 |
| Motion wake | `motion_wake/set` `day`/`always`/`off` | `motion_wake` |
| Screensaver | `screensaver/set` `ON`/`OFF`; `screensaver/start` | `screensaver` |
| Wake | `wake` | — |
| Display state | — | `state` (`awake`/`photos`/`dim`/`asleep`) |

**SSH:** `ha-kiosk-setup display get --json` and `display set <function> <value>`, which call the
bridge's localhost HTTP server (`POST /display`); no separate socket.

Settings persist in `overrides.env` (7); `power`, `wake`, `screensaver/start` are transient.

* **Current state:** the bridge handles `<base>/screensaver/set`, `night_mode/set`,
  `motion_wake/set`, `smart_dim/set` (`ON`/`OFF`), `day_level/set`, `wake/press`, writes
  `overrides.env` and restarts `ha-kiosk-display`. `ha-kiosk-display.sh` has no IPC and can block
  for up to `IDLE_DIM_SECS` (300 s), re-reading config only on restart. No screen-off.
* **Planned:** the display loop is rewritten so every wait is ≤ 2 s and it applies commands from
  `/run/ha-kiosk-display/cmd` (atomic rename), or moved to Python with `select` on touch input and a
  control pipe. Until then the topics keep restart semantics. Old topics stay as aliases in
  contract 1.

---

## 11. OTA

* **Current state (UNVERIFIED):** MANUAL says OTA replaces the read-only image and keeps `/etc` and
  `/var`. Not confirmed; nor whether OTA re-enables `switch_ui_app` or changes `message_bus.service`.
* **Planned:**
  * At boot, `recover` (4.3) checks the `current` tree against `MANIFEST.sha256` and the digest in
    `state.json` (mismatch → stock UI, code 13 `integrity`), and firmware against `firmware.min`
    (below → stock UI, code 13 `firmware`). If state is `active` but units, drop-in or the
    `switch_ui_app` state are wrong, it runs `reapply`.
  * The integration keeps the desired state (enabled, pinned version) and cached verified bundle.
    After a firmware change or reboot it first runs its own OTA re-apply (which also touches
    `message_bus`), then `/etc/ha-kiosk/ha-kiosk-setup status --json`. Exit 127 or missing tool
    with kiosk desired → `install` from the cached bundle.
  * Pilot: one OTA with the kiosk active, recording surviving paths and `systemctl cat
    message_bus` before and after.

---

## 12. Diagnostics

`ha-kiosk-setup diagnostics --json`, lock-free, ≤ 256 KB, `state: not-installed` on a bare panel:

* `status`; `VERSION`/`CONTRACT` of each release; `txn.json` step names only;
* config by **allowlist**: keys marked `diag: safe` with values, others as `{"set": true, "len": n}`;
  entity ids hashed with a per-panel salt;
* `journalctl -u 'ha-kiosk*'` last 200 lines (`message_bus` only lines tagged `ha-kiosk`), with
  every output field scrubbed by: JWT `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`,
  `Bearer \S+`, `authSig=[^&\s]+`, literal search-and-replace of the token, MQTT password and the
  values of `HA_URL`, `MQTT_HOST`, `VOICE_BIND`, `VOICE_ALLOW`, `INTERCOM_BIND`, then IP literals;
* health, display state, firmware, uptime, memory, disk, listeners, `switch_ui_app` state.

Excluded: photos, layout content, entity lists.

Logging rule for all services: never log full URLs with query strings; strip `authSig=`.

* **Current state:** none; `scripts/logs.sh` is unredacted. `Ha.qml` logs `base=<HA_URL>`, MQTT
  logs the broker host, voice and photos log fetched URLs that can carry signed `authSig` links.
* **Planned:** as above.

---

## 13. Compatibility

* Contract version is an integer in `CONTRACT` and `release.json`. Within a contract, commands,
  flags, exit codes, JSON fields, config keys and topics are only added.
* A MAJOR release accompanies a contract bump; each release supports one contract.
* Downgrades within a contract need `--allow-downgrade` and are always refused below the
  `min_version` floor recorded on the panel (raised monotonically from signed `release.json`).
* Deprecated aliases (`install-local.sh`, old topics, `KIOSK_MQTT=1/0`) stay for one MAJOR.

---

## 14. Phasing

1. **Installer, security, rollback:** sections 2–8 (signing, reproducible builds, `ha-kiosk-setup`,
   release layout and path refactor, transactions, boot recovery, heartbeat, config parser,
   uninstall, non-admin token).
2. **MQTT and display:** sections 9–10 (`kiosk` mode, withdraw, display loop rewrite).
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
