# Brilliant Kiosk: Home Assistant integration

The **Brilliant Kiosk** integration (`brilliant_kiosk`) manages Brilliant panels that run this
project's kiosk from Home Assistant: it shows their health, edits their settings, restarts their
services, reads their logs and installs kiosk updates. It is a custom integration (installed from the Marketplace or HACS, see
[Installation](#installation)), one
config entry per panel, and it talks to the panel over SSH with the typed library
[`pybrilliantkiosk`](../lib/pybrilliantkiosk/README.md).

The panel's *entities* (the wired light, motion, screensaver switches, sensors) are published by
the kiosk itself over MQTT discovery ([manual, section 2.7](MANUAL.md#27-connect-the-panels-to-home-assistant-over-mqtt-optional)): wired light, motion, display state, screensaver and night
switches, gestures, voice, intercom, capture, security. This integration adds the management side and links its
device to the MQTT device (`via_device`), so both appear together.

Contents: [Description](#high-level-description) · [Installation](#installation) ·
[Removal](#removal) · [Configuration parameters](#configuration-parameters) ·
[Installation parameters](#installation-parameters) · [Actions](#actions) ·
[Entities](#entities) · [Repairs](#repairs) · [Data update](#data-update) · [Examples](#examples) · [Known limitations](#known-limitations) ·
[Supported devices](#supported-devices) · [Supported functions](#supported-functions) ·
[Troubleshooting](#troubleshooting) · [Use cases](#use-cases)

## High-level description

A Brilliant panel is a wall light switch with a touchscreen. This project replaces its stock
touchscreen app with a native Home Assistant UI (the *kiosk*), installed and kept up to date by
an on-panel tool, `ha-kiosk-setup`. The integration is the Home Assistant side of that tool:

* **Monitor** each panel: kiosk version, install state, memory, load, and whether each kiosk
  service is running.
* **Change settings** (`kiosk.env`) from automations, scripts and the UI, safely: values are
  validated, both copies of the file are kept in step, and nothing is written while an install
  is running.
* **Operate**: restart a service or reboot the panel, read (redacted) service logs, read and write
  Brilliant message-bus variables.
* **Update**: an update entity offers new kiosk releases (stable or nightly channel) and installs
  them with `ha-kiosk-setup`, which verifies the bundle, keeps the previous release and rolls
  back if the new one does not come up. Buttons roll back or reapply. A panel that has SSH but
  no kiosk can be installed from the setup dialog.
* **Repair**: repair issues tell you when the panel's two copies of its settings disagree or an
  MQTT provider change could not be applied, and offer a fix.

Everything the integration does on a panel goes through an allow-list: a fixed set of systemd
units, validated setting names and values, and `ha-kiosk-setup`'s own commands. It never runs
free-form shell commands that you (or an automation) supply. All actions are for
administrators only.

## Installation

You need a panel that already has SSH switched on (see the [manual, section 2.1](MANUAL.md#21-turn-on-ssh-on-the-panel)) and
Home Assistant **2026.3.1 or newer** (the first release that shows an integration's own
`brand/` images and the version the tests run against).

The integration depends on the **MQTT** integration (it discovers panels from the kiosk's retained
state topic). Set MQTT up first and make sure your panels publish to the same broker.

### Marketplace (Home Assistant 2026.11 and newer)

1. **Settings, Marketplace, Add from link**, enter `MikeI-Code/ha-brilliant-kiosk`.
2. Install **Brilliant Kiosk** and restart Home Assistant when asked.
3. **Settings, Devices & services, Add integration, Brilliant Kiosk**.

The integration is distributed from
[github.com/MikeI-Code/ha-brilliant-kiosk](https://github.com/MikeI-Code/ha-brilliant-kiosk); it
is developed, tested and released from this repository. HACS does not run on Home Assistant
2026.11 and newer.

### HACS

For Home Assistant 2026.3.1 to 2026.10.

1. In HACS, open the three-dot menu, **Custom repositories**, add
   `https://github.com/MikeI-Code/ha-brilliant-kiosk` with category **Integration**.
2. Search for **Brilliant Kiosk**, download it, and restart Home Assistant.
3. Go to **Settings, Devices & services, Add integration**, choose **Brilliant Kiosk**.
   A panel that is already publishing over MQTT is also offered as a discovered device.

### Manual

1. Copy `custom_components/brilliant_kiosk/` from this repository into your Home Assistant
   configuration directory (`<config>/custom_components/brilliant_kiosk/`).
2. Restart Home Assistant. The library `pybrilliantkiosk` is installed automatically from the
   requirement in `manifest.json`.
3. Add the integration as in step 3 above.

### Adding a panel

A panel whose kiosk publishes over MQTT is offered as a **discovered** device under *Devices &
services*. With a kiosk nightly newer than 20261109 the panel's address is already filled in and
the card says which room it is (*Kitchen Brilliant panel found*). Older kiosks are offered by
node id without an address. Or click **Add integration** and enter everything.

1. Enter the panel's **host** (name or IP address), the SSH **port** (22 unless you changed it),
   the SSH **username** (normally `root`) and a
   **password**, a **private key**, or both.
2. The integration connects once to read the panel's SSH host key and shows its fingerprint.
   Compare it with the panel (`ssh-keygen -l -E sha256 -f /etc/ssh/ssh_host_ed25519_key.pub`), or
   paste the SHA256 fingerprint into **Expected host key fingerprint** to skip this step. **No password or key is
   sent before you confirm the key**, and afterwards only to a panel that presents it.
3. The integration reads the panel's Brilliant device id and room name, and its kiosk state. If
   the kiosk is not installed on the panel, you are offered to install it
   ([installation parameters](#installation-parameters)). Untick **Install the kiosk now** to add
   the panel for monitoring only.

The panel appears as a device named **"\<Room\> Brilliant kiosk"** (for example *Kitchen
Brilliant kiosk*), or *Brilliant kiosk \<host\>* when Brilliant has no room name for it.

## Removal

1. **Settings, Devices & services, Brilliant Kiosk**, three-dot menu of the panel,
   **Delete**. This removes the panel's device and entities and any repair issues of that panel.
2. The kiosk on the panel is **not** removed: deleting the entry only stops Home Assistant managing
   it. To remove the kiosk too, run the **Uninstall** (`ha-kiosk-setup uninstall` or
   `scripts/uninstall.sh`, see the [manual, section 8](MANUAL.md#8-maintenance)) before or after.
3. Marketplace or HACS installs: remove the integration there and restart Home Assistant. Manual
   installs: delete `custom_components/brilliant_kiosk/`.

## Configuration parameters

Set when adding a panel (and changed with **Reconfigure** or **Reauthenticate**):

| Parameter | Description |
|---|---|
| Host | Name or IP address of the panel. **Reconfigure** changes it (same panel, same pinned host key). |
| SSH port | The panel's SSH port, default 22. Stored in the config entry and used for every connection (the host key scan, the login, polling, a first install). **Reconfigure** changes it together with the host. Entries made before this field existed use 22. |
| Username | SSH user, normally `root`. |
| Password | SSH password. Optional if you give a private key. |
| Private key | The whole text of an **unencrypted** OpenSSH or PEM private key; the panel needs the matching public key in `authorized_keys`. Optional if you give a password. |
| Expected host key fingerprint | Optional `SHA256:...` fingerprint; if given and it matches, the confirmation step is skipped. MD5 fingerprints are refused with an explanation: they are not safe to pin. |

The SSH host key is **pinned** when you confirm it. If it ever changes (the panel was reset or
re-flashed, or something else answers at that address), polling stops and Home Assistant starts a
**re-authentication** (in Devices & services, not in Repairs): it shows the pinned and the new fingerprint, both as `SHA256:...` with the new key's type beside it, and pins the new key only after
you tick the confirmation box and enter your credentials. Reconfiguring to an address that
presents another key works the same way.

**Options** (the cog of a panel):

| Option | Description |
|---|---|
| MQTT provider | `Built-in kiosk MQTT` (`KIOSK_MQTT=1`, the default) or `brilliant-mqtt` (`KIOSK_MQTT=kiosk`). MQTT is never switched off. Changing it writes `KIOSK_MQTT` on the panel and restarts the bridge. If that fails, a repair issue offers to try again, the integration keeps using the provider the panel actually runs (no reload for the refused one), and every poll compares the panel with the option: a disagreement (a failed change, or an edit on the panel) raises the repair, agreement clears it. |
| brilliant-mqtt device id | Required for `brilliant-mqtt` (`MQTT_DEVICE_ID`); this panel's device is linked to that MQTT device. |
| Manage installs | Allow installing, updating and rolling back the kiosk from Home Assistant. Off by default when brilliant-mqtt manages the panel. |

The **Release channel** select entity (stable or nightly) chooses which releases the update
entity offers and whether a nightly may be installed (see [Data update](#data-update)). On first
setup it follows what is installed: a panel running a nightly starts on the nightly channel, any
other on stable. Your later choice is never overwritten.

### How a panel is identified

A panel is identified by **Brilliant's own device id**, which never changes. It is read from the
stock firmware's message bus with Brilliant's Python and libraries, using nothing of the kiosk, so
it is available before the kiosk is installed and after it was uninstalled or the panel was
re-flashed. The config entry's unique id (and the Home Assistant device) is
`brilliant_<first 12 characters of the device id>`, the same node name the kiosk's MQTT discovery
uses.

* The SSH **host key is never an identity**: it changes on a re-flash. It only authenticates the
  connection (pinning).
* If the device id cannot be read when adding a panel, the panel is **not added**; the flow ends
  with an explanation (the bus restarts for a minute or two when the kiosk is switched on or off,
  and after a reboot; wait and try again).
* **Reauthenticate** and **Reconfigure** compare the id only when both sides have one, so they work
  after an uninstall or a re-flash, and refuse a different panel behind the same address.
* Entries (config entry version 1) made while the id could not be read have the host key
  fingerprint as unique id. On the first successful poll they are converted to the device id,
  together with their device and entities (names, history and settings are kept). If another entry
  already has that id, a warning is logged and nothing is merged.

## Installation parameters

Asked only when the kiosk is not yet installed on the panel. Everything is typed here: **nothing
is read from other integrations** (in particular not from Home Assistant's MQTT entry).

| Parameter | Description |
|---|---|
| Install the kiosk now | Untick to add the panel for monitoring only. |
| Home Assistant URL for the panel | Address the panel can reach, starting with `http://` or `https://`. Prefilled with your internal URL when there is one. |
| Long-lived access token | Created on your Home Assistant profile page (Security tab), ideally for an administrator; stored on the panel only (`/etc/ha-kiosk/token` and `/var/lib/ha-kiosk/config/token`, readable by root). Any characters. |
| MQTT broker host | Your broker's host name or IP address. The panel publishes its entities there. Port 1883 is assumed; change `MQTT_PORT` afterwards with the `set_env` action. |
| MQTT user, MQTT password | Optional broker login. |

**Characters.** The panel keeps settings in a file that root shell scripts read, so values are
written as `KEY="value"` and must not contain a single quote `'`, a double quote `"`, a dollar sign
`$`, a backtick `` ` ``, a backslash `\` or control characters. This applies to the Home Assistant
URL, the MQTT host, user and **password** (a broker password with one of these characters cannot be
used; change it on the broker) and to every value you write with `set_env`. The form refuses such
values before anything is downloaded. Spaces and `; | & > ( ) #` are fine.

**What gets installed.** Only the latest *stable* release, and only if its SHA-256 and size are in
the table pinned inside the integration (`custom_components/brilliant_kiosk/pinned.py`). That table
is a trust-on-first-use snapshot: the v1.4.0 entry was taken from GitHub on 2026-11-09 and checked
three ways (the API's asset digest and size, the `.sha256` asset, and a local `sha256sum` of the
download). It is not independent of GitHub at that moment, but after it someone who could replace
a release on GitHub could replace its `.sha256` file too, and still not the table you installed
with the integration. If the newest release is not in
the table yet, the install is refused with an explanation: update the integration, or untick
**Install the kiosk now** and install by hand ([manual](MANUAL.md#24-install)). The bundle is also verified
again on the panel by `ha-kiosk-setup`, with the pinned size. The settings written to the panel
are the bundle's own `kiosk.env.example` with your URL, token and MQTT values applied, so every
default (including `WEATHER_FX`) is the one the release ships.

A first install takes a few minutes (the panel restarts its display services); the dialog shows
progress and the result.

## Actions

All actions are **for administrators only**: they run commands as root on a panel, so a call made
by a user who is not an administrator is refused (automations and scripts are not affected). All
actions need the **panel** (`config_entry_id`, picked from a list in the UI). They return
translated errors; a value the panel would refuse raises a validation error and changes nothing.

### `brilliant_kiosk.set_env`: set settings

Merges settings into the panel's `kiosk.env`, atomically, and keeps comments and order.

| Field | Description |
|---|---|
| `values` | Mapping `KEY: value` of settings to set. Booleans are written as `1`/`0`. Names must be settings `ha-kiosk-setup` knows ([manual, section 7](MANUAL.md#7-configuration-reference)); values follow the [character rules](#installation-parameters). |
| `delete` | List of setting names to remove. |
| `restart` | Restart `ha-kiosk.service` afterwards (default off). The display service is restarted by the number entities, not here. |

`set_env` writes both copies the panel keeps (`/etc/ha-kiosk/kiosk.env` and
`/var/lib/ha-kiosk/config/kiosk.env`).

`KIOSK_MQTT` and `MQTT_DEVICE_ID` are refused (set or delete): they belong to the **MQTT provider**
option, so change them in the panel's options (Configure), which keeps the options and the panel
in step and never switches MQTT off. Runtime overrides (the switches published by the
kiosk) win over `kiosk.env`, so every override for a key you set or delete is purged; the change
is evaluated against what the panel effectively uses. If the panel's running copy of
`/etc/ha-kiosk/kiosk.env` contains a value that cannot be written safely (for example a hand-edited
`HA_URL` with a `$`), the action **fails naming the keys** and writes nothing: such a line is never
rewritten and never replaced by the saved value. Fix it on the panel, or set that key explicitly in
the same call.

```yaml
action: brilliant_kiosk.set_env
data:
  config_entry_id: 01J0EXAMPLE
  values:
    DAY_LEVEL: 12
    SCREENSAVER: false
    SLEEP_START: "22:30"
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

`bus_get` returns the variables of a Brilliant peripheral (`{"variables": [{"name", "value",
"read_only"}]}`); `bus_set` writes externally settable ones.

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

Returns `{"logs": "<text>"}`, the last journal lines of one kiosk service, **redacted** before they
leave Home Assistant: the panel's address, node and device ids and logins (and the values of
settings that name them) are replaced by `**REDACTED**` exactly as in the diagnostics, and so is
anything that looks like a secret: values assigned to `password`, `token`, `secret`, `api_key` or
`Authorization`, `Bearer` tokens, JSON web tokens (a Home Assistant long-lived access token is
one) and any 32+ character run of key-like characters. This is a best-effort scrub of free text,
not a guarantee: read a log before you share it.

| Field | Description |
|---|---|
| `unit` | One of the kiosk's systemd units (a list in the UI): `ha-kiosk`, `ha-kiosk-display`, `ha-kiosk-bridge`, `ha-kiosk-swap`, `ha-kiosk-voice`, `ha-kiosk-intercom`, `ha-kiosk-capture` (all with `.service`). |
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

Installs a kiosk release with `ha-kiosk-setup`; needs **Manage installs** on and a panel that
already has the kiosk. On a panel without it (the install state sensor says *Not installed*) the
action, and `update.install`, are refused with the `not_installed` error: a first install needs the
Home Assistant URL, a token and the MQTT settings, which only the install step of the setup dialog
asks for. To install on such a panel, remove it and add it again.

| Field | Description |
|---|---|
| `tag` | The GitHub release tag (`v1.4.0`, `nightly-20261108`) **or** the bundle version (`v1.4.0-nightly.20261108.g89ec239`), as the update entity accepts either. |

A stable release must be in the pinned table. A nightly is installed only if the panel's
**Release channel** is **Nightly**, and is checked only against its release's `.sha256` file,
which is weaker (see [Data update](#data-update)).

```yaml
action: brilliant_kiosk.install_version
data:
  config_entry_id: 01J0EXAMPLE
  tag: v1.4.0
```

### Entities

Per panel (device **\<room\> Brilliant kiosk**, for example *Kitchen Brilliant kiosk*; the room is
Brilliant's own name for it, read from the panel once when the entry is set up, and a panel whose
room is not known is called *Brilliant kiosk \<host\>*; entries made earlier as *Brilliant panel
\<host\>* are renamed the same way at their next setup, unless you gave the entry or the device a
name yourself; entity ids do not change): binary sensors for each kiosk service
(`ha-kiosk`, display and bridge enabled by default, the others disabled), sensors for kiosk
version, install state, memory used and load (load disabled by default), numbers for **Dim level**
and **Minimum level**, a **Release channel** select, buttons (**Restart kiosk**, **Reboot panel**,
and disabled by default: **Restart voice**, **Reapply kiosk**, **Roll back kiosk**; the last two
only with Manage installs) and an **update** entity.

The full list, on the device **"\<Room\> Brilliant kiosk"**:

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

## Repairs

| Repair | When | Fix |
|---|---|---|
| **Panel settings differ between the running and the saved copy** | `/etc/ha-kiosk/kiosk.env` (what the kiosk runs with) and `/var/lib/ha-kiosk/config/kiosk.env` (what `ha-kiosk-setup` rebuilds it from on a reinstall or reapply) disagree, for example after a hand edit on the panel. A reinstall would drop the difference. | Submit writes the running values to both copies; nothing is restarted. |
| **Panel settings differ and some cannot be fixed from Home Assistant** | As above, but a running value contains a character that is not allowed. Root shell scripts read that file, so the integration never rewrites such a line. | No fix button. On the panel over SSH: edit the named keys in `/etc/ha-kiosk/kiosk.env` to remove the character (the issue then becomes the fixable one above), or run `ha-kiosk-setup reapply` to rebuild the running copy from the saved one. |
| **The MQTT provider could not be applied** | Changing the MQTT provider option needed a settings change on the panel, which failed, or a poll found the panel running a different provider than the option says. | Submit applies the chosen provider again and shows the panel's reason if it still refuses. |

A changed SSH host key is not a repair issue but a re-authentication (Devices & services). The
issues of a panel are removed with its entry.

## Data update

Home Assistant **polls** each panel every **60 seconds** with **one SSH command** that returns the
panel's health (uptime, load, memory, kiosk version, unit states, backlight), both copies of
`kiosk.env` with their checksums, the runtime overrides and the files `ha-kiosk-setup status` reads
(state, current and previous version, heartbeat, last result), in marked sections. The poll never
starts the setup tool's Python interpreter; the full `status` (enabled flags, releases) is used for
setup and after installs. The section markers carry a random per-command nonce, so no file content
can forge one. Earlier versions needed four commands per poll;
there is now exactly one (a test asserts it). One persistent SSH connection is reused. The MQTT device link (`via_device`) is re-checked on
every poll.

The **GitHub releases** list is refreshed every **6 hours** by one coordinator shared by all
panels, with two anonymous API requests (the newest 30 releases and the release GitHub marks
*Latest*, so the newest stable release is found however many nightlies were published since).
Downloads are streamed and limited to 50 MiB.

If the panel cannot be reached its entities become unavailable (one log line, and one when it is
back). A rejected password or a changed host key starts re-authentication.

**Verification of what is installed.** *Stable* releases are verified against the pinned SHA-256
and size in the integration (a snapshot of GitHub's assets from the day the pin was added,
checked three ways, that GitHub can no longer change). *Nightly* releases cannot be pinned in
advance: they are installable only when you choose the nightly channel and are verified against the
release's own `.sha256` asset. That proves the download is intact, **not who published it**, so it
is a weaker guarantee; do not use the nightly channel on a panel you cannot afford to break. Every install is also
verified on the panel by `ha-kiosk-setup`, which rolls back if the new release does not come up.

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

Notify when the kiosk service stops (use your panel's own entity ids and entry id; the ids
shown here are placeholders):

```yaml
automation:
  - alias: Kiosk stopped
    triggers:
      - trigger: state
        entity_id: binary_sensor.brilliant_panel_kiosk
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

Update when a new stable release is offered (the update entity does the work):

```yaml
automation:
  - alias: Update panel kiosk
    triggers:
      - trigger: state
        entity_id: update.brilliant_panel_kiosk
        to: "on"
    actions:
      - action: update.install
        target:
          entity_id: update.brilliant_panel_kiosk
```

Set a panel's sky to the cheaper `sky` mode (the default of current kiosks):

```yaml
action: brilliant_kiosk.set_env
data:
  config_entry_id: 01J0EXAMPLE
  values: {WEATHER_FX: sky}
  restart: true
```

## Known limitations

* **Home Assistant version.** Home Assistant 2026.3.1 or newer is required (it runs on Python
  3.14); older versions neither show the integration's own brand images nor are tested.
* **Discovery.** A kiosk of the nightly that added it also publishes retained
  `brilliant_kiosk/<node>/discovery` (a kiosk nightly newer than 20261109), JSON with `state`, `host` (the panel's LAN address),
  `device_id` and `room`. The flow then knows which panel it is (a panel you already added is not
  offered again), fills in the address and shows the room (*Kitchen Brilliant panel found*), so you
  only enter the password. Older kiosks publish only the bare `kiosk_state` below; their flow
  works as before, without the address. MQTT discovery uses the default topic
  `brilliant_kiosk/<node>/kiosk_state` and
  offers a panel only while that retained state is `active`: `not_installed`, `disabled` and
  `failed` are what `ha-kiosk-setup` leaves on the broker when the kiosk is removed, switched off
  or fails to start, and such a stale message does not make a panel appear (add it by hand). The
  panel found at the address you then type must be the discovered one (same Brilliant device id),
  otherwise the flow stops. A
  panel with a custom `MQTT_BASE` is not discovered, add it manually.
* **No voice (Wyoming) check.** The integration does not look at your Wyoming integration, so it
  cannot tell you that a panel running the voice satellite (`KIOSK_VOICE=1`) is missing from it.
  Add the **Wyoming Protocol** integration yourself with the panel's address and port `10700`
  (`VOICE_PORT`).
* **Keys.** Only unencrypted private keys are supported (no passphrase prompt); the keys and
  password are stored in the config entry like any integration's credentials.
* **Values.** Settings values cannot contain `'`, `"`, `$`, a backtick, a backslash or control
  characters (see [Installation parameters](#installation-parameters)).
* **Releases.** A stable release is installable only if its checksum is pinned in the installed
  version of the integration; a brand-new release needs an integration update first.
* **First install needs the dialog.** `install_version` and the update entity refuse a panel
  without the kiosk; remove and re-add the panel to install.
* **Overrides race.** The kiosk rewrites `overrides.env` without the setup lock; a switch toggled
  in the same few milliseconds as a `set_env` that purges that override can be lost.
* **One writer.** Settings changes read the files, edit and write them back; if someone else
  changes `kiosk.env` in between, the write is refused and retried once, then reported as an error.
* **Nightly channel** verification is weaker (see above).
* The panel's SSH service must stay reachable; the integration cannot enable it.

## Supported devices

Brilliant Control panels (single-gang, no physical slider) running Brilliant OS **v26.10.21.1**
(Yocto kirkstone, kernel 5.15) with this project's kiosk installed. Other Brilliant models and
firmware versions may work but are untested; see the
[manual's troubleshooting section](MANUAL.md#9-troubleshooting). Panels are reached over the network, so no extra
hardware or adapter is needed.

## Supported functions

* Health and install state sensors; per-service running state.
* Settings read and write with validation, reconciliation of the two copies and override handling.
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
* **The panel's host key changed.** Home Assistant asks you to sign in again (Devices & services)
  and shows the old and the new fingerprint. Confirm only if you expect the change (a reset or
  re-flash).
* **"Could not read the Brilliant device id" when adding a panel.** Brilliant's own software must
  be running; it restarts for a minute or two after a reboot or when the kiosk is switched on or
  off. Wait and try again.
* **A discovered panel is not offered, or appears without an address.** Only a running kiosk is
  offered; the address needs a kiosk nightly newer than 20261109 and the default `MQTT_BASE`.
  Add the panel by hand otherwise.
* **An update failed.** The messages say which case it was: not verified (nothing installed),
  settings rejected (nothing changed), rolled back to the previous version (running again), or the
  stock screen restored (no earlier version could start; install the kiosk again).
* **A repair says the running and saved settings differ.** `ha-kiosk-setup` rebuilds
  `/etc/ha-kiosk/kiosk.env` from the saved copy on reinstall, which would drop the difference.
  Use the repair's fix to write the running values to both copies.
* **A repair says some settings "cannot be fixed from Home Assistant".** The running copy,
  `/etc/ha-kiosk/kiosk.env`, has a value with a character that is not allowed (a quote, a
  backslash, `$` or a backtick). Root shell scripts read that file, so the integration never
  rewrites such a line, and the repair has no fix button. Fix it on the panel over SSH, either way:
  * edit the named keys in `/etc/ha-kiosk/kiosk.env` to remove the character; the next poll turns
    the repair into the ordinary drift repair, whose fix writes the values to both copies; or
  * run `ha-kiosk-setup reapply` (`/var/lib/ha-kiosk/ha-kiosk-setup reapply` if it is not on the
    PATH), which rebuilds the running copy from the saved one
    (`/var/lib/ha-kiosk/config/kiosk.env`) and so drops the running values.
* **An install is refused with "not in this integration's list of verified releases".** Update the
  integration, or install by hand ([manual, section 8](MANUAL.md#8-maintenance)).
* **"is a nightly build".** Set the panel's **Release channel** to Nightly first.
* **"Install management is off for this panel".** Turn on **Manage installs** in the panel's
  options.
* **An action says the settings changed while it ran.** Another process edited `kiosk.env`;
  try again.
* **`set_env` refuses `KIOSK_MQTT` or `MQTT_DEVICE_ID`.** Change the MQTT provider in the panel's
  options instead.
* **Logs and diagnostics.** Use the `logs` action, or **Download diagnostics** on the device
  (addresses, ids, logins and secrets are removed). Enable debug logging for
  `custom_components.brilliant_kiosk`, `pybrilliantkiosk` and `asyncssh` for more.

## Use cases

* A house with several panels: see every panel's kiosk version and service state at a glance, and
  update them all from the update entities.
* A new panel: switch on SSH, add it to Home Assistant, and let the setup dialog install the
  kiosk; no computer with this repository needed.
* Day and night schedules: change brightness and screensaver settings from automations without
  opening SSH.
* Support: pull the logs of a misbehaving service into a notification or a dashboard card.
* Safe experiments: try a nightly on one panel (nightly channel), and roll back from Home
  Assistant if it misbehaves.
* Fleet hygiene: be told when a panel's running settings differ from the saved ones before a
  reinstall silently drops them.
