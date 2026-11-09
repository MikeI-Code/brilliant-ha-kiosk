# Brilliant Kiosk: Home Assistant integration

The **Brilliant Kiosk** integration (`brilliant_kiosk`) manages Brilliant panels that run this
project's kiosk from Home Assistant: it shows their health, edits their settings, restarts their
services, reads their logs and installs kiosk updates. It is a custom integration (HACS), one
config entry per panel, and it talks to the panel over SSH with the typed library
[`pybrilliantkiosk`](../lib/pybrilliantkiosk/README.md).

The panel's *entities* (the wired light, motion, screensaver switches, sensors) are published by
the kiosk itself over MQTT discovery. This integration adds the management side and links its
device to the MQTT device (`via_device`), so both appear together.

Contents: [Description](#high-level-description) · [Installation](#installation) ·
[Removal](#removal) · [Configuration parameters](#configuration-parameters) ·
[Installation parameters](#installation-parameters) · [Actions](#actions) ·
[Data update](#data-update) · [Examples](#examples) · [Known limitations](#known-limitations) ·
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
* **Operate**: restart a service or reboot the panel, read service logs, read and write
  Brilliant message-bus variables.
* **Update**: an update entity offers new kiosk releases and installs them with `ha-kiosk-setup`,
  with a rollback button.
* **Repair**: repair issues tell you when the panel's two copies of its settings disagree or an
  MQTT provider change could not be applied, and offer a fix.

Everything the integration does on a panel goes through an allow-list: a fixed set of systemd
units, validated setting names and values, and `ha-kiosk-setup`'s own commands. It never runs
free-form shell commands that you (or an automation) supply.

## Installation

You need a panel that already has SSH switched on (see the [manual](MANUAL.md)) and
Home Assistant **2026.3.1 or newer** (the first release that shows an integration's own
`brand/` images and the version the tests run against).

The integration depends on the **MQTT** integration (it discovers panels from the kiosk's retained
state topic). Set MQTT up first and make sure your panels publish to the same broker.

### HACS

1. In HACS, open the three-dot menu, **Custom repositories**, add
   `https://github.com/MikeI-Code/brilliant-ha-kiosk` with category **Integration**.
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

1. Enter the panel's **host** (name or IP address), the SSH **username** (normally `root`) and a
   **password**, a **private key**, or both.
2. The integration connects once to read the panel's SSH host key and shows its fingerprint.
   Compare it with the panel (`ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub`), or paste the
   fingerprint into **Expected host key fingerprint** to skip this step. **No password or key is
   sent before you confirm the key**, and afterwards only to a panel that presents it.
3. If the kiosk is not installed on the panel, you are offered to install it
   ([installation parameters](#installation-parameters)). Untick **Install the kiosk now** to add
   the panel for monitoring only.

## Removal

1. **Settings, Devices & services, Brilliant Kiosk**, three-dot menu of the panel,
   **Delete**. This removes the panel's device and entities and any repair issues of that panel.
2. The kiosk on the panel is **not** removed: deleting the entry only stops Home Assistant managing
   it. To remove the kiosk too, run the **Uninstall** (`ha-kiosk-setup uninstall` or
   `scripts/uninstall.sh`, see the [manual](MANUAL.md)) before or after.
3. HACS installs: remove the integration in HACS and restart Home Assistant. Manual installs: delete
   `custom_components/brilliant_kiosk/`.

## Configuration parameters

Set when adding a panel (and changed with **Reconfigure** or **Reauthenticate**):

| Parameter | Description |
|---|---|
| Host | Name or IP address of the panel. **Reconfigure** changes it (same panel, same pinned host key). |
| Username | SSH user, normally `root`. |
| Password | SSH password. Optional if you give a private key. |
| Private key | The whole text of an **unencrypted** OpenSSH or PEM private key; the panel needs the matching public key in `authorized_keys`. Optional if you give a password. |
| Expected host key fingerprint | Optional SHA256 or MD5 fingerprint; if given and it matches, the confirmation step is skipped. |

The SSH host key is **pinned** when you confirm it. If it ever changes (the panel was reset or
re-flashed, or something else answers at that address), polling stops and Home Assistant starts a
**re-authentication**: it shows the pinned and the new fingerprint and pins the new key only after
you tick the confirmation box and enter your credentials. Reconfiguring to an address that
presents another key works the same way.

**Options** (the cog of a panel):

| Option | Description |
|---|---|
| MQTT provider | `Built-in kiosk MQTT` (`KIOSK_MQTT=1`, the default) or `brilliant-mqtt` (`KIOSK_MQTT=kiosk`). MQTT is never switched off. Changing it writes `KIOSK_MQTT` on the panel and restarts the bridge; if that fails a repair issue offers to try again. |
| brilliant-mqtt device id | Required for `brilliant-mqtt` (`MQTT_DEVICE_ID`); this panel's device is linked to that MQTT device. |
| Manage installs | Allow installing, updating and rolling back the kiosk from Home Assistant. Off by default when brilliant-mqtt manages the panel. |

The **Release channel** select entity (stable or nightly) chooses which releases the update
entity offers and whether a nightly may be installed (see [Data update](#data-update)).

## Installation parameters

Asked only when the kiosk is not yet installed on the panel. Everything is typed here: **nothing
is read from other integrations** (in particular not from Home Assistant's MQTT entry).

| Parameter | Description |
|---|---|
| Install the kiosk now | Untick to add the panel for monitoring only. |
| Home Assistant URL for the panel | Address the panel can reach, starting with `http://` or `https://`. Prefilled with your internal URL when there is one. |
| Long-lived access token | Created on your Home Assistant profile page; stored on the panel only. Any characters. |
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
is independent of GitHub: someone who could replace a release on GitHub could replace its `.sha256`
file too, but not the table you installed with the integration. If the newest release is not in
the table yet, the install is refused with an explanation: update the integration, or untick
**Install the kiosk now** and install by hand ([manual](MANUAL.md)). The bundle is also verified
again on the panel by `ha-kiosk-setup`, with the pinned size.

## Actions

All actions need the **panel** (`config_entry_id`, picked from a list in the UI). They return
translated errors; a value the panel would refuse raises a validation error and changes nothing.

### `brilliant_kiosk.set_env`: set settings

Merges settings into the panel's `kiosk.env`, atomically, and keeps comments and order.

| Field | Description |
|---|---|
| `values` | Mapping `KEY: value` of settings to set. Booleans are written as `1`/`0`. Names must be settings `ha-kiosk-setup` knows; values follow the [character rules](#installation-parameters). |
| `delete` | List of setting names to remove. |
| `restart` | Restart `ha-kiosk.service` afterwards (default off). The display service is restarted by the number entities, not here. |

`KIOSK_MQTT` cannot be switched off or removed. Runtime overrides (the switches published by the
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

Returns `{"logs": "<text>"}`, the last journal lines of one kiosk service.

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

Installs a kiosk release with `ha-kiosk-setup`; needs **Manage installs** on.

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

Per panel (device **Brilliant panel \<host\>**): binary sensors for each kiosk service
(`ha-kiosk`, display and bridge enabled by default, the others disabled), sensors for kiosk
version, install state, memory used and load (load disabled by default), numbers for **Dim level**
and **Minimum level**, a **Release channel** select, buttons (**Restart kiosk**, **Reboot panel**,
and disabled by default: **Restart voice**, **Reapply kiosk**, **Roll back kiosk**; the last two
only with Manage installs) and an **update** entity.

## Data update

Home Assistant **polls** each panel every **60 seconds** with **one SSH command** that returns the
panel's health (uptime, load, memory, kiosk version, unit states, backlight), both copies of
`kiosk.env` with their checksums, the runtime overrides and the output of
`ha-kiosk-setup status`, in marked sections. Earlier versions needed four commands per poll;
there is now exactly one (a test asserts it). One persistent SSH connection is reused.

The **GitHub releases** list is refreshed every **6 hours** by one coordinator shared by all
panels, with two anonymous API requests (the newest 30 releases and the release GitHub marks
*Latest*, so the newest stable release is found however many nightlies were published since).
Downloads are streamed and limited to 50 MiB.

If the panel cannot be reached its entities become unavailable (one log line, and one when it is
back). A rejected password or a changed host key starts re-authentication.

**Verification of what is installed.** *Stable* releases are verified against the pinned SHA-256
and size in the integration (independent of GitHub). *Nightly* releases cannot be pinned in
advance: they are installable only when you choose the nightly channel and are verified against the
release's own `.sha256` asset. That proves the download is intact, **not who published it**, so it
is a weaker guarantee; do not use the nightly channel on a panel you cannot afford to break.

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

## Known limitations

* **Home Assistant version.** Home Assistant 2026.3.1 or newer is required (it runs on Python
  3.14); older versions neither show the integration's own brand images nor are tested.
* **Discovery.** MQTT discovery uses the default topic `brilliant_kiosk/<node>/kiosk_state`; a
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
[manual's troubleshooting section](MANUAL.md). Panels are reached over the network, so no extra
hardware or adapter is needed.

## Supported functions

* Health and install state sensors; per-service running state.
* Settings read and write with validation, reconciliation of the two copies and override handling.
* Service restart, panel reboot, journal logs for the kiosk's units.
* Brilliant message-bus read and write.
* Kiosk install, update, rollback and reapply through `ha-kiosk-setup`.
* Repair flows for diverged settings and for MQTT provider changes that could not be applied.
* Diagnostics with the panel's address, ids, logins and secrets removed.
* Config flow with host key pinning, password or private key, re-authentication,
  reconfiguration, MQTT discovery and options.

## Troubleshooting

* **Cannot connect.** Check the panel's address, that SSH is enabled and that Home Assistant can
  reach port 22. The integration retries by itself.
* **Invalid authentication.** The password or key was rejected: Home Assistant asks again
  (**Reauthenticate**).
* **The panel's host key changed.** Home Assistant opens a re-authentication that shows the old
  and the new fingerprint. Confirm only if you expect the change (a reset or re-flash).
* **A repair says the running and saved settings differ.** `ha-kiosk-setup` rebuilds
  `/etc/ha-kiosk/kiosk.env` from the saved copy on reinstall, which would drop the difference.
  Use the repair's fix to write the running values to both copies. If it names keys that "cannot
  be fixed from Home Assistant", edit those lines on the panel: they contain a character that is
  not allowed, and the integration never rewrites them.
* **An install is refused with "not in this integration's list of verified releases".** Update the
  integration, or install by hand.
* **An action says the settings changed while it ran.** Another process edited `kiosk.env`;
  try again.
* **Logs and diagnostics.** Use the `logs` action, or **Download diagnostics** on the device
  (addresses, ids, logins and secrets are removed). Enable debug logging for
  `custom_components.brilliant_kiosk` and `pybrilliantkiosk` for more.

## Use cases

* A house with several panels: see every panel's kiosk version and service state at a glance, and
  update them all from the update entities.
* Day and night schedules: change brightness and screensaver settings from automations without
  opening SSH.
* Support: pull the logs of a misbehaving service into a notification or a dashboard card.
* Safe experiments: try a nightly on one panel (nightly channel), and roll back from Home
  Assistant if it misbehaves.
* Fleet hygiene: be told when a panel's running settings differ from the saved ones before a
  reinstall silently drops them.
