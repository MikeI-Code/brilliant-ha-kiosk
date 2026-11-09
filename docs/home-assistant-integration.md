# Brilliant Kiosk: Home Assistant integration

The **Brilliant Kiosk** integration (`brilliant_kiosk`, version 0.2.0) manages Brilliant panels
that run this project's kiosk from Home Assistant. One config entry per panel; each is reached
over SSH through the typed library [`pybrilliantkiosk`](../lib/pybrilliantkiosk/README.md).

It is the **admin layer**. The panel's day-to-day entities (wired light, motion, display state,
screensaver and night switches, gestures, voice, intercom, capture, security) are published by
the kiosk itself over MQTT ([manual, section 2.7](MANUAL.md#27-connect-the-panels-to-home-assistant-over-mqtt)).
This integration adds installation and maintenance on top and links its device to that MQTT
device, so both appear together.

Contents: [Description](#high-level-description) · [Installation](#installation) ·
[Removal](#removal) · [Configuration parameters](#configuration-parameters) ·
[Installation parameters](#installation-parameters) · [Entities](#entities) · [Actions](#actions) ·
[Repairs](#repairs) · [Data update](#data-update) · [Examples](#examples) ·
[Known limitations](#known-limitations) · [Supported devices](#supported-devices) ·
[Supported functions](#supported-functions) · [Troubleshooting](#troubleshooting) ·
[Use cases](#use-cases)

## High-level description

A Brilliant panel is a wall light switch with a touchscreen. The kiosk replaces its stock
touchscreen app with a native Home Assistant UI, installed and kept up to date by an on-panel
tool, `ha-kiosk-setup`. The integration is the Home Assistant side of that tool:

* **Install, update, roll back.** An update entity offers new kiosk releases (stable or nightly
  channel) and installs them with `ha-kiosk-setup`, which verifies the bundle, keeps the previous
  release and rolls back if the new one does not come up. Buttons roll back or reapply. A panel
  that has SSH but no kiosk can be installed from the setup dialog.
* **Monitor.** Kiosk version, install state, memory, load, and whether each kiosk service runs.
* **Settings.** Read and change `kiosk.env` from the UI, scripts and automations. Values are
  validated, the panel's two copies of the file are kept in step, and nothing is written while an
  install runs.
* **Operate.** Restart the kiosk or the voice service, reboot the panel, read redacted service
  logs, read and write Brilliant message-bus variables.
* **Repair.** Repair issues tell you when the panel's running and saved settings disagree, or an
  MQTT provider change could not be applied, and offer a fix.

Everything the integration does on a panel goes through an allow-list: a fixed set of systemd
units, validated setting names and values, and `ha-kiosk-setup`'s own commands. It never runs
shell commands that you or an automation supply. All actions are for administrators only.

## Installation

You need a panel with SSH switched on ([manual, section 2.1](MANUAL.md#21-turn-on-ssh-on-the-panel)),
Home Assistant **2026.3.1 or newer**, and the **MQTT** integration set up (the integration
depends on it: it discovers panels from the kiosk's retained MQTT topics, and the kiosk's own
entities come over MQTT).

### Home Assistant 2026.11 and newer: Marketplace

1. **Settings, Marketplace, Add from link**, enter `MikeI-Code/ha-brilliant-kiosk`.
2. Install **Brilliant Kiosk** and restart Home Assistant when asked.
3. **Settings, Devices & services, Add integration, Brilliant Kiosk**.

The integration is distributed from [github.com/MikeI-Code/ha-brilliant-kiosk](https://github.com/MikeI-Code/ha-brilliant-kiosk);
it is developed, tested and released from this repository. HACS does not run on Home Assistant
2026.11 and newer.

### Home Assistant 2026.3.1 to 2026.10: HACS

1. In HACS, three-dot menu, **Custom repositories**: add
   `https://github.com/MikeI-Code/ha-brilliant-kiosk`, category **Integration**.
2. Download **Brilliant Kiosk** and restart Home Assistant.
3. **Settings, Devices & services, Add integration, Brilliant Kiosk**.

### Manual

1. Copy `custom_components/brilliant_kiosk/` into `<config>/custom_components/`.
2. Restart Home Assistant. `pybrilliantkiosk` is installed automatically from `manifest.json`.
3. Add the integration as above.

### Adding a panel

A panel whose kiosk publishes over MQTT is offered as a **discovered** device under *Devices &
services*. With a kiosk nightly newer than 20261109 the panel's address is already filled in and
the card says which room it is (*Kitchen Brilliant panel found*). Older kiosks are offered by
node id without an address. Or click **Add integration** and enter everything.

1. Enter the panel's **host**, SSH **port** (22 unless you changed it), **username** (normally
   `root`) and a **password**, a **private key**, or both.
2. The integration connects once to read the panel's SSH host key and shows its fingerprint.
   Compare it with the panel (`ssh-keygen -l -E sha256 -f /etc/ssh/ssh_host_ed25519_key.pub`), or
   paste the fingerprint into **Expected host key fingerprint** on the first form to skip this
   step. No password or key is sent before you confirm the key, and afterwards only to a panel
   that presents it.
3. The integration reads the panel's Brilliant device id and room name, and its kiosk state. If
   the kiosk is not installed, you are offered to install it ([installation parameters](#installation-parameters));
   untick **Install the kiosk now** to add the panel for monitoring only.

The panel appears as a device named **"\<Room\> Brilliant kiosk"** (for example *Kitchen
Brilliant kiosk*), or *Brilliant kiosk \<host\>* when Brilliant has no room name for it.

## Removal

1. **Settings, Devices & services, Brilliant Kiosk**, three-dot menu of the panel, **Delete**.
   This removes the panel's device, entities and repair issues.
2. The kiosk on the panel is **not** removed: deleting the entry only stops Home Assistant
   managing it. To remove the kiosk too, run `ha-kiosk-setup uninstall` on the panel or
   `scripts/uninstall.sh` ([manual, section 8](MANUAL.md#8-maintenance-updates-channels-rollback-uninstall)).
3. Then remove the integration itself: Marketplace or HACS, or delete
   `custom_components/brilliant_kiosk/` for a manual install, and restart Home Assistant.

## Configuration parameters

Set when adding a panel, changed with **Reconfigure** (address, port) or **Reauthenticate**
(credentials):

| Parameter | Description |
|---|---|
| Host | Name or IP address of the panel. |
| SSH port | Default 22. Used for every connection. Entries made before this field existed use 22. |
| Username | SSH user, normally `root`. |
| Password | SSH password. Optional if you give a private key. |
| Private key | The whole text of an **unencrypted** OpenSSH or PEM private key; the panel needs the matching public key in `authorized_keys`. Optional if you give a password. |
| Expected host key fingerprint | Optional `SHA256:...`. If it matches, the confirmation step is skipped. MD5 fingerprints are refused: they are not safe to pin. |

**Host key pinning.** The SSH host key is pinned when you confirm it. If it ever changes (the
panel was reset or re-flashed, or something else answers at that address), polling stops and
Home Assistant starts a **re-authentication**: it shows the pinned and the new fingerprint, and
pins the new key only after you tick the confirmation box and enter your credentials.
Reconfiguring to an address that presents another key works the same way.

**How a panel is identified.** By **Brilliant's own device id**, which never changes. It is read
from the stock firmware's message bus, so it is available before the kiosk is installed and
after it was removed. The config entry's unique id and the device are `brilliant_<first 12
characters of the device id>`, the same node name the kiosk's MQTT uses. The host key is never an
identity (it changes on a re-flash). If the device id cannot be read when adding a panel, the
panel is not added: Brilliant's software restarts for a minute or two when the kiosk is switched
on or off and after a reboot; wait and try again. Reauthenticate and Reconfigure refuse a
different panel behind the same address. Entries made while the id could not be read (config
entry version 1) have the host key fingerprint as unique id and are converted on the first
successful poll, keeping names, history and settings.

**Options** (the cog of a panel):

| Option | Description |
|---|---|
| MQTT provider | **Built-in kiosk MQTT** (`KIOSK_MQTT=1`, the default) or **brilliant-mqtt** (`KIOSK_MQTT=kiosk`). MQTT is never switched off: the panel always publishes its entities, either itself or through brilliant-mqtt. Changing it writes `KIOSK_MQTT` on the panel and restarts the bridge. If that fails, a repair issue offers to try again, and every poll compares the panel with the option: a disagreement raises the repair, agreement clears it. |
| brilliant-mqtt device id | Required for brilliant-mqtt (`MQTT_DEVICE_ID`): the kiosk's entities join that MQTT device, and this panel's device is linked to it. |
| Manage installs | Allow installing, updating and rolling back the kiosk from Home Assistant. On by default with built-in MQTT, off by default when brilliant-mqtt manages the panel. |

The **Release channel** select entity (Stable or Nightly) chooses which releases the update
entity offers and whether a nightly may be installed. On first setup it follows what is
installed: a panel running a nightly starts on Nightly, any other on Stable. Your later choice is
kept.

## Installation parameters

Asked only when the kiosk is not yet installed on the panel. Everything is typed here: nothing is
read from other integrations (not even Home Assistant's MQTT entry).

| Parameter | Description |
|---|---|
| Install the kiosk now | Untick to add the panel for monitoring only. |
| Home Assistant URL for the panel | An address the panel can reach on your network, starting with `http://` or `https://`. Prefilled with your internal URL when there is one. |
| Long-lived access token | Created on your Home Assistant profile page (Security tab), ideally for an administrator. Stored on the panel only, readable by root. |
| MQTT broker host | Your broker's host name or IP address. Port 1883 is assumed; change `MQTT_PORT` afterwards with `set_env`. |
| MQTT user, MQTT password | Optional broker login. |

**Characters.** The panel keeps settings in a file that root shell scripts read, so values are
written as `KEY="value"` and must not contain `'`, `"`, `$`, a backtick, a backslash or control
characters. This applies to the URL, the MQTT host, user and password (a broker password with one
of these characters cannot be used; change it on the broker), and to every value written with
`set_env`. The form refuses such values before anything is downloaded. Spaces and `; | & > ( ) #`
are fine.

**What gets installed.** The latest *stable* release, and only if its SHA-256 and size are in the
table pinned inside the integration (`custom_components/brilliant_kiosk/pinned.py`; currently
v1.4.0). That table is a trust-on-first-use snapshot of GitHub's assets, checked three ways when
it was added; after that, someone who could replace a release on GitHub could replace its
`.sha256` file too, but not the table you installed with the integration. If the newest release
is not in the table yet, the install is refused with an explanation: update the integration, or
untick **Install the kiosk now** and install by hand ([manual](MANUAL.md#24-install)). The bundle
is verified again on the panel by `ha-kiosk-setup`. The settings written to the panel are the
bundle's own `kiosk.env.example` with your URL, token and MQTT values applied, so every default
(including `WEATHER_FX`) is the one the release ships.

A first install takes a few minutes (the panel restarts its display services); the dialog shows
progress and the result.

## Entities

Per panel, on the device **"\<Room\> Brilliant kiosk"**:

| Entity | Type | Default | What it is |
|---|---|---|---|
| Kiosk | update | enabled | Installed and newest kiosk version of the selected channel, with release notes. Supports install (also a specific version) when **Manage installs** is on. Never offers a downgrade: a stable channel does not offer an older stable over an installed nightly. |
| Release channel | select | enabled | `Stable` or `Nightly`. |
| Kiosk version | sensor (diagnostic) | enabled | The installed bundle version. |
| Install state | sensor (diagnostic) | enabled | `Active`, `Disabled`, `Failed`, `In progress`, `Not installed` or `Installed manually` (the kiosk is there but was not installed with `ha-kiosk-setup`). |
| Memory used | sensor (diagnostic) | enabled | Percent. |
| Load | sensor (diagnostic) | disabled | 1-minute load average. |
| Kiosk, Display service, Bridge service | binary sensors (diagnostic, running) | enabled | Whether `ha-kiosk`, `ha-kiosk-display` and `ha-kiosk-bridge` run. |
| Swap service, Voice service, Intercom service, Capture service | binary sensors (diagnostic, running) | disabled | The optional services. |
| Dim level | number (config), 0-15 | enabled | `DIM_LEVEL` in `kiosk.env`; writing it restarts the display service. |
| Minimum level | number (config), 0-15 | enabled | `MIN_LEVEL` in `kiosk.env`; writing it restarts the display service. |
| Restart kiosk | button | enabled | Restarts `ha-kiosk.service`. |
| Reboot panel | button (config) | enabled | Reboots the panel. |
| Restart voice | button | disabled | Restarts `ha-kiosk-voice.service`. |
| Reapply kiosk | button (config) | disabled | `ha-kiosk-setup reapply`: re-activates the current release, for example after a firmware update. Needs **Manage installs**. |
| Roll back kiosk | button (config) | disabled | `ha-kiosk-setup rollback`: activates the previous release. Needs **Manage installs**. |

Entity ids are `<platform>.<device name>_<entity>`, for example `update.kitchen_brilliant_kiosk_kiosk`.
Entries created by earlier versions as *Brilliant panel \<host\>* are renamed to the room form at
their next setup unless you named the entry or device yourself; entity ids do not change.

Diagnostics (**Download diagnostics** on the device) contain the panel's health, settings and
setup status with the address, ids, host key, logins, secrets, MQTT broker details, allow-lists
and every IP address removed.

## Actions

All actions are **for administrators only**: they run commands as root on a panel, so a call by
a user who is not an administrator is refused (automations and scripts are not affected). Every
action needs the **panel** (`config_entry_id`, picked from a list in the UI). A value the panel
would refuse raises a validation error and changes nothing.

### `brilliant_kiosk.set_env`: set settings

Merges settings into the panel's `kiosk.env`, atomically, keeping comments and order, in both
copies the panel keeps (`/etc/ha-kiosk/kiosk.env` and `/var/lib/ha-kiosk/config/kiosk.env`).

| Field | Description |
|---|---|
| `values` | Mapping `KEY: value` of settings to set. Booleans are written as `1`/`0`. Names must be settings `ha-kiosk-setup` knows ([manual, section 7](MANUAL.md#7-configuration-reference)); values follow the [character rules](#installation-parameters). |
| `delete` | List of setting names to remove. |
| `restart` | Restart `ha-kiosk.service` afterwards (default off). The display service is restarted by the number entities, not here. |

`KIOSK_MQTT` and `MQTT_DEVICE_ID` are refused (set or delete): they belong to the **MQTT
provider** option, so change them in the panel's options, which keeps the options and the panel
in step and never switches MQTT off. Runtime overrides (the switches published by the kiosk)
win over `kiosk.env`, so every override for a key you set or delete is purged. If the panel's
running `/etc/ha-kiosk/kiosk.env` contains a value that cannot be written safely (for example a
hand-edited `HA_URL` with a `$`), the action fails naming the keys and writes nothing. Fix it on
the panel, or set that key explicitly in the same call.

```yaml
action: brilliant_kiosk.set_env
data:
  config_entry_id: 01J0EXAMPLE
  values:
    DAY_LEVEL: 12
    SCREENSAVER: false
    SLEEP_START: "22:30"
    WEATHER_FX: sky
  delete:
    - PHOTO_DIRS
  restart: true
```

### `brilliant_kiosk.get_env`: read settings (response)

Returns `{"env": {...}}`: the effective settings (`kiosk.env` with runtime overrides applied,
secrets masked).

```yaml
action: brilliant_kiosk.get_env
data:
  config_entry_id: 01J0EXAMPLE
response_variable: panel_settings
```

### `brilliant_kiosk.bus_get` and `brilliant_kiosk.bus_set`: message-bus variables

`bus_get` returns the variables of a Brilliant peripheral
(`{"variables": [{"name", "value", "read_only"}]}`); `bus_set` writes externally settable ones.

| Field | Description |
|---|---|
| `peripheral` | Peripheral name, for example `hardware_peripheral`. |
| `values` | (`bus_set` only) Mapping `variable: value`. |

```yaml
action: brilliant_kiosk.bus_get
data:
  config_entry_id: 01J0EXAMPLE
  peripheral: hardware_peripheral
response_variable: bus
```

### `brilliant_kiosk.logs`: service logs (response)

Returns `{"logs": "<text>"}`, the last journal lines of one kiosk service, **redacted** before
they leave Home Assistant: the panel's address, node and device ids and logins are replaced by
`**REDACTED**` as in the diagnostics, and so is anything that looks like a secret (values
assigned to `password`, `token`, `secret`, `api_key` or `Authorization`, `Bearer` tokens, JSON
web tokens, and any 32+ character run of key-like characters). This is a best-effort scrub of
free text: read a log before you share it.

| Field | Description |
|---|---|
| `unit` | One of the kiosk's systemd units (a list in the UI): `ha-kiosk.service`, `ha-kiosk-display.service`, `ha-kiosk-bridge.service`, `ha-kiosk-swap.service`, `ha-kiosk-voice.service`, `ha-kiosk-intercom.service`, `ha-kiosk-capture.service`. |
| `lines` | 1 to 5000, default 100. |

```yaml
action: brilliant_kiosk.logs
data:
  config_entry_id: 01J0EXAMPLE
  unit: ha-kiosk-bridge.service
  lines: 200
response_variable: bridge_log
```

### `brilliant_kiosk.install_version`: install a release

Installs a kiosk release with `ha-kiosk-setup`. Needs **Manage installs** on and a panel that
already has the kiosk: on a panel whose install state is *Not installed*, this action and
`update.install` are refused (`not_installed`), because a first install needs the Home Assistant
URL, a token and the MQTT settings, which only the setup dialog asks for. To install on such a
panel, remove it and add it again.

| Field | Description |
|---|---|
| `tag` | The GitHub release tag (`v1.4.0`, `nightly-20261108`) **or** the bundle version (`v1.4.0-nightly.20261108.g89ec239`). |

A stable release must be in the pinned table. A nightly is installed only if the panel's
**Release channel** is **Nightly**, and is verified only against its release's `.sha256` file,
which is weaker (see [Data update](#data-update)).

```yaml
action: brilliant_kiosk.install_version
data:
  config_entry_id: 01J0EXAMPLE
  tag: v1.4.0
```

## Repairs

| Repair | When | Fix |
|---|---|---|
| **Panel settings differ between the running and the saved copy** | `/etc/ha-kiosk/kiosk.env` (what the kiosk runs with) and `/var/lib/ha-kiosk/config/kiosk.env` (what `ha-kiosk-setup` rebuilds it from on a reinstall or reapply) disagree, for example after a hand edit on the panel. A reinstall would drop the difference. | Submit writes the running values to both copies; nothing is restarted. |
| **Panel settings differ and some cannot be fixed from Home Assistant** | As above, but a running value contains a character that is not allowed. Root shell scripts read that file, so the integration never rewrites such a line. | No fix button. On the panel over SSH: edit the named keys in `/etc/ha-kiosk/kiosk.env` to remove the character (the issue then becomes the fixable one above), or run `ha-kiosk-setup reapply` to rebuild the running copy from the saved one. |
| **The MQTT provider could not be applied** | Changing the MQTT provider option needed a settings change on the panel, which failed, or a poll found the panel running a different provider than the option says. | Submit applies the chosen provider again and shows the panel's reason if it still refuses. |

A changed SSH host key is not a repair issue but a re-authentication (Devices & services). The
issues of a panel are removed with its entry.

## Data update

Home Assistant **polls** each panel every **60 seconds** with **one SSH command** over one
persistent connection. It returns the panel's health (uptime, load, memory, kiosk version, unit
states, backlight), both copies of `kiosk.env` with their checksums, the runtime overrides and
the files `ha-kiosk-setup status` reads (state, current and previous version, heartbeat, last
result). The poll never starts the setup tool's Python interpreter; the full `status` is used
for setup and after installs. The section markers carry a random per-command nonce, so no file
content can forge one. The MQTT device link (`via_device`) is re-checked on every poll.

The **GitHub releases** list is refreshed every **6 hours** by one coordinator shared by all
panels, with two anonymous API requests (the newest 30 releases and the release GitHub marks
*Latest*, so the newest stable is found however many nightlies were published since). Downloads
are streamed and limited to 50 MiB.

If the panel cannot be reached its entities become unavailable (one log line, and one when it is
back). A rejected password or a changed host key starts re-authentication.

**Verification of what is installed.** *Stable* releases are verified against the pinned SHA-256
and size in the integration. *Nightly* releases cannot be pinned in advance: they are installable
only on the nightly channel and are verified against the release's own `.sha256` asset. That
proves the download is intact, not who published it; do not use the nightly channel on a panel
you cannot afford to break. Every install is also verified on the panel by `ha-kiosk-setup`,
which rolls back if the new release does not come up.

## Examples

Dim the panel and turn the screensaver off at bedtime, restore it in the morning:

```yaml
automation:
  - alias: Panel bedtime
    triggers:
      - trigger: time
        at: "22:00:00"
    actions:
      - action: brilliant_kiosk.set_env
        data:
          config_entry_id: 01J0EXAMPLE
          values: {DAY_LEVEL: 3, SCREENSAVER: false}
  - alias: Panel morning
    triggers:
      - trigger: time
        at: "06:30:00"
    actions:
      - action: brilliant_kiosk.set_env
        data:
          config_entry_id: 01J0EXAMPLE
          values: {DAY_LEVEL: 15, SCREENSAVER: true}
```

Notify when the kiosk service stops (use your own entity ids and entry id):

```yaml
automation:
  - alias: Kiosk stopped
    triggers:
      - trigger: state
        entity_id: binary_sensor.kitchen_brilliant_kiosk_kiosk
        to: "off"
        for: "00:02:00"
    actions:
      - action: brilliant_kiosk.logs
        data:
          config_entry_id: 01J0EXAMPLE
          unit: ha-kiosk.service
          lines: 50
        response_variable: log
      - action: notify.mobile_app_phone
        data:
          title: Panel kiosk stopped
          message: "{{ log.logs[-500:] }}"
```

Update when a new release of the panel's channel is offered:

```yaml
automation:
  - alias: Update panel kiosk
    triggers:
      - trigger: state
        entity_id: update.kitchen_brilliant_kiosk_kiosk
        to: "on"
    actions:
      - action: update.install
        target:
          entity_id: update.kitchen_brilliant_kiosk_kiosk
```

Set every panel's sky to the cheaper `sky` mode:

```yaml
action: brilliant_kiosk.set_env
data:
  config_entry_id: 01J0EXAMPLE
  values: {WEATHER_FX: sky}
  restart: true
```

## Known limitations

* **Home Assistant version.** 2026.3.1 or newer; older versions neither show the integration's
  brand images nor are tested.
* **Discovery.** Panels are discovered from the kiosk's retained `brilliant_kiosk/<node>/kiosk_state`
  and `brilliant_kiosk/<node>/discovery` topics. Only a kiosk whose state is `active` is offered;
  `not_installed`, `disabled` and `failed` (left by `ha-kiosk-setup` when the kiosk is removed,
  switched off or fails) do not make a panel appear, add it by hand. The address is prefilled
  only from `discovery`, which needs a kiosk nightly newer than 20261109; older kiosks are offered
  by node id. A panel with a custom `MQTT_BASE` is not discovered. The panel found at the address
  you type must be the discovered one (same Brilliant device id), otherwise the flow stops.
* **No voice (Wyoming) check.** The integration cannot tell you that a panel running the voice
  satellite is missing from your Wyoming integration. Add **Wyoming Protocol** yourself with the
  panel's address and port `10700` (`VOICE_PORT`).
* **Keys.** Only unencrypted private keys are supported; keys and passwords are stored in the
  config entry like any integration's credentials.
* **Values.** Settings values cannot contain `'`, `"`, `$`, a backtick, a backslash or control
  characters.
* **Releases.** A stable release is installable only if its checksum is pinned in the installed
  version of the integration; a brand-new stable release needs an integration update first.
  Nightly verification is weaker (see above).
* **First install needs the dialog.** `install_version` and the update entity refuse a panel
  without the kiosk; remove and re-add the panel to install.
* **Overrides race.** The kiosk rewrites `overrides.env` without the setup lock; a switch toggled
  in the same few milliseconds as a `set_env` that purges that override can be lost.
* **One writer.** Settings changes read the files, edit and write them back; if someone else
  changes `kiosk.env` in between, the write is refused and retried once, then reported as an
  error.
* The panel's SSH service must stay reachable; the integration cannot enable it.

## Supported devices

Brilliant Control panels (single-gang, no physical slider) running Brilliant OS v26.10.21.1
(Yocto kirkstone, kernel 5.15), with or without the kiosk installed (a panel without it can be
installed or monitored). Other Brilliant models and firmware versions may work but are untested;
see the [manual's troubleshooting section](MANUAL.md#9-troubleshooting). Panels are reached over
the network; no extra hardware is needed.

## Supported functions

* Health and install state sensors; per-service running state.
* Settings read and write with validation, reconciliation of the two copies and override
  handling.
* Service restart, panel reboot, redacted journal logs for the kiosk's units.
* Brilliant message-bus read and write.
* Kiosk install (first install from the setup dialog), update, specific version, rollback and
  reapply through `ha-kiosk-setup`, with stable and nightly channels.
* Repair flows for diverged settings and for MQTT provider changes that could not be applied.
* Diagnostics with the panel's address, ids, logins and secrets removed.
* Config flow with host key pinning, password or private key, re-authentication,
  reconfiguration, MQTT discovery with address prefill, and options.

## Troubleshooting

* **Cannot connect.** Check the panel's address, that SSH is enabled and that Home Assistant can
  reach the SSH port (22 by default; Reconfigure changes it). The integration retries by itself.
* **Invalid authentication.** The password or key was rejected: Home Assistant asks again
  (**Reauthenticate**).
* **The panel's host key changed.** Home Assistant asks you to sign in again (Devices &
  services) and shows the old and the new fingerprint. Confirm only if you expect the change (a
  reset or re-flash).
* **"Could not read the Brilliant device id" when adding a panel.** Brilliant's own software must
  be running; it restarts for a minute or two after a reboot or when the kiosk is switched on or
  off. Wait and try again.
* **A discovered panel is not offered, or appears without an address.** Only a running kiosk is
  offered; the address needs a kiosk nightly newer than 20261109 and the default `MQTT_BASE`.
  Add the panel by hand otherwise.
* **An update failed.** The message says which case it was: not verified (nothing installed),
  settings rejected (nothing changed), rolled back to the previous version (running again), or
  the stock screen restored (no earlier version could start; install the kiosk again).
* **"not in this integration's list of verified releases".** Update the integration, or install
  by hand ([manual, section 8](MANUAL.md#8-maintenance-updates-channels-rollback-uninstall)).
* **"is a nightly build".** Set the panel's **Release channel** to Nightly first.
* **"Install management is off for this panel".** Turn on **Manage installs** in the panel's
  options.
* **A repair says the running and saved settings differ.** Use the repair's fix to write the
  running values to both copies; otherwise a reinstall drops the difference.
* **A repair says some settings "cannot be fixed from Home Assistant".** The running copy has a
  value with a character that is not allowed. On the panel over SSH, either edit the named keys
  in `/etc/ha-kiosk/kiosk.env` to remove the character (the next poll turns the repair into the
  fixable one), or run `/var/lib/ha-kiosk/ha-kiosk-setup reapply`, which rebuilds the running copy
  from the saved one.
* **An action says the settings changed while it ran.** Another process edited `kiosk.env`; try
  again.
* **`set_env` refuses `KIOSK_MQTT` or `MQTT_DEVICE_ID`.** Change the MQTT provider in the panel's
  options instead.
* **Logs and diagnostics.** Use the `logs` action, or **Download diagnostics** on the device.
  Enable debug logging for `custom_components.brilliant_kiosk`, `pybrilliantkiosk` and `asyncssh`
  for more.

## Use cases

* A house with several panels: see every panel's kiosk version and service state at a glance, and
  update them all from the update entities.
* A new panel: switch on SSH, add it to Home Assistant, and let the setup dialog install the
  kiosk; no computer with this repository needed.
* Day and night schedules: change brightness and screensaver settings from automations without
  SSH.
* Support: pull the logs of a misbehaving service into a notification or a dashboard card.
* Safe experiments: try a nightly on one panel (Nightly channel) and roll back from Home
  Assistant if it misbehaves.
* Fleet hygiene: be told when a panel's running settings differ from the saved ones before a
  reinstall silently drops them.
