# Home Assistant integration and `pybrilliantkiosk`: architecture note

*2026-11-09. Describes `custom_components/brilliant_kiosk` 0.2.0 and `pybrilliantkiosk` 0.1.3 on
the `nightly` branch. This file began as the design draft of the "admin helper" (same date); the
draft is superseded and this note replaces it under the same filename. User-facing behaviour,
configuration and actions are in [home-assistant-integration.md](home-assistant-integration.md);
the panel-side interface is in [INTEGRATION.md](INTEGRATION.md) section 0; the library's API is in
[lib/pybrilliantkiosk/README.md](../lib/pybrilliantkiosk/README.md).*

## 1. Components

| Component | Where | Role |
|---|---|---|
| `ha-kiosk-setup` | `kiosk/ha-kiosk-setup`, shipped in every bundle, installed at `/var/lib/ha-kiosk/ha-kiosk-setup` | The only thing that installs, activates, rolls back or removes the kiosk on a panel. Prints JSON, documented exit codes, holds the panel lock |
| `ha-kiosk-bridge.py` + `ha-kiosk-mqtt.py` | On the panel, `ha-kiosk-bridge.service` | Message-bus access for the UI, heartbeat file, MQTT device and the retained `kiosk_state` / `discovery` notices |
| `pybrilliantkiosk` | `lib/pybrilliantkiosk`, PyPI | Typed async SSH client (`asyncssh`, `aiohttp`). The only code that talks to a panel. Builds the shell commands, parses their output, verifies bundles, lists GitHub releases |
| `brilliant_kiosk` | `custom_components/brilliant_kiosk` here (source of truth, tests, CI); distributed from `github.com/MikeI-Code/ha-brilliant-kiosk` | One config entry per panel; a 60 s coordinator, entities, admin-only actions, repairs, diagnostics, options |
| MQTT device | Created by Home Assistant's MQTT integration from the kiosk's discovery | The panel's sensors and controls (load, motion, display, screensaver, ...) |

There is no add-on, no HA helper (`input_*`) management and no MQTT publishing by the integration
itself; those were in the draft and were dropped.

## 2. Data flow

```
 panel                                   Home Assistant
 ─────────────────────────────────       ─────────────────────────────────────────────
 message bus ◄── brilliant_bus.py ◄──┐   pybrilliantkiosk.Panel ◄── BrilliantKioskCoordinator
 (device id, room, variables)        │     │  one persistent SSH connection          │ 60 s
                                     ├───  │  poll: one shell command, no Python     │
 /proc, systemctl, kiosk.env ×2,     │     │  set_env: write under the setup flock    ▼
 overrides.env, /run/ha-kiosk/*  ────┘     │  install/setup: ha-kiosk-setup --detach  entities, repairs,
                                           │                     + wait               diagnostics, actions
 ha-kiosk-setup ◄──────────────────────────┘
      │ kiosk_state, discovery (retained)        pybrilliantkiosk.Releases ◄── ReleasesCoordinator
      ▼                                           GitHub API, 2 requests / 6 h, shared by all panels
 ha-kiosk-bridge ──► MQTT broker ──► HA MQTT integration ──► MQTT device ◄── via_device ── kiosk device
                     (discovery, states)                     ──► discovery flow (brilliant_kiosk/+/discovery)
```

**Poll** (`Panel.poll`, every 60 s). One `exec` of a shell script whose output is split into
sections by markers that carry a random per-command nonce (`#--kl-<nonce>:<name>--`), so file
content cannot forge a section. It yields `PanelSnapshot`: health (`/proc` facts, seven unit
states, backlight, display state, `VERSION`), both copies of `kiosk.env` with their SHA-256 as
read, the runtime overrides, and the files `ha-kiosk-setup status` reads. `setup.status_from_files`
rebuilds the tool's status object from those without starting Python on the panel. The coordinator
turns the snapshot into entities, keeps the device's `sw_version` current, links `via_device` to
the MQTT device once it exists, and raises or clears repair issues (`env_drift`,
`env_drift_unsafe`, `mqtt_provider_not_applied`).

**Settings** (`Panel.set_env`, used by the number entities, the options listener and the `set_env`
action). Both copies of `kiosk.env` are read with their hashes, reconciled (running values win),
edited with the tool's key allowlist (parsed out of the installed `ha-kiosk-setup`, falling back to
the library's copy) and value rules, rendered `KEY="value"`, and sent as stdin to a script that
runs under the setup flock, checks the stdin hash and both files' expected hashes, and installs
the new text into both copies (`.bak` kept) and purges the same keys from `overrides.env`. A
hash mismatch is retried once on fresh state, then `ConcurrentModification`.

**Install and setup commands** (`Panel.install`, `Panel.setup_command`). The bundle is verified in
Home Assistant, uploaded (SFTP, or `cat >` when the panel has no SFTP subsystem) into a
`mktemp -d /tmp/ha-kiosk-install.XXXXXXXX` directory, hashed again on the panel, and
`ha-kiosk-setup` is extracted from it and run with `--detach`; `wait --timeout 100` is polled up
to 15 times (sized from the tool's own worst case). Settings and token files travel in that
directory on a first install only; other installs keep the panel's. `reapply`, `enable`,
`disable`, `rollback`, `uninstall`, `purge` run the installed tool the same way.

**Identity and discovery.** `Panel.identity` runs one script with Brilliant's Python that performs
the bus HELLO (the reply's `domain` is the device id) and reads `art_config_peripheral.
display_name` (the room). The kiosk publishes retained `<base>/kiosk_state` and
`<base>/discovery` (state, device id, room, LAN address); the integration's manifest subscribes to
both under `brilliant_kiosk/+/` and opens a discovery flow for `state = active`, prefilled with the
address.

## 3. Trust model

| Concern | Mechanism | Where |
|---|---|---|
| Who the panel is (transport) | SSH host-key pinning. The config flow fetches the key before any credential is sent (`fetch_host_key`: the key exchange completes and the connection is then refused), shows the `SHA256:` fingerprint, and pins it in the entry. Later connections accept only that key (`known_hosts` disabled, `~/.ssh` never read, fixed host-key algorithm order so the negotiated key is the one shown). MD5 fingerprints are refused. A changed key stops the poll and starts a reauth flow that shows the new fingerprint | `pybrilliantkiosk.panel`, `config_flow.py` |
| Credentials | Password or a private key given to the integration; only that key is offered (`client_keys=None` when there is none, so asyncssh does not load default identities). Stored in the config entry, redacted in diagnostics | `panel.py`, `diagnostics.py` |
| What gets installed as root | **Pinned stable releases**: `pinned.py` lists each stable tag with the bundle's SHA-256 and size (a trust-on-first-use snapshot checked three ways); a stable release not in the table is refused (`release_not_pinned`). **Nightlies** are installable only when the user chose the nightly channel and are checked against the release's `.sha256` asset, which proves integrity, not origin. Downloads are streamed and capped at 50 MiB. The panel re-hashes the bundle and checks its `MANIFEST.sha256` | `install.py`, `pinned.py`, `releases.py`, `ha-kiosk-setup` |
| Writes to the panel | Every `kiosk.env` write is sha-verified on the panel before it is installed (an empty or truncated stdin once emptied both copies), checked against the hashes of the files as read, and done under the setup tool's flock. Lines that are not inert when sourced by a root shell are never written; a drifted unsafe running value blocks the write and surfaces as a non-fixable repair | `panel.py` (`_write_env_script`, `_LOCKED_RUNNER`), `env.py` |
| Who may act | The six actions (`set_env`, `get_env`, `bus_get`, `bus_set`, `logs`, `install_version`) are registered with `async_register_admin_service`: a non-administrator's call is refused; automations are unaffected. Options, reauth and repairs are Home Assistant administrator dialogs. `set_env` refuses `KIOSK_MQTT` and `MQTT_DEVICE_ID` (owned by the MQTT-provider option). Units and peripheral names are allowlisted or pattern-checked; every remote argument is shell-quoted | `services.py`, `panel.py`, `bus.py` |
| Downgrades and first installs | The update entity and `install_version` never offer or install an older version (`versions.is_newer`) and refuse a panel without the kiosk (a first install needs HA URL, token and MQTT settings and happens only in the config flow) | `update.py`, `coordinator.py` |
| Timeouts | A timed-out command is killed and its channel closed, so the setup lock cannot stay held by a zombie | `panel.py` (`_abandon`) |
| Information exposure | Diagnostics redact credentials, addresses, logins and any setting whose name suggests a secret; the `logs` action output is scrubbed; the panel-side `diagnostics` redacts the token and MQTT password | `diagnostics.py`, `redact.py`, `ha-kiosk-setup` |

Not covered (see INTEGRATION.md section 0.15 and the planned sections): release signatures, a
non-admin HA token for the kiosk, per-panel MQTT credentials and broker ACLs, withdrawal of retained
MQTT discovery on uninstall.

## 4. Identity model

A panel **is** its Brilliant device id, read from the stock firmware's message bus (`Panel.
identity`). The id does not change when the kiosk is installed, removed or the panel re-flashed,
and it needs no kiosk file, so it is available on a bare panel.

* Config entry `unique_id` and the Home Assistant device identifier: `brilliant_<first 12
  characters of the device id>` (`const.node_of`), which is also the kiosk's MQTT node name and the
  topic segment of `kiosk_state`/`discovery`.
* Entry title and device name: `<room> Brilliant kiosk` from Brilliant's room name, else
  `Brilliant kiosk <host>`; set once at setup unless the user renamed it.
* The MQTT device is a separate device, `(mqtt, <node>)` for the built-in provider or
  `(mqtt, <MQTT_DEVICE_ID>)` for the `brilliant_mqtt` provider; the kiosk device points at it with
  `via_device` and never claims its identifiers.
* The SSH host key is never an identity, only transport authentication. Entries of version 1 that
  carried a fingerprint as `unique_id` are converted to the node on their first successful poll
  (device and entity unique ids migrated).
* If the id cannot be read while adding a panel (the bus restarts for a minute or two when the
  kiosk is switched on or off), the panel is not added.

## 5. Release and distribution

| Artifact | Channel | Mechanism |
|---|---|---|
| `pybrilliantkiosk` | PyPI | `.github/workflows/publish-lib.yml` on tag `pybrilliantkiosk-vX.Y.Z` (must equal `pyproject.toml`): ruff, mypy `--strict`, tests, `uv build`, `twine check`, then `pypa/gh-action-pypi-publish` with **trusted publishing** (OIDC, GitHub environment `pypi`, no stored token). Requires Python ≥ 3.13 |
| `brilliant_kiosk` | Separate public repository `https://github.com/MikeI-Code/ha-brilliant-kiosk` (created 2026-11-09; first release v0.2.0). It holds only `custom_components/brilliant_kiosk`, `hacs.json` and a README, copied from this repository, which stays the source of truth with the tests and CI. Kept separate because this repository's own GitHub releases (`v1.4.0`, `nightly-*`) are panel bundles and would be read as integration versions. **Install:** Home Assistant 2026.11 or newer: Settings → Marketplace → Add from link → `MikeI-Code/ha-brilliant-kiosk` (the built-in Marketplace replaced HACS, which refuses to run on 2026.11+); older Home Assistant: HACS custom repository with the same URL. Updates appear under Settings → Updates. A manual copy of `custom_components/brilliant_kiosk` also works | `manifest.json` pins `pybrilliantkiosk==<version>`; `hacs.json` sets Home Assistant ≥ 2026.3.1; `integration.yml` in this repository runs ruff, mypy `--strict`, both test suites at 100 % line coverage, hassfest and HACS validation on `main`, `nightly`, pull requests and weekly. **Shipping a version** (manual today): copy the integration into `ha-brilliant-kiosk`, then `gh release create vX.Y.Z` there |
| Kiosk bundles | GitHub releases of the same repository | `release.yml` (stable, on release publish) and `nightly.yml` (prerelease `nightly-YYYYMMDD`, 14 kept); the integration installs them (section 3) |

Integration work lands on `nightly` first; `main` holds the released state. The `quality_scale:
platinum` in the manifest is the author's own assessment against the Home Assistant quality
scale; hassfest's scale validation and `.strict-typing` do not apply to custom integrations.

## 6. Entities, actions and repairs (summary)

| Kind | Items |
|---|---|
| Sensors | `version`, `setup_state` (enum: `active`, `disabled`, `failed`, `in_progress`, `not_installed`, `unmanaged`), `load`, `memory_used` |
| Binary sensors | One per kiosk unit (`running`, diagnostic); voice, intercom, capture and swap disabled by default |
| Buttons | `restart_kiosk`, `restart_voice`, `reboot`, `reapply`, `rollback` |
| Numbers | `dim_level`, `min_level` (written to `kiosk.env`, display service restarted) |
| Select | Release channel (`stable`, `nightly`) |
| Update | `update.<panel>_kiosk`: stable or nightly channel, release notes, install (when `manage_installs` is on), specific version |
| Actions | `set_env`, `get_env`, `bus_get`, `bus_set`, `logs`, `install_version` (administrators only) |
| Options | Release channel, MQTT provider (`builtin` or `brilliant_mqtt` + device id), `manage_installs` |
| Repairs | `env_drift` (fixable: reconcile both copies), `env_drift_unsafe` (not fixable: fix on the panel), `mqtt_provider_not_applied` (fixable: apply the option again) |

The exact names, parameters and examples are in
[home-assistant-integration.md](home-assistant-integration.md).

## 7. History

The 2026-11-09 draft that this note replaces proposed, and the build changed: transport through
`install-local.sh` edits became transport through `ha-kiosk-setup`; shared MQTT identifiers became
`via_device`; a 95 % coverage target became 100 %; an ingress add-on and HA helper management were
dropped; zeroconf/DHCP discovery became MQTT discovery of a running kiosk; a wake button, day-level
number and switch entities were not built (the MQTT device has them). Its open questions are
answered: settings drift is a repair issue; the library is on PyPI; discovery uses the kiosk's
retained MQTT messages.
