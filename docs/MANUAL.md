# Manual

Everything you need to install, configure, use and maintain the Brilliant → Home Assistant panel.

**Contents**

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Using the panel](#3-using-the-panel)
4. [Editing the screens in Home Assistant](#4-editing-the-screens-in-home-assistant)
5. [This switch: local control and gestures](#5-this-switch-local-control-and-gestures)
6. [Display: night mode, screensaver, motion wake, smart dimming](#6-display-night-mode-screensaver-motion-wake-smart-dimming)
7. [Configuration reference](#7-configuration-reference)
8. [Maintenance](#8-maintenance)
9. [Troubleshooting](#9-troubleshooting)
10. [Tools for tinkerers](#10-tools-for-tinkerers)

---

## 1. Requirements

| What | Notes |
|---|---|
| **Brilliant Control panel** | Tested on single-gang panels without a physical slider, Brilliant OS v26.10.21.1. The panel must be on your Wi-Fi. |
| **Home Assistant** | Reachable from the panel on your **local network** (the panel can't use Tailscale/VPN addresses). Tested with HA 2026.10. Sonos, weather and your lights should already be set up in HA. |
| **A computer** to install from | `bash` (Linux, macOS, or Git Bash on Windows) and either OpenSSH (`ssh`) or PuTTY's `plink`. `plink` is used automatically when a password is configured. |
| **Python 3** (optional) | Only for `scripts/publish_layout.py` (creating the editable layout in HA): `pip install websockets`. |

Nothing needs to be installed on the panel itself: it already has Qt 5.15, QtWebSockets and Python.

---

## 2. Installation

### 2.1 Turn on SSH on the panel

Brilliant's software has an official **Root SSH Login** setting, found in the panel's
**Advanced Settings**, so nothing is hacked or exploited. According to Brilliant's support pages:

* Turning it on requires **verifying your identity by email**.
* It is **permanent**: the panel is flagged from then on, and *Device Settings* shows a
  "May Have Had Root SSH Enabled" security status. Brilliant warns that it can affect software
  updates and that it no longer guarantees the software's integrity.
* **Brilliant will never ask you to enable it or for your credentials.** Treat any such request as
  a scam.

See Brilliant's articles [Root SSH](https://support.brilliant.tech/hc/en-us/articles/23152790775195-RootSSH)
and [Security Status on Device Settings](https://support.brilliant.tech/hc/en-us/articles/21213665069851-Security-Status-on-Device-Settings).
The option may not be offered on every panel or firmware version. If you don't see it, check those
articles or contact Brilliant support.

Write down:

* the panel's **IP address** (give it a fixed lease in your router, so it doesn't change), and
* the **root password** it shows.

Check from your computer:

```bash
ssh root@<panel-ip>          # or: plink -ssh root@<panel-ip>
```

The first connection shows the panel's **host-key fingerprint** (`SHA256:…`). Note it: pinning it
in the config protects against a spoofed device on your network.

### 2.2 Get the code and create your private config

```bash
git clone https://github.com/MikeI-Code/brilliant-ha-kiosk.git
cd brilliant-ha-kiosk
mkdir -p .private
```

Everything specific to your home goes in **`.private/`**. It is git-ignored, so it never ends up in
a commit:

| File | Start from | Contents |
|---|---|---|
| `.private/panel1.env` | `config/panel.env.example` | `PANEL_HOST`, `PANEL_PASSWORD`, `PANEL_HOSTKEY` for one panel. Add `panel2.env` and so on for more panels. |
| `.private/kiosk.env` | `config/kiosk.env.example` | Shared settings: HA address, schedule, features (see [§7](#7-configuration-reference)). |
| `.private/panel1.kiosk.env` | *(optional)* | Per-panel settings, appended to `kiosk.env` for that panel: `LOAD_ENTITY`, `ROOM_SPEAKERS`, `ROOM_LIGHTS`, `LIGHT_SENSOR`, gestures. |
| `.private/token` | HA | A long-lived access token (below). |
| `.private/layout.json` | `native/layout.example.json` | *(optional)* A local layout, used when you don't edit the screens in HA. |
| `.private/photos/` | your photos | *(optional)* Screensaver photos (JPEG/PNG), copied to every panel by `install.sh`. |

```bash
cp config/panel.env.example .private/panel1.env
cp config/kiosk.env.example .private/kiosk.env
```

Edit `.private/panel1.env`:

```bash
PANEL_HOST=192.168.1.50
PANEL_USER=root
PANEL_PASSWORD=<root password from the panel>
PANEL_HOSTKEY=SHA256:<fingerprint from 2.1>
```

Edit `.private/kiosk.env`. At minimum set **`HA_URL`** to Home Assistant's **LAN** address:

```bash
HA_URL=http://192.168.1.10:8123
TZ=EST5EDT,M3.2.0,M11.1.0      # the panel's clock runs in UTC; this is US Eastern
```

### 2.3 Create a Home Assistant token

The panel has no on-screen keyboard, so it signs in with a token:

1. In Home Assistant, open your **profile** (bottom-left) → **Security**.
2. Under **Long-lived access tokens**, choose **Create token** and name it, e.g. "Wall panels".
3. Save the token as a single line in `.private/token`.

Use an **administrator** account: live layout updates (section 4) subscribe to an event that only
admins can see. The token is copied to the panel as `/etc/ha-kiosk/token` (mode 600) and never
leaves your network.

### 2.4 Install

```bash
scripts/install.sh panel1
```

The script copies the UI and services to the panel, installs the systemd units, disables the
stock touchscreen app, and starts everything. After about 30 seconds the panel shows the new
interface. Repeat for each panel (`scripts/install.sh panel2`, and so on).

> **Note:** the first install restarts Brilliant's background services once (to apply the
> display drop-in), so the wired light may flicker briefly.

### 2.5 Per-panel settings (recommended)

Create `.private/panel1.kiosk.env` so each panel knows its own room:

```bash
# Kitchen panel
LOAD_ENTITY=light.kitchen_table                        # this panel's own wired light, as HA calls it
ROOM_SPEAKERS=media_player.kitchen                     # three-finger volume
ROOM_LIGHTS=light.kitchen_table,light.kitchen_island   # smart-dimming estimate
# LIGHT_SENSOR=sensor.kitchen_illuminance              # if the room has a light sensor
```

Then run `scripts/install.sh panel1` again.

### 2.6 Make the screens editable in Home Assistant

```bash
pip install websockets
HA_URL=http://192.168.1.10:8123 HA_TOKEN=$(cat .private/token) \
  python3 scripts/publish_layout.py native/layout.example.json
```

This creates an admin-only dashboard called **Panel Layout** (`/panel-layout`). Make sure
`LAYOUT_DASHBOARD=panel-layout` is in `.private/kiosk.env`, re-run `scripts/install.sh panel1`, and
from then on edit the screens in HA (section 4). Pass your own `.private/layout.json` instead of the
example to start from your existing layout.

---

### 2.7 Connect the panels to Home Assistant over MQTT (optional)

Each panel can appear in Home Assistant as a **device** through MQTT discovery. No HA add-on or
custom integration from this project is needed; HA's built-in MQTT integration does the work.

**1. Set up a broker** (once). On Home Assistant OS or Supervised:

1. **Settings → Add-ons → Add-on Store → Mosquitto broker → Install**, then **Start** (turn on
   *Start on boot*).
2. **Settings → Devices & services**: HA discovers **MQTT**. Click **Configure → Submit**.
3. Create a login for the panels: **Settings → People → Users → Add user** (e.g. `brilliant-panels`,
   not an administrator). The Mosquitto add-on accepts Home Assistant users.

Any other broker works too, as long as HA's MQTT integration is connected to it.

**2. Point the panels at it** in `.private/kiosk.env`, then run `scripts/install.sh` for each panel:

```bash
MQTT_HOST=192.168.1.10        # the broker (for the add-on: Home Assistant's IP)
MQTT_USER=brilliant-panels
MQTT_PASSWORD=<that user's password>
```

**What you get**: one device per panel, named after its room (e.g. *Kitchen Brilliant panel*):

| Entity | What it does |
|---|---|
| **Wired light** | The panel's own load: on/off and brightness, controlled locally over Brilliant's bus |
| Load power, Dimmer temperature | Live wattage and dimmer temperature |
| Faceplate / CPU temperature | Diagnostics |
| **Motion** | On when Brilliant's motion logic wakes its screen (clears after `MOTION_CLEAR_SECS`) |
| Display | `awake`, `photos`, `dim` or `asleep` |
| Backlight, Room light level | Current backlight (0–15) and the smart-dimming room light estimate (%) |
| **Screensaver, Night mode, Motion wake, Smart dimming** | Switches that change the panel's behavior at runtime |
| Day brightness | Awake backlight level (1–15) |
| **Wake screen** | Button: wakes the display as if touched |
| **Gesture** | Event entity: every multi-finger gesture (`2_tap`, `3_tap`, `2_swipe_left`, …), for automations |

Switches and the brightness number are stored on the panel in `/var/lib/ha-kiosk/overrides.env`,
which takes precedence over `kiosk.env`, and they survive reboots.

**Example: a three-finger tap on the kitchen panel runs the goodnight scene**

```yaml
automation:
  - alias: Kitchen panel three-finger tap
    triggers:
      - trigger: state                # fires on every gesture (the event's timestamp changes)
        entity_id: event.kitchen_brilliant_panel_gesture
    conditions:
      - condition: state
        entity_id: event.kitchen_brilliant_panel_gesture
        attribute: event_type
        state: "3_tap"
    actions:
      - action: scene.turn_on
        target: { entity_id: scene.goodnight }
```

(Entity IDs follow the device name; check yours under the device page.)

> **brilliant-mqtt.** The community project
> [brilliant-mqtt](https://github.com/joyfulhouse/brilliant-mqtt) also publishes Brilliant panels
> over MQTT (including BLE-mesh devices and a voice satellite). Don't run both on the same panel:
> you'd get duplicate entities for the same load. Its screen and brightness controls drive
> Brilliant's own screen settings, which this kiosk deliberately sidelines (see §6), so they would
> have no effect here.

### 2.8 Universal Devices eisy / Polisy / ISY users

If your lighting runs on a [Universal Devices](https://www.universal-devices.com/) hub (eisy,
Polisy or ISY994 with IoX), the panel can control all of it. The panel talks to Home Assistant, and
Home Assistant has a built-in integration for these hubs:

1. In Home Assistant: **Settings → Devices & services → Add integration → Universal Devices ISY/IoX**.
   It is usually discovered automatically. Enter the hub's address and your IoX admin username and
   password.
2. Your Insteon, Z-Wave and Zigbee devices, scenes, and any programs or variables you choose to
   expose appear as normal Home Assistant entities (`light.*`, `switch.*`, `binary_sensor.*`, ...).
   Devices that come from IoX plugins (PG3 node servers) appear too.
3. Add them to the panel's dashboard in HA's editor (§4) like any other entity. IoX scenes work well
   as tiles: they turn several devices on or off together, the way the hub's own keypads do.

Tips:

* Status updates are pushed from the hub to Home Assistant as they happen, so tiles on the panel
  stay in sync when someone uses a wall keypad.
* To run an IoX program from the panel, expose the program in the integration's options and put its
  button on a dashboard, or call it from a gesture (§5) with an action such as
  `button.press:button.<program>`.
* The panel's own wired light is controlled locally by the panel (§5). To let IoX programs see and
  control the Brilliant panel itself, connect the panels to Home Assistant over MQTT (§2.7) and, if
  you want, mirror its entities into IoX through Home Assistant. A native IoX plugin for Brilliant
  panels is being evaluated.

---

## 3. Using the panel

<p align="center">
  <img src="images/home.png" width="23%" alt="Home">
  <img src="images/lights.png" width="23%" alt="Lights">
  <img src="images/sonos.png" width="23%" alt="Sonos">
  <img src="images/house.png" width="23%" alt="House">
</p>

**Navigating**: tap a tab in the bottom bar, or **swipe left/right** anywhere. Drag up and down to
scroll a page.

**Tiles**

| Tile | Tap | Other |
|---|---|---|
| Light | Toggle | Drag the bar to set brightness (drag to 0 to turn off) |
| Switch | Toggle | |
| Scene / script | Run it | Flashes to confirm |
| All off | Tap twice within 3 s | Turns off every light |
| Thermostat | | −/+ change the setpoint; several taps are combined into one change |
| Media | Play/pause | Artwork, title, volume; "now playing" tiles only appear while playing |
| Sensor / presence | | Live value |

A light tile marked **"this switch"** is the panel's own wired load, controlled locally (section 5).

**Music tab** (works with any Home Assistant media player: Sonos, WiiM/LinkPlay, Music Assistant,
Bluesound, HEOS, Chromecast, ...): pick a room at the top; see album art, title and progress; use shuffle,
previous, play/pause, next and repeat; set volume and mute; use **"Play in these rooms too"** to
group speakers. **Favorites** shows everything you've starred in the Sonos app (SiriusXM channels,
playlists, radio) as tap-to-play tiles, grouped as Sonos groups them. Players without a favorites
folder show the top of their own media library instead (WiiM presets, Music Assistant playlists and
radio, ...). A **Source** row appears for players with inputs (Wi-Fi, Bluetooth, line-in, optical), and
the grouping row only appears for players whose integration supports grouping.

**Connection**: if the panel loses Home Assistant it shows a red *Connecting to Home Assistant…*
banner and reconnects on its own every few seconds. The wired light still works locally.

---

## 4. Editing the screens in Home Assistant

The panels build their screens from the **Panel Layout** dashboard. Open it in HA, click the
pencil, and edit it like any dashboard. When you press **Save**, every panel rebuilds within about
a second. The last layout is cached on each panel, so it boots with your screens even if HA is down.

| In the HA dashboard | On the panel |
|---|---|
| A view (tab at the top of the dashboard) | A tab in the bottom bar: the view's title and icon |
| A view whose **URL** is `music` (or `sonos`) | The Music tab. Every media player on it, from any integration, becomes a speaker in the room picker. |
| **Heading** card | Starts a titled section |
| **Tile** card | A tile. Its kind follows the entity: light, switch/fan/input_boolean, scene/script, climate, media player, sensor, binary sensor. |
| Tile **width** (Layout → columns 12 / 6 / 4 / 3) | Full / half / third / quarter width |
| **Weather forecast** card | The clock and weather header with the forecast. A temperature tile next to it becomes "Inside". |
| **Media control** card | An always-visible player. A media *tile* only shows while something plays. |
| **Button** with action `light.turn_off` on *all* lights | The All-off button (tap twice) |
| **Button** with any other action | An action button that runs it |
| Entities / Grid / Stack cards | Flattened into tiles |

Names and icons come from the card. When a card has no name, the entity's friendly name is used.
Every Material Design icon (`mdi:…`) from HA's icon picker works.

> Prefer files? Leave `LAYOUT_DASHBOARD` empty and edit `.private/layout.json` instead (format:
> `native/layout.example.json`), then re-run `scripts/install.sh`.

---

## 5. This switch: local control and gestures

<img src="images/dim.png" width="30%" align="right" alt="Two-finger dimming readout">

These panels have no physical slider: dimming happens on the touchscreen. A small local service,
**`ha-kiosk-bridge`**, connects to Brilliant's message bus and lets the screen switch and dim the
panel's own wired load **directly**: about 0.1 s per change, with no Home Assistant in the path.

Set `LOAD_ENTITY` to the HA entity of that load (section 2.5). Tiles for it then use local control
and show **"this switch"**.

**Gestures** work anywhere on the screen:

| Gesture | Default action |
|---|---|
| **Two fingers up / down** | Dim this switch's light, with a live percentage on screen |
| **Two-finger tap** | Toggle this switch's light |
| **Three fingers up / down** | Volume of the room's Sonos speakers (`ROOM_SPEAKERS`). Each speaker moves by the same amount, keeping their balance. |
| Three-finger tap, two/three-finger swipes left or right | Unassigned; set your own |

While two or more fingers are down, taps, brightness bars and scrolling ignore the touch, so a
gesture never presses the tile under your fingers.

**Custom gestures**: add lines like these to `kiosk.env` (or a panel's `.kiosk.env`):

```bash
GESTURE_3_TAP=scene.turn_on:scene.goodnight
GESTURE_2_SWIPE_LEFT=media_player.media_next_track:media_player.kitchen
GESTURE_2_SWIPE_RIGHT=media_player.media_previous_track:media_player.kitchen
GESTURE_3_SWIPE_LEFT=page:3
```

| Action | Meaning |
|---|---|
| `load.toggle`, `load.on`, `load.off` | This switch's wired load |
| `page:<n>` | Jump to tab *n* (0 = first) |
| `<domain>.<service>` | Any HA action with no target, e.g. `script.turn_on` |
| `<domain>.<service>:<entity_id>` | Any HA action on an entity, e.g. `light.turn_off:all` |

Setting `GESTURE_2_SWIPE_UP/DOWN` or `GESTURE_3_SWIPE_UP/DOWN` replaces the built-in dimming or
volume gesture.

<br clear="right">

---

## 6. Display: night mode, screensaver, motion wake, smart dimming

<img src="images/saver.jpg" width="30%" align="right" alt="Photo screensaver">

The **`ha-kiosk-display`** service owns the screen:

* **Awake**: full brightness (or the smart-dimming level) while you use it.
* **Screensaver**: after `IDLE_DIM_SECS` (5 min) without a touch during the day, a photo slideshow
  with a clock. Set `SCREENSAVER=0` to just dim instead. See **Choosing the photos** below.
* **Night**: from `SLEEP_START` to `SLEEP_END` (21:00–06:30) the screen is off. A touch wakes it
  for `NIGHT_WAKE_SECS` (3 min).
* **Touch to wake**: the waking touch is swallowed, so it never presses anything. After the
  screensaver or night, the panel returns to the Home tab.
* **Motion wake**: walking up to the panel wakes it (`MOTION_WAKE=day`, `always` or `off`). It uses
  the faceplate's motion sensor, through Brilliant's own logic.
* **Smart dimming**: every minute the brightness follows the room's light level, between
  `MIN_LEVEL` and `DAY_LEVEL`. If you set `LIGHT_SENSOR` to an HA illuminance sensor in the room,
  its lux reading is used (log scale from `LUX_DARK` to `LUX_BRIGHT`). Otherwise the level is
  estimated from the sun's elevation and whether `ROOM_LIGHTS` are on, whichever is brighter. (The
  panel's own light sensor reads 0 on this hardware, and the camera would light its red privacy LED,
  so neither is used.)
* **Power**: the CPU runs at full speed only while the screen is awake, and a 256 MB swap file
  guards against running out of memory.

**Choosing the photos.** Your own photos always win. As soon as there are any, only they are shown:

1. **From your computer**: put JPEG/PNG files in `.private/photos/` and run `scripts/install.sh`.
   Only new or changed files are copied, and files you delete are removed from the panel.
2. **From Home Assistant (easiest from a phone)**: in HA open **Media → My media** and upload
   photos. Every panel checks for new or removed photos every `PHOTO_SYNC_MINS` (30) minutes and
   mirrors them. To use only one folder of My media, set `HA_PHOTO_FOLDER` to its name (folders can
   be created with an add-on such as Samba share or File editor, under `/media`).

With no photos of your own, the panels show the photos added through the Brilliant app (which also
include some of Brilliant's demo albums), then Brilliant's stock art. Photos are resized when shown,
so phone-camera originals are fine.

Brilliant's own services still run their screen-off logic. A systemd drop-in
(`systemd/message_bus-ha-kiosk.conf`) gives them harmless stand-in files, so only this service
drives the real backlight.

<br clear="right">

---

## 7. Configuration reference

All settings live in `.private/kiosk.env` (shared) and `.private/<panel>.kiosk.env` (per panel,
appended last, so it wins). After changing them, run `scripts/install.sh <panel>`.

### Home Assistant and layout

| Setting | Default | Meaning |
|---|---|---|
| `HA_URL` | — | Home Assistant on the LAN, e.g. `http://192.168.1.10:8123` (web mode loads the full URL) |
| `LAYOUT_DASHBOARD` | `panel-layout` | HA dashboard to build the screens from. Empty = use `layout.json`. |
| `KIOSK_MODE` | `native` | `native` (this UI) or `web` (HA's own dashboard in a browser; works, but slow on this CPU) |
| `HA_ZOOM` | `2.0` | Web mode only: page zoom |
| `TZ` / `TZ_LOCAL` | US Eastern | POSIX time zone for the clock and schedule (the panel runs in UTC) |

### Display

| Setting | Default | Meaning |
|---|---|---|
| `SLEEP_START` / `SLEEP_END` | `21:00` / `06:30` | Night window (screen off) |
| `DAY_LEVEL` | `15` | Awake brightness (backlight 0–15) |
| `DIM_LEVEL` | `4` | Idle brightness when `SCREENSAVER=0` |
| `IDLE_DIM_SECS` | `300` | Seconds without a touch before the screensaver |
| `NIGHT_WAKE_SECS` | `180` | How long a touch at night keeps the screen on |
| `SCREENSAVER` | `1` | `1` = photo slideshow, `0` = just dim |
| `PHOTO_LEVEL` | `12` | Screensaver brightness |
| `PHOTO_SECS` | `15` | Seconds per photo |
| `HA_PHOTO_FOLDER` | — (all of My media) | Folder in HA's **My media** to sync screensaver photos from |
| `PHOTO_SYNC_MINS` | `30` | How often to sync photos from HA (`0` = off) |
| `PHOTO_DIRS` | — | Advanced: show exactly these panel folders, ignoring the rules above |
| `MOTION_WAKE` | `day` | Wake on motion: `day`, `always`, `off` |

### Smart dimming

| Setting | Default | Meaning |
|---|---|---|
| `SMART_DIM` | `1` | Follow the room's light level |
| `MIN_LEVEL` | `5` | Brightness in a dark room |
| `LIGHT_SENSOR` | — | HA illuminance sensor (lux) in the room. Overrides the estimate. |
| `LUX_DARK` / `LUX_BRIGHT` | `5` / `300` | Lux mapped to `MIN_LEVEL` / full brightness |
| `ROOM_LIGHTS` | — | Comma-separated lights/switches in the room, for the estimate |
| `SUN_DARK_ELEV` / `SUN_BRIGHT_ELEV` | `-4` / `10` | Sun elevation (°) counted as dark / full daylight |
| `AMBIENT_SECS` | `60` | How often to re-evaluate |

### This switch and gestures

| Setting | Default | Meaning |
|---|---|---|
| `LOAD_ENTITY` | — | HA entity of the panel's own wired load (local control + gestures) |
| `ROOM_SPEAKERS` | — | Comma-separated Sonos `media_player`s for three-finger volume |
| `GESTURE_2_TAP` | `load.toggle` | Two-finger tap |
| `GESTURE_3_TAP` | — | Three-finger tap |
| `GESTURE_<2\|3>_SWIPE_<LEFT\|RIGHT\|UP\|DOWN>` | — | Swipes (UP/DOWN replace dimming/volume) |
| `BRIDGE_PORT` | `8765` | Local bridge port (localhost only) |

### MQTT (Home Assistant devices)

| Setting | Default | Meaning |
|---|---|---|
| `MQTT_HOST` | — | Broker address. Empty = MQTT off. |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USER` / `MQTT_PASSWORD` | — | Broker login (`kiosk.env` is root-only on the panel) |
| `MQTT_BASE` | `brilliant_kiosk` | Topic prefix for state and commands |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | HA discovery prefix |
| `MOTION_CLEAR_SECS` | `60` | How long Motion stays on |
| `NIGHT_MODE` | `1` | Night schedule on/off (also an HA switch) |

### System

| Setting | Default | Meaning |
|---|---|---|
| `CPU_AWAKE_GOV` / `CPU_IDLE_GOV` / `CPU_SLEEP_GOV` | `performance` / `schedutil` / `powersave` | CPU governor per display state |
| `SWAP_MB` | `256` | Swap file size (0 = none; the kernel has no zram) |
| `SWAPPINESS` | `10` | Use swap only under memory pressure |

---

## 8. Maintenance

**Logs**

```bash
scripts/logs.sh panel1 100          # last 100 lines from the UI
```

On the panel itself: `journalctl -u ha-kiosk -u ha-kiosk-display -u ha-kiosk-bridge -f`.

**Live state on the panel**

| File | Contents |
|---|---|
| `/run/ha-kiosk-display/state` | `awake`, `photos`, `dim` or `asleep` |
| `/run/ha-kiosk-display/levels` | Current smart-dimming levels (awake, screensaver) |
| `echo 2 > /run/ha-kiosk-display/page` | Switch the UI to tab 2 (handy for testing) |

**Updating**: `git pull`, then `scripts/install.sh <panel>` for each panel.

**Brilliant OS updates**: over-the-air updates stay enabled. They replace the read-only system
image, while everything this project installs lives in `/etc` and `/var`, which updates keep. If an
update ever re-enables the stock UI, run `scripts/install.sh` again.

**Uninstall**

```bash
scripts/uninstall.sh panel1
```

This stops the HA interface, hands the screen back to Brilliant's services and starts the stock
UI. Files stay in `/etc/ha-kiosk`, so you can switch back with `install.sh`.

**Security notes**

* The HA token is stored only on the panel (`/etc/ha-kiosk/token`, root-only) and in your
  `.private/` folder. Revoke it in HA at any time.
* The local bridge listens on `127.0.0.1` only. Nothing new is exposed to your network.
* Consider changing the panel's root password if you've shared it, and pin the host key.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Red **"Connecting to Home Assistant…"** banner | `HA_URL` must be the **LAN** address the panel can reach (not a Tailscale/VPN `100.x` address or `homeassistant.local`). Check `.private/token`. `scripts/logs.sh panel1` shows `auth invalid` for a bad token. |
| Screen stays **black after a touch** | Check `systemctl status ha-kiosk-display`. Make sure the display drop-in is installed (`/etc/systemd/system/message_bus.service.d/ha-kiosk-display.conf`); re-run `install.sh`. |
| **Clock is wrong** by hours | Set `TZ` (and `TZ_LOCAL`) to your POSIX time zone. |
| **Layout doesn't update** when you save in HA | The token's user must be an HA **administrator**. Check `LAYOUT_DASHBOARD`. The log shows `no live layout updates` if the subscription was refused. |
| A tile shows **Unavailable** | The entity ID in the layout doesn't exist in HA, or the device is offline. |
| An icon is **blank** | Use a Material Design icon name (`mdi:…`). Others aren't in the icon font. |
| **Gestures don't control the light** | Set `LOAD_ENTITY`. Check the bridge: `systemctl status ha-kiosk-bridge`, then `wget -qO- http://127.0.0.1:8765/load` on the panel. |
| **Three-finger volume** does nothing | Set `ROOM_SPEAKERS` to your Sonos `media_player` entity IDs. |
| **Motion wake** never fires | Brilliant's motion detection may be off on your panel. Check with `tools/brilliant_bus.py get faceplate_peripheral`. Use `MOTION_WAKE=off` if it misfires. |
| **Screensaver shows the wrong photos** | Add your own (`.private/photos/` or HA **Media → My media**); then only yours are shown. `journalctl -u ha-kiosk-display` says which source is in use, e.g. `54 screensaver photos (Brilliant app photos)`. |
| New HA photos don't appear | Wait up to `PHOTO_SYNC_MINS`, or restart the bridge (`systemctl restart ha-kiosk-bridge`). `journalctl -u ha-kiosk-bridge` shows `photos: N from HA, +added -removed`. |
| **Favorites empty** on the Music tab | Sonos: star stations and playlists in the Sonos app. Other players: check the player's media browser in HA (Media → pick the player); the tab shows what HA shows there. |
| Panel doesn't appear in HA (MQTT) | Check `MQTT_HOST`/`MQTT_USER`/`MQTT_PASSWORD`. On the panel, `journalctl -u ha-kiosk-bridge` shows `mqtt: connected …` or the error. HA's MQTT integration must be set up. |
| An HA switch (screensaver, night…) "doesn't stick" | It's saved in `/var/lib/ha-kiosk/overrides.env`. Delete that file to go back to `kiosk.env`. |
| Want the stock UI back | `scripts/uninstall.sh panel1` |

---

## 10. Tools for tinkerers

| Tool | Runs on | What it does |
|---|---|---|
| `tools/brilliant_bus.py` | Panel | `list`, `get`, `watch`, `set` on Brilliant's message bus: every peripheral and variable (wired load, faceplate, motion, hardware). Writes are check-and-set and only allowed for settable variables. |
| `tools/inject_touch.py` | Panel | Replays taps and multi-finger swipes into the touchscreen, for testing gestures remotely. |
| `tools/gen_icons.py` | Computer | Regenerates `native/icons.json` from the Material Design Icons CSS. |
| `scripts/publish_layout.py` | Computer | Creates the HA *Panel Layout* dashboard from a `layout.json`. |
| `scripts/publish_dashboard.py` | Computer | Web mode: publishes an HA dashboard YAML. |

```bash
# on the panel
cd /data/switch-embedded
env/bin/python /etc/ha-kiosk/brilliant_bus.py list
env/bin/python /etc/ha-kiosk/brilliant_bus.py get gangbox_peripheral_0
env/bin/python /etc/ha-kiosk/brilliant_bus.py watch faceplate_peripheral movement_detected
```

How the bus works (transport, handshake, methods, check-and-set writes) is documented in
[hardware-and-services.md §6](hardware-and-services.md).

---

*Brilliant → Home Assistant Panel. MIT License © 2026 Mike Ippolito.*
