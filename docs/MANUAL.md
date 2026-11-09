# Manual

How to install, configure, use and maintain the Brilliant HA kiosk: the native Home Assistant
interface for Brilliant smart-home panels.

The kiosk replaces only the panel's touchscreen app. Brilliant's background services keep
running, so the wired light, HomeKit, over-the-air updates and the integrations you link in the
Brilliant app (Ring, Schlage) keep working. Keep your Brilliant account and those links active.

**Contents**

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [Using the panel](#3-using-the-panel)
4. [Editing the screens in Home Assistant](#4-editing-the-screens-in-home-assistant)
5. [This switch: local control and gestures](#5-this-switch-local-control-and-gestures)
6. [Display: night mode, screensaver, motion wake, smart dimming](#6-display-night-mode-screensaver-motion-wake-smart-dimming)
7. [Configuration reference](#7-configuration-reference)
8. [Maintenance: updates, channels, rollback, uninstall](#8-maintenance-updates-channels-rollback-uninstall)
9. [Troubleshooting](#9-troubleshooting)
10. [Tools for tinkerers](#10-tools-for-tinkerers)

Related documents: the [Home Assistant integration](home-assistant-integration.md) (manage
panels from Home Assistant), [INTEGRATION.md](INTEGRATION.md) (the `ha-kiosk-setup` contract for
tool authors), [hardware-and-services.md](hardware-and-services.md) (the panel's hardware and
message bus).

---

## 1. Requirements

| What | Notes |
|---|---|
| **Brilliant Control panel** | Tested on single-gang panels without a physical slider, Brilliant OS v26.10.21.1. The panel must be on your Wi-Fi. |
| **Home Assistant** | Reachable from the panel on your **local network**; the panel cannot use VPN or Tailscale addresses. Tested with Home Assistant 2026.10 and 2026.11. |
| **A computer to install from** | `bash` (Linux, macOS, or Git Bash on Windows) and OpenSSH (`ssh`) or PuTTY's `plink`. `plink` is used automatically when a password is set in the panel file. |
| **Python 3** (optional) | Only for `scripts/publish_layout.py`: `pip install websockets`. |
| **An MQTT broker** (recommended) | For the panel's Home Assistant device, the intercom, capture events and the Home Assistant integration. The Mosquitto add-on is enough. |

Nothing has to be installed on the panel itself: it already has Qt 5.15, QtWebSockets and
Python with the libraries the kiosk uses.

---

## 2. Installation

### 2.1 Turn on SSH on the panel

Brilliant's software has an official **Root SSH Login** setting in the panel's **Advanced
Settings**. According to Brilliant's support pages:

* Turning it on requires verifying your identity by email.
* It is permanent: *Device Settings* then shows a "May Have Had Root SSH Enabled" security
  status. Brilliant warns that it can affect software updates and that it no longer guarantees
  the software's integrity.
* Brilliant will never ask you to enable it or for your credentials. Treat any such request as a
  scam.

See Brilliant's articles [Root SSH](https://support.brilliant.tech/hc/en-us/articles/23152790775195-RootSSH)
and [Security Status on Device Settings](https://support.brilliant.tech/hc/en-us/articles/21213665069851-Security-Status-on-Device-Settings).
The option may not be offered on every panel or firmware version.

Write down the panel's **IP address** (give it a fixed lease in your router) and the **root
password** it shows. Check from your computer:

```bash
ssh root@192.168.1.50          # or: plink -ssh root@192.168.1.50
```

The first connection shows the panel's host-key fingerprint (`SHA256:...`). Note it: pinning it
in the panel file protects you against another device answering at that address.

### 2.2 Get the code and create your private config

```bash
git clone https://github.com/MikeI-Code/brilliant-ha-kiosk.git
cd brilliant-ha-kiosk
git checkout v1.4.0            # stable; or: git checkout nightly   (see section 8)
mkdir -p .private
cp config/panel.env.example .private/panel1.env
cp config/kiosk.env.example .private/kiosk.env
```

Everything specific to your home goes in **`.private/`**, which is git-ignored:

| File | Needed? | What it is for |
|---|---|---|
| `.private/kiosk.env` | Yes, one | **Settings for every panel**: Home Assistant address, schedule, features ([section 7](#7-configuration-reference)). Start from `config/kiosk.env.example`. |
| `.private/panel1.env`, `panel2.env`, ... | Yes, one per panel | **How to reach that panel**: `PANEL_HOST`, `PANEL_USER` (default `root`), `PANEL_PASSWORD`, `PANEL_HOSTKEY`. The file name is the panel's name for the scripts (`panel1`). |
| `.private/panel1.kiosk.env`, ... | Optional | **Settings for that panel only**: its wired light (`LOAD_ENTITY`), room speakers and lights, a feature turned on or off just here. Start from `config/panel.kiosk.env.example`. Without it, the panel uses `kiosk.env` as is. |
| `.private/<group>.group.env` | Optional | Settings shared by a group of panels, for example one apartment ([section 2.13](#213-multi-unit-and-rental-properties-lite-groups)). |
| `.private/token` | Yes | A Home Assistant long-lived access token, one line ([section 2.3](#23-create-a-home-assistant-token)). |
| `.private/layout.json` | Optional | A local layout, used only when `LAYOUT_DASHBOARD` is empty ([section 4](#4-editing-the-screens-in-home-assistant)). |
| `.private/photos/` | Optional | Screensaver photos (JPEG/PNG), mirrored to every panel. |

**How a panel's settings are built.** `scripts/install.sh` concatenates `kiosk.env`, then the
panel's group file (if `PANEL_GROUP` is set), then the panel's own `.kiosk.env`. The last
assignment of a key wins, so a panel file only needs the lines that differ. Turning a shared
feature off for one panel is `FEATURE=0` in its file. The combined file is validated by
`ha-kiosk-setup` on the panel: an unknown key or a value with `"`, `'`, `` ` ``, `$`, `\` or a
control character is rejected and nothing is installed.

Edit `.private/panel1.env`:

```bash
PANEL_HOST=192.168.1.50
PANEL_USER=root
PANEL_PASSWORD=<root password from the panel>
PANEL_HOSTKEY=SHA256:<fingerprint from 2.1>
```

Edit `.private/kiosk.env`. At minimum set `HA_URL` to Home Assistant's LAN address and your
time zone:

```bash
HA_URL=http://192.168.1.10:8123
TZ=EST5EDT,M3.2.0,M11.1.0          # UI clock (POSIX TZ string; the panel runs in UTC)
TZ_LOCAL=EST5EDT,M3.2.0,M11.1.0    # display schedule
```

### 2.3 Create a Home Assistant token

The panel has no on-screen keyboard, so it signs in with a token:

1. In Home Assistant, open your **profile**, then **Security**.
2. Under **Long-lived access tokens**, choose **Create token** and name it, for example "Wall
   panels".
3. Save the token as a single line in `.private/token`.

Use an **administrator** account: live layout updates ([section 4](#4-editing-the-screens-in-home-assistant))
subscribe to an event only admins can see, and clip uploads (`CAPTURE_UPLOAD=1`) need admin
rights. The token is stored on the panel in `/var/lib/ha-kiosk/config/token` and
`/etc/ha-kiosk/token` (root-only) and never leaves your network.

### 2.4 Install

```bash
scripts/install.sh panel1      # one panel
scripts/install.sh all         # every .private/<panel>.env that has PANEL_HOST
```

`install.sh` builds a release bundle from your checkout (`scripts/build-bundle.sh`), copies it,
the combined settings, the token and the layout to the panel, and runs the bundle's
`ha-kiosk-setup install` there. That tool:

1. checks the bundle's SHA-256 and its internal manifest,
2. validates the settings and token before anything on the panel changes,
3. unpacks the release into `/var/lib/ha-kiosk/releases/<version>` and activates it (the running
   copy is `/etc/ha-kiosk`),
4. waits up to 60 s for the UI to report in and connect to Home Assistant,
5. on failure goes back to the previous release (exit 5), or to Brilliant's own screen if there
   is none (exit 6), and keeps the last two releases for rollback.

After about 30 s the panel shows the new interface. The first install restarts Brilliant's
message bus once (to apply a display drop-in), which takes 60 to 90 s and may make the wired
light flicker.

The same command upgrades a panel later: it replaces the program files and re-applies your
current `.private/` settings. It never changes the files in `.private/`.

### 2.5 Per-panel settings (recommended)

Create `.private/panel1.kiosk.env` so the panel knows its own room, then run
`scripts/install.sh panel1` again:

```bash
# Kitchen panel
LOAD_ENTITY=light.kitchen_table                        # this panel's own wired light, as HA names it
ROOM_SPEAKERS=media_player.kitchen                     # three-finger volume
ROOM_LIGHTS=light.kitchen_table,light.kitchen_island   # smart-dimming estimate
# LIGHT_SENSOR=sensor.kitchen_illuminance              # if the room has a light sensor
```

### 2.6 Make the screens editable in Home Assistant

The kiosk builds its screens from a Home Assistant dashboard named by `LAYOUT_DASHBOARD`
(default `panel-layout`). Either create that dashboard by hand (*Settings*, *Dashboards*, *Add
dashboard*, *New dashboard from scratch*, URL `panel-layout`) and add cards, or publish a
starter layout:

```bash
pip install websockets
HA_URL=http://192.168.1.10:8123 HA_TOKEN=$(cat .private/token) \
  python3 scripts/publish_layout.py native/layout.example.json
```

This creates an admin-only dashboard **Panel Layout** at `/panel-layout`. It refuses to overwrite
a dashboard that already has views unless you add `--force`. Pass your own `.private/layout.json`
instead of the example to start from it. From then on, edit the screens in Home Assistant
([section 4](#4-editing-the-screens-in-home-assistant)).

### 2.7 Connect the panels to Home Assistant over MQTT

With a broker configured, each panel appears in Home Assistant as a device through MQTT
discovery. MQTT is also how panels find each other for the intercom, how capture clips are
announced, and how the Home Assistant integration discovers panels. Set it up once.

**1. Set up a broker.** On Home Assistant OS or Supervised:

1. **Settings, Add-ons, Add-on Store, Mosquitto broker**: install and start it (turn on *Start on
   boot*).
2. **Settings, Devices & services**: Home Assistant discovers **MQTT**; click **Configure**,
   **Submit**.
3. Create a login for the panels: **Settings, People, Users, Add user** (for example
   `brilliant-panels`, not an administrator). The Mosquitto add-on accepts Home Assistant users.

Any other broker works as long as Home Assistant's MQTT integration is connected to it.

**2. Point the panels at it** in `.private/kiosk.env`, then run `scripts/install.sh all`:

```bash
MQTT_HOST=192.168.1.10          # the broker (for the add-on: Home Assistant's address)
MQTT_USER=brilliant-panels
MQTT_PASSWORD=<that user's password>
```

**Who publishes the panel.** `KIOSK_MQTT` chooses the MQTT provider. It is never "off" in normal
use:

| `KIOSK_MQTT` | Meaning |
|---|---|
| `1` (default, also when the key is absent) | **Built-in kiosk MQTT.** The kiosk publishes the whole panel: the device below with every entity. |
| `kiosk` | **brilliant-mqtt publishes the panel.** You run the community project [brilliant-mqtt](https://github.com/joyfulhouse/brilliant-mqtt) on the panel for the wired load, power, temperatures, motion and diagnostics; the kiosk publishes only what it owns (display, switches, gestures, voice, intercom, capture, security, privacy) and joins brilliant-mqtt's Home Assistant device through `MQTT_DEVICE_ID`. Set both keys. |
| `0` | Legacy: no kiosk MQTT at all. Not recommended: the intercom, capture events and discovery stop too, and the Home Assistant integration never sets it. |

Do not run brilliant-mqtt and the kiosk's built-in mode (`1`) on the same panel: you would get
two entities for the same load. brilliant-mqtt's screen and brightness controls drive Brilliant's
own screen settings, which the kiosk sidelines ([section 6](#6-display-night-mode-screensaver-motion-wake-smart-dimming)),
so they have no effect with the kiosk installed.

**What you get** in built-in mode: one device per panel, named **"\<Room\> Brilliant panel"**
(Brilliant's own room name), node id `brilliant_<first 12 characters of the Brilliant device id>`:

| Entity | What it does |
|---|---|
| **Wired light** | The panel's own load: on/off and brightness, controlled locally over Brilliant's bus |
| Load power, Gangbox temperature | Power drawn by the wired load (watts) and the dimmer board's temperature, one pair per load (`Load 2 power`, ... on multi-gang panels). Brilliant only reports changes larger than about 40 W / 10 %, so treat it as approximate. |
| Faceplate / CPU temperature, Brilliant firmware, Last boot | Diagnostics, polled every `DIAG_SECS` |
| HomeKit paired, HomeKit enabled | Whether Brilliant's built-in HomeKit accessory is enabled and paired (see below) |
| Shelly BLE sensors | Number of Shelly BLU sensors Brilliant's own Bluetooth scan tracks; per-sensor motion, contact, battery, RSSI and last-heard time in the attributes |
| Load minimum / maximum dim level, Dimming edge, Dim calibration | Diagnostics, disabled by default: the load's dimming calibration. Read-only; change them in the Brilliant app. |
| **Motion** | On when Brilliant's motion logic wakes its screen; clears after `MOTION_CLEAR_SECS` |
| Display | `awake`, `photos`, `dim` or `asleep` |
| Backlight, Room light level | Current backlight (0-15) and the smart-dimming room light estimate (%) |
| **Screensaver, Night mode, Motion wake, Smart dimming** | Switches that change the panel's behavior at runtime |
| Day brightness | Awake backlight level (1-15) |
| **Wake screen** | Button: wakes the display as if touched |
| **Gesture** | Event entity: every multi-finger gesture (`2_tap`, `3_tap`, `2_swipe_left`, ...) for automations |
| Privacy | Diagnostic: `open`, `mic_locked`, `camera_locked` or `mic_and_camera_locked` (`ALLOW_MIC` / `ALLOW_CAMERA`), with the panel's group as an attribute |
| Ring alarm, lock sensors, Ring doorbell event, Security controls | Read-only Ring and Schlage status ([section 2.12](#212-security-ring-alarm-locks-doorbells-and-cameras)) |
| Talk, Announce, Speaker volume, Voice state | With `KIOSK_VOICE=1` ([section 2.9](#29-voice-assistant-and-announcements-optional-off-by-default)) |
| Intercom state / target / talk / test tone | With `KIOSK_INTERCOM=1` ([section 2.10](#210-intercom-between-panels-optional-off-by-default)) |
| Capture event, Capture state | With `KIOSK_CAPTURE=1` ([section 2.11](#211-voice-and-video-capture-api-optional-off-by-default)) |

The switches and Day brightness are stored on the panel in `/var/lib/ha-kiosk/overrides.env`,
which takes precedence over `kiosk.env` and survives reboots and reinstalls.

The kiosk also publishes two retained topics for other software: `<MQTT_BASE>/<node>/kiosk_state`
(`active`; `ha-kiosk-setup` leaves `disabled`, `not_installed` or `failed` there when the kiosk
stops) and `<MQTT_BASE>/<node>/discovery` (JSON with `state`, `host`, `device_id`, `room`), which
the Home Assistant integration uses to offer the panel with its address filled in.

**Example: a three-finger tap on the kitchen panel runs the goodnight scene**

```yaml
automation:
  - alias: Kitchen panel three-finger tap
    triggers:
      - trigger: state                 # fires on every gesture (the event's timestamp changes)
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

Entity ids follow the device name; check yours on the device page.

**HomeKit.** Brilliant's firmware still runs its own HomeKit accessory with the kiosk installed.
It exposes only the wired load, so it adds nothing over the MQTT light; use it only for Siri or
Apple Home. Pair it through the Brilliant app; the panel accepts pairing for about 10 minutes
after its HomeKit service starts. During that window Brilliant writes the setup code to the
panel's journal in plain text, so treat panel logs as sensitive. The kiosk never pairs or changes
HomeKit; it only shows its state.

### 2.8 Manage panels from Home Assistant (integration)

The `brilliant_kiosk` integration is the admin layer: it connects to each panel over SSH and
lets Home Assistant install, update and roll back the kiosk, show its health and install state,
change settings, read redacted logs and Brilliant message-bus variables, and raise repair issues
when the panel's settings drift. It does not replace the MQTT device above; it links to it.

* Home Assistant 2026.11 or newer: **Settings, Marketplace, Add from link**, enter
  `MikeI-Code/ha-brilliant-kiosk`, install, restart.
* Older Home Assistant (2026.3.1 to 2026.10): add `https://github.com/MikeI-Code/ha-brilliant-kiosk`
  as a HACS custom repository of type *Integration*.

Then **Settings, Devices & services, Add integration, Brilliant Kiosk**. A panel running a
current kiosk nightly is offered with its address filled in; you enter the root password and
confirm the SSH host key. The integration can also do a first install on a panel that has SSH
but no kiosk, so with it you do not need this repository on a computer at all.

Everything about it is in [home-assistant-integration.md](home-assistant-integration.md).

### 2.9 Voice assistant and announcements (optional, off by default)

With `KIOSK_VOICE=1` the `ha-kiosk-voice` service turns the panel's microphones and speaker into
a **Home Assistant voice satellite** (Wyoming protocol, for Assist) and an **announcement
speaker**. It uses Brilliant's Python only and about 16 MB of memory. Leave it off on bedroom
panels.

**Turn it on** for one panel in `.private/panel1.kiosk.env`, then run `scripts/install.sh panel1`:

```sh
KIOSK_VOICE=1
```

The installer enables `ha-kiosk-voice.service` only when `KIOSK_VOICE=1` and the microphone is
not locked (`ALLOW_MIC`), and disables it otherwise.

**How it listens.** The satellite listens on TCP `VOICE_PORT` (default 10700), bound to the
panel's LAN address (the one it uses to reach `HA_URL`; `VOICE_BIND` overrides it). Only
`VOICE_ALLOW` (default: the host in `HA_URL`) may connect. Brilliant's firewall drops unknown
ports, so while the service runs it adds one runtime nftables rule for `VOICE_PORT` from those
hosts; the rule is removed when the service stops and never saved.

**Add it to Home Assistant:** **Settings, Devices & services, Add integration, Wyoming
Protocol**, enter the panel's IP address and port `10700`. You get an **Assist satellite** device
with mute, pipeline and noise-suppression controls. Pick a pipeline with working speech-to-text
and text-to-speech (*Settings, Voice assistants*). Home Assistant Cloud needs a subscription;
otherwise use local add-ons such as Whisper or Speech-to-Phrase plus Piper.

**Wake word.** The panel is too slow for an on-device wake-word engine, so:

* `VOICE_WAKE=off` (default): push-to-talk. Press the **Talk** button entity or call it from an
  automation (for example on a `3_tap` gesture). The panel listens for up to `VOICE_LISTEN_SECS`
  seconds; Home Assistant decides when you stopped speaking.
* `VOICE_WAKE=ha`: the microphone streams to Home Assistant all the time and its pipeline detects
  the wake word. This needs a wake-word engine in Home Assistant (for example the openWakeWord
  add-on) and sends room audio over the LAN continuously.

**Microphone.** The far-field mics are shared with Brilliant through ALSA, so the satellite runs
alongside Brilliant's own listener. `VOICE_MIC` takes any ALSA capture device that delivers
16 kHz mono 16-bit:

| `VOICE_MIC` | What you get |
|---|---|
| `default` (recommended) | DC removal and 30x gain, Brilliant's own capture chain. The right level for speech-to-text. |
| `dsnoop_filtered` | The same plus a 300 Hz high-pass and 3 kHz low-pass. Less hum and hiss, narrower; try it in a noisy room. |
| `plug_dsnoop_48000` | The raw mics with no gain, about 30 dB too quiet for most speech-to-text. |

**Speaker.** Replies and announcements play through `VOICE_SPEAKER` (default `dmix_filtered`,
which follows Brilliant's volume and has a 600 Hz high-pass filter, so it sounds thin; `default`
bypasses the volume and plays at full level). There is no echo cancellation: the panel stops
sending the microphone while it speaks. Nothing is recorded to disk.

**MQTT entities** (with [section 2.7](#27-connect-the-panels-to-home-assistant-over-mqtt)), on the
panel's device:

| Entity | What it does |
|---|---|
| **Talk** | Button: start listening (push-to-talk) |
| **Announce** | Text: a sentence to speak with `VOICE_TTS_ENGINE`, or an `http(s)://...` or `/api/...` URL of an MP3/WAV to play. From automations: `text.set_value`. |
| Speaker volume | Brilliant's speaker volume (0-100). It affects Brilliant's own sounds too. |
| Voice state | `idle`, `listening`, `processing` or `speaking` |

Home Assistant's own `assist_satellite.announce` action works too and uses the pipeline's TTS.

### 2.10 Intercom between panels (optional, off by default)

Panels can talk to each other, push-to-talk, audio only. It needs MQTT ([section 2.7](#27-connect-the-panels-to-home-assistant-over-mqtt)):
that is how panels find each other. Turn it on per panel in `.private/<panel>.kiosk.env`, then
run `scripts/install.sh`:

```sh
KIOSK_INTERCOM=1     # this panel can hear the others (speaker only)
INTERCOM_MIC=1       # ...and can talk too (mic). Leave it off on bedroom panels.
```

A panel with `KIOSK_INTERCOM=1` alone is receive-only: its microphone is never opened.

**Talking.** On a panel with `INTERCOM_MIC=1`, a round microphone button appears above the tabs
(bottom right) as soon as another intercom panel is online. Hold it to talk, let go to stop. The
label under it is the target; tap it to step through the rooms and **All panels**. A talk stops
by itself after `INTERCOM_MAX_SECS`. The button's ring turns blue while another panel talks to
this one.

**How it works.** Audio is 16 kHz mono PCM in RTP packets over UDP `INTERCOM_PORT` (about
256 kbit/s while someone talks, nothing otherwise). Brilliant's firewall already accepts UDP 5000
and up, so no rule is added. A panel only plays audio from panels it found through MQTT
(`<MQTT_BASE>/intercom/peers/...`) and in the same `PANEL_GROUP`. Playback goes through
`INTERCOM_SPEAKER` at Brilliant's speaker volume. Nothing is recorded.

**MQTT entities** on the panel's device:

| Entity | Panels | What it does |
|---|---|---|
| Intercom state | all intercom panels | `idle`, `talking to <room>` or `listening to <room>` |
| **Intercom target** | mic panels | Select: a room or **All panels** |
| **Intercom talk** | mic panels | Switch: on = stream the mic to the target (stops after `INTERCOM_MAX_SECS`) |
| Intercom test tone | mic panels | Button: send a 1 s tone to the target (no mic), to check a receiving panel |

The service also answers on the panel at `http://127.0.0.1:<INTERCOM_PORT+1>/` (`status`,
`talk?on=1`, `talk?on=0`, `target?name=<room>`, `tone`); the panel UI uses this.

### 2.11 Voice and video capture API (optional, off by default)

An opt-in way to record a voice clip, a camera snapshot or a short video on the panel and hand it
to Home Assistant. Nothing listens all the time: the microphone or camera is opened for one
explicit recording and closed right after. The panel only publishes each clip; what happens next
(speech-to-text, a notification, an AI reply) is up to automations you write. The project ships
no Home Assistant logic for it.

**Turn it on** per panel in `.private/<panel>.kiosk.env`, then run `scripts/install.sh`:

```sh
KIOSK_CAPTURE=1      # Capture screen + API, voice clips
CAPTURE_VIDEO=1      # ...plus camera snapshots and video clips (optional)
```

The installer enables `ha-kiosk-capture.service` only when `KIOSK_CAPTURE=1` and at least one of
the microphone (`ALLOW_MIC`) or, with `CAPTURE_VIDEO=1`, the camera (`ALLOW_CAMERA`) is not
locked.

**The screen.** A **Capture** tab appears. Tap the round button to start a voice recording and
again to stop (it stops by itself at `CAPTURE_MAX_SECS`). With `CAPTURE_VIDEO=1` there are also
**Snapshot** and **Video clip** buttons. A red *Recording* pill shows whenever the mic or camera
is in use, including recordings Home Assistant started (labelled *Requested by Home Assistant*;
the panel switches to the Capture tab so the person in the room sees it). The last clip and any
reply text from Home Assistant are shown below.

**Clips.** Voice is 16 kHz mono 16-bit WAV. Snapshots are 1280x720 JPEG. Video is 640x360 at
5 fps, H.264 in MP4, no sound, 10 s at most. Clips live in RAM (`/run/ha-kiosk-capture`, 16 MB in
total, oldest dropped first) and are deleted after `CAPTURE_TTL` seconds or when the service
stops.

**Clip URLs.** `http://<panel-ip>:<CAPTURE_PORT>/clips/<token>.<wav|jpg|mp4>`. The token is
random and only appears in the MQTT event. The server listens on the panel's LAN address
(`CAPTURE_BIND` overrides it), answers `GET` only, and only for the hosts in `CAPTURE_ALLOW`
(others get 403; unknown or expired clips 404). While the service runs, one runtime nftables rule
accepts `CAPTURE_PORT` from those hosts.

**Upload to Home Assistant.** With `CAPTURE_UPLOAD=1`, each clip is also uploaded into Home
Assistant's media library (*Media*, *My media*, optionally into the existing folder
`CAPTURE_UPLOAD_DIR`) and the event carries its `media_id`, so an automation can use the clip
directly (for example as an AI task attachment). The panel's token must belong to an
administrator. Uploaded clips are never shown by the photo screensaver.

**MQTT** (needs [section 2.7](#27-connect-the-panels-to-home-assistant-over-mqtt)). `<base>` is
`<MQTT_BASE>/<node>`, for example `brilliant_kiosk/brilliant_0123456789ab`. Two entities appear on
the panel's device:

| Entity | Topic | What it is |
|---|---|---|
| **Capture** (event) | `<base>/capture/event` | Fires `audio_clip`, `snapshot`, `video_clip` or `capture_error` |
| Capture state (sensor) | `<base>/capture/state` | `idle`, `recording`, `processing` or `error` |

Event payload (QoS 1, not retained):

```json
{"event_type": "audio_clip", "clip_id": "6f1c...", "url": "http://192.168.1.50:10720/clips/<token>.wav",
 "mime": "audio/wav", "duration": 4.2, "bytes": 134444, "expires": 1767225600,
 "panel": "brilliant_0123456789ab", "room": "Kitchen", "source": "panel", "request_id": null, "error": null}
```

`mime` is `audio/wav`, `image/jpeg` or `video/mp4`; `duration` is seconds (0 for a snapshot);
`expires` is a Unix time; `source` is `panel` (someone tapped) or `ha` (a command); `media_id` is
added with `CAPTURE_UPLOAD=1`. A `capture_error` has `url`, `mime` and `expires` set to `null`
and the reason in `error` (for example `busy`, `rate limited` or
`video capture is off (CAPTURE_VIDEO=0)`).

**Commands**, JSON on `<base>/capture/command`:

```json
{"action": "record", "kind": "audio", "secs": 10, "request_id": "my-id-1"}
{"action": "stop"}
{"action": "cancel"}
```

`kind` is `audio`, `snapshot` or `video` (the last two only with `CAPTURE_VIDEO=1`). `secs` is
capped at `CAPTURE_MAX_SECS` (10 for video). Recordings started this way are limited to one every
10 s and always show on the screen. `request_id` comes back in the event. `stop` ends a recording
and publishes it; `cancel` discards it.

**Reply text**, JSON on `<base>/capture/reply`: `{"text": "Added milk to the list", "clear_after": 30}`
shows the text on the Capture screen for `clear_after` seconds. The panel does not speak it; use
the **Announce** entity ([section 2.9](#29-voice-assistant-and-announcements-optional-off-by-default))
for that.

**Example for automation authors** (adapt it): send each voice clip from the kitchen panel as a
notification.

```yaml
alias: Kitchen panel voice clip (example)
triggers:
  - trigger: state
    entity_id: event.kitchen_brilliant_panel_capture
conditions:
  - "{{ trigger.to_state.attributes.event_type == 'audio_clip' }}"
actions:
  - action: notify.notify
    data:
      message: "New {{ trigger.to_state.attributes.duration }} s clip: {{ trigger.to_state.attributes.url }}"
```

The service also answers on the panel at `http://127.0.0.1:<CAPTURE_PORT+1>/` (`status`,
`start?kind=audio|video`, `stop`, `snapshot`, `cancel`); the panel UI uses this.

### 2.12 Security: Ring Alarm, locks, doorbells and cameras

If you link **Ring** or **Schlage** in the **Brilliant app**, the panel uses Brilliant's own
integrations for them. Nothing needs setting up here: link or unlink devices in the Brilliant app
and every panel follows within seconds. (Home Assistant's own Ring integration has no Ring Alarm
support at the time of writing.)

**On the panel**

* A **Security** tab appears by itself when Brilliant reports any of these devices
  (`SECURITY_TAB=0` or `SECURITY_ACCESS=off` hides it). It has three sections:
  * **Alarm:** status, **Arm home** / **Arm away**, and **Disarm** with your alarm code.
  * **Locks:** one card per lock. Tap to lock; unlocking needs a second tap within 3 s.
  * **Doorbells & cameras:** each Ring device as a camera tile (Home Assistant's still picture,
    matched by name, for example "Front Door" to `camera.front_door_live_view`) with the time of
    the last press. Tap for full screen.
* The Home header shows a one-line summary (alarm and locks); tap it to open the Security tab.
* **Doorbell pop-up:** a press wakes the panel (also at night) and shows that door's camera for
  `DOORBELL_SECS` (45 s). `DOORBELL=0` turns pop-ups off; `DOORBELL_MOTION=1` also pops up on
  camera motion events; `DOORBELL_CAMERAS` pairs doorbell event entities with cameras when the
  names do not match (`event.front_door_ding=camera.front_door_live_view,...`). Camera tiles from
  any Home Assistant camera work the same way ([section 3](#3-using-the-panel)).

**In Home Assistant** (read-only, over MQTT): a **Ring alarm** sensor (`disarmed`, `armed_home`,
`armed_away`, ...) and a lock sensor for each lock, from the panel that hosts each integration in
Brilliant; a **Ring doorbell** event; and on every panel a diagnostic **Security controls**
sensor (below). Home Assistant cannot arm, disarm, lock or unlock through this project.

#### How the alarm and lock control works

The Security tab talks to Brilliant's integrations over the panel's local message bus, the same
interface Brilliant's own screen used. It never contacts Ring or Schlage directly and never reads
your Ring or Schlage login.

* **Status, arming and locking** use variables Brilliant's software exposes as settable, with the
  bus's normal check-and-set request.
* **Disarming needs your alarm code.** Brilliant only accepts a disarm with the code encrypted
  for the alarm's one-time key (X25519, HKDF-SHA256, AES-GCM). That format is undocumented and was
  matched by testing standard settings against Brilliant's own decryption on a throwaway key. The
  alarm still checks the code itself.
* **Your code** is typed on the panel and sent once, encrypted. It is never stored, logged or
  sent to Home Assistant.
* **These are internal interfaces**, so a Brilliant firmware update may change them (disarming is
  the most likely to break). The panel protects you from that as described next.

#### When something changes: safety behaviour

* **Compatibility check.** At start, every 10 minutes and after every firmware change, each panel
  checks each ability (arm, disarm, lock) without sending anything to the devices. If a check
  fails, only that ability is switched off.
* **Visible, never silent.** A switched-off control is greyed out on the panel with the reason,
  for example *"Disarm from the panel is unavailable: the alarm's key format changed. Use the Ring
  app or keypad."*
* **Disarm is confirmed.** After a disarm the panel waits up to 20 s for the alarm to report
  disarmed; if it does not, the panel says so. After a firmware update the panel notes that
  disarm has not been confirmed on that firmware yet, until one works.
* **Attempts are limited.** After 3 disarms in a row that were not accepted, panel disarm pauses
  for 5 minutes.
* **Home Assistant is told.** The **Security controls** sensor is `ok` or `limited`, with
  attributes `arm`, `disarm`, `lock` (true/false), `reasons`, `firmware`,
  `disarm_verified_on_this_firmware`, `last_disarm` and `paused_until`:

```yaml
alias: Panel security controls limited
triggers:
  - trigger: state
    entity_id: sensor.kitchen_brilliant_panel_security_controls
    to: limited
actions:
  - action: notify.notify
    data:
      message: >
        {{ trigger.to_state.attributes.reasons }} (Brilliant firmware
        {{ trigger.to_state.attributes.firmware }})
```

### 2.13 Multi-unit and rental properties (lite groups)

For a duplex, a building with a few units, or a rental with an owner's suite, you can put panels
into **groups**: a label (`PANEL_GROUP`) plus a shared settings file. "Lite" because it works
through settings files and per-panel filtering.

**Three settings layers**, later wins:

1. `.private/kiosk.env`: the whole property (Home Assistant address, broker, defaults).
2. `.private/<group>.group.env`: everything shared by one unit, used when the panel's own file
   sets `PANEL_GROUP=<group>` (lowercase letters, digits and `-`).
3. `.private/<panel>.kiosk.env`: this panel only.

```
.private/
  kiosk.env              # property-wide: HA_URL, MQTT_HOST, ...
  unit-2b.group.env      # unit 2B: SECURITY_DEVICES=Unit 2B Door, ALLOW_CAMERA=0
  panel3.env             # PANEL_HOST=192.168.1.53 (how to reach the panel)
  panel3.kiosk.env       # PANEL_GROUP=unit-2b, ROOM_LIGHTS=...
```

`scripts/install.sh all` skips `*.group.env` and `*.kiosk.env`; only `.private/<panel>.env` files
with `PANEL_HOST` are panels.

**Intercom stays inside the group.** Each panel advertises its group, and the intercom lists and
accepts only panels with the same group. Panels without a group form one group of their own.

**Security access.** `SECURITY_ACCESS=control` (default) allows arm/disarm and lock/unlock; `view`
shows status only; `off` hides the Security tab, alarm, locks and doorbell pop-ups on this panel.
`SECURITY_DEVICES` limits the panel to the named devices (comma-separated, case-insensitive;
empty = all). The word `alarm` in the list includes the Ring Alarm. A doorbell that is not in the
list does not wake the panel. The owner's view in Home Assistant (the MQTT sensors) is not
filtered.

**Mic and camera locks, deny wins.** `ALLOW_MIC=0` turns off the voice satellite, the intercom
microphone (the panel can still receive) and audio capture. `ALLOW_CAMERA=0` turns off snapshots
and video capture. `0`, `off`, `false` and `no` all count. A deny in **any** layer wins: if the
property file says `ALLOW_MIC=0`, a group or panel file cannot turn it back on. `install.sh`
prints `privacy: mic blocked / camera blocked`, and the MQTT **Privacy** sensor shows the state
of every panel in Home Assistant.

Residents never get the panel's root password; settings are changed by whoever runs
`scripts/install.sh` or the Home Assistant integration.

#### What this does and doesn't isolate

| Layer | Isolation | What to do |
|---|---|---|
| Brilliant | Strong when each unit is its own Brilliant home | Set up a separate Brilliant home per unit in the Brilliant app |
| Home Assistant | None per entity: a panel's token can reach everything in one shared Home Assistant | Use one Home Assistant per unit, or no Home Assistant on resident panels. Groups only limit what the panel shows |
| MQTT | Shared credentials unless you set per-group credentials and broker ACLs | See [INTEGRATION.md section 9.5](INTEGRATION.md#95-network-listeners-and-broker-acl) |
| Network | None from this project | Put each unit on its own VLAN |
| Brilliant's own services | Not covered | The locks govern this project's features only; Brilliant's own voice assistant is controlled in the Brilliant app |

---

## 3. Using the panel

<p align="center">
  <img src="images/home.png" width="23%" alt="Home">
  <img src="images/lights.png" width="23%" alt="Lights">
  <img src="images/sonos.png" width="23%" alt="Music">
  <img src="images/house.png" width="23%" alt="House">
</p>

**Navigating:** tap a tab in the bottom bar or swipe left/right anywhere. Drag up and down to
scroll a page.

**Home tab.** The clock and current weather sit on an animated sky that follows the weather and
the sun (`WEATHER_FX`, see [section 7](#7-configuration-reference)). Tap a forecast day to expand
its day and night detail; tap the sky for a full-screen weather page with wind, humidity,
pressure, visibility, sunrise and sunset, moon phase, a 24-hour chart and a 7-day forecast. A
short weather note ("Rain until about 3 PM") is worked out on the panel from the hourly forecast.

**Tiles**

| Tile | Tap | Other |
|---|---|---|
| Light | Toggle | Drag the bar to set brightness (drag to 0 to turn off) |
| Switch | Toggle | |
| Scene / script / button | Run it | Flashes to confirm |
| All off | Tap twice within 3 s | Turns off every light |
| Thermostat | | -/+ change the setpoint; several taps are combined into one change. With `CLIMATE_CONTROLLER`, a **Mode** chip and the setpoint go to the controller and a **Fan** chip to the thermostat |
| Fan / humidifier | Toggle | Speed or humidity bar |
| Cover / valve | | Open, stop, close, position; garage doors and gates need two taps |
| Lock | Lock | Unlocking needs two taps |
| Number / select | | Bar or -/+; option chips |
| Media | Play/pause | Artwork, title, volume; "now playing" tiles only appear while playing |
| Sensor / binary sensor | | Live value |
| Camera | Full screen | See below |

A light tile marked **"this switch"** is the panel's own wired load, controlled locally
([section 5](#5-this-switch-local-control-and-gestures)).

**Cameras.** A camera tile shows Home Assistant's still picture, refreshed while it is on screen.
Tap it for full screen. Pinch or double-tap to zoom (up to 4x), drag to pan, double-tap again to
reset.

**Live video** plays in the full-screen view when the camera has an **H.264** stream, decoded by
the panel's video chip and marked with a red **LIVE** badge. The panel cannot decode H.265, so:

* **CuboAI** cameras: turn on the integration's **H.264 transcode** for the camera (720p is
  enough). The panel finds the stream by itself.
* Any other camera with an H.264 RTSP stream: list it in `CAMERA_LIVE`
  (`camera.x=rtsp://host:port/path,...`).
* `CAMERA_LIVE_ON=0` shows stills only. Cameras without an H.264 stream show refreshed stills,
  which zoom the same way.

**Music tab** (any Home Assistant media player: Sonos, WiiM/LinkPlay, Music Assistant,
Bluesound, HEOS, Google Cast, ...): pick a room at the top; see album art, title and progress;
shuffle, previous, play/pause, next and repeat; volume and mute; **"Play in these rooms too"**
groups speakers (only for integrations that support grouping). **Favorites** shows what you
starred in the player's app as tap-to-play tiles; players without a favorites folder show the top
of their media library instead. A **Source** row appears for players with inputs. Buttons the
player does not support are hidden.

**Connection:** if the panel loses Home Assistant it shows a red *Connecting to Home Assistant*
banner and reconnects on its own. The wired light still works locally.

---

## 4. Editing the screens in Home Assistant

The panels build their screens from the dashboard named by `LAYOUT_DASHBOARD` (default
`panel-layout`, [section 2.6](#26-make-the-screens-editable-in-home-assistant)). Open it in Home
Assistant, click the pencil, and edit it like any dashboard. When you press **Save**, every panel
rebuilds within about a second. The last layout is cached on each panel, so it boots with your
screens even if Home Assistant is down.

| In the Home Assistant dashboard | On the panel |
|---|---|
| A view (tab at the top of the dashboard) | A tab in the bottom bar, with the view's title and icon |
| A view whose **URL** is `music` (or `sonos`) | The Music tab. Every media player on it becomes a speaker in the room picker. |
| **Heading** card | Starts a titled section |
| **Tile** card | A tile. Its kind follows the entity: light; switch, input_boolean, automation; fan, humidifier; cover, valve; lock; number, input_number; select, input_select; scene, script, button; climate; media player; sensor; binary sensor; camera |
| Tile **width** (Layout: columns 12 / 6 / 4 / 3) | Full / half / third / quarter width |
| **Weather forecast** card | The clock and weather header with the forecast. A temperature tile next to it becomes "Inside". |
| **Media control** card | An always-visible player. A media *tile* only shows while something plays. |
| **Button** with action `light.turn_off` on all lights | The All-off button (tap twice) |
| **Button** with any other action | An action button that runs it |
| **Picture entity** / camera cards | A camera tile |
| Entities / Grid / Stack cards | Flattened into tiles |

Names and icons come from the card; without a name, the entity's friendly name is used. Every
Material Design icon (`mdi:...`) from Home Assistant's icon picker works.

**Other hubs.** Anything Home Assistant can control can go on the panel. For example, devices
behind a Universal Devices eisy/Polisy/ISY hub appear as normal entities once Home Assistant's
*Universal Devices ISY/IoX* integration is added, and their scenes work well as tiles.

**Prefer files?** Leave `LAYOUT_DASHBOARD` empty and edit `.private/layout.json` instead (format:
`native/layout.example.json`), then run `scripts/install.sh` again.

### Match the look in Home Assistant's web interface

`ha/themes/brilliant-kiosk.yaml` is a Home Assistant theme with the panel's colours.

1. Copy it to Home Assistant's `themes/` folder (for example with the File editor add-on).
2. Make sure `configuration.yaml` loads themes:
   ```yaml
   frontend:
     themes: !include_dir_merge_named themes
   ```
3. Run **Developer tools, Actions, `frontend.reload_themes`**.
4. Pick **Brilliant kiosk** in your profile, or add `theme: Brilliant kiosk` to the views of the
   panel dashboard.

For tiles that behave like the panel's in a browser, give them tile **features**
(`light-brightness`, `target-temperature`, `climate-fan-modes`). The panel ignores features and
view themes; this only changes the browser and phone app.

---

## 5. This switch: local control and gestures

<img src="images/dim.png" width="30%" align="right" alt="Two-finger dimming readout">

These panels have no physical slider: dimming happens on the touchscreen. The **`ha-kiosk-bridge`**
service connects to Brilliant's message bus and lets the screen switch and dim the panel's own
wired load directly, in about 0.1 s, with no Home Assistant in the path. It listens on
`127.0.0.1:8765` only.

Set `LOAD_ENTITY` to the Home Assistant entity of that load ([section 2.5](#25-per-panel-settings-recommended)).
Tiles for it then use local control and show **"this switch"**.

**Gestures** work anywhere on the screen (it tracks up to 10 fingers):

| Gesture | Default action |
|---|---|
| **Two fingers up / down** | Dim this switch's light, with a live percentage on screen |
| **Two-finger tap** | Toggle this switch's light (`GESTURE_2_TAP=load.toggle`) |
| **Three fingers up / down** | Volume of the room's speakers (`ROOM_SPEAKERS`). Each speaker moves by the same amount. |
| Three-finger tap, two/three-finger swipes left or right | Unassigned; set your own |

While two or more fingers are down, taps, bars and scrolling ignore the touch, so a gesture never
presses the tile under your fingers.

**Custom gestures:** add lines like these to `kiosk.env` or a panel's `.kiosk.env`:

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
| `<domain>.<service>` | Any Home Assistant action with no target, for example `script.turn_on` |
| `<domain>.<service>:<entity_id>` | Any Home Assistant action on an entity, for example `light.turn_off:all` |

Setting `GESTURE_2_SWIPE_UP/DOWN` or `GESTURE_3_SWIPE_UP/DOWN` replaces the built-in dimming or
volume gesture. Every gesture is also published as an MQTT event for automations
([section 2.7](#27-connect-the-panels-to-home-assistant-over-mqtt)).

<br clear="right">

---

## 6. Display: night mode, screensaver, motion wake, smart dimming

<img src="images/saver.jpg" width="30%" align="right" alt="Screensaver with clock and local weather">

The **`ha-kiosk-display`** service owns the screen:

* **Awake:** `DAY_LEVEL` (or the smart-dimming level) while you use it.
* **Screensaver:** after `IDLE_DIM_SECS` (5 min) without a touch during the day, a photo
  slideshow at `PHOTO_LEVEL` with a clock and the current weather (`SCREENSAVER_WEATHER=0` hides
  it). `SCREENSAVER=0` dims to `DIM_LEVEL` instead.
* **Night:** from `SLEEP_START` to `SLEEP_END` (21:00 to 06:30) the screen is off. A touch wakes
  it for `NIGHT_WAKE_SECS` (3 min). `NIGHT_MODE=0` (or the Night mode switch) turns the schedule
  off.
* **Touch to wake:** the waking touch is swallowed, so it never presses anything. After the
  screensaver or night, the panel returns to the Home tab.
* **Motion wake:** walking up to the panel wakes it (`MOTION_WAKE=day`, `always` or `off`),
  using the faceplate's motion sensor through Brilliant's own logic.
* **Smart dimming** (`SMART_DIM=1`): every `AMBIENT_SECS` the brightness follows the room's
  light level between `MIN_LEVEL` and `DAY_LEVEL`. With `LIGHT_SENSOR` set to a Home Assistant
  illuminance sensor, its lux reading is used (log scale from `LUX_DARK` to `LUX_BRIGHT`).
  Otherwise the level is estimated from the sun's elevation (`sun.sun`, between `SUN_DARK_ELEV`
  and `SUN_BRIGHT_ELEV`) and whether `ROOM_LIGHTS` are on, whichever is brighter. The panel's own
  light sensor reads 0 on this hardware and the camera would light its privacy LED, so neither is
  used.
* **Power:** the CPU governor follows the state (`CPU_AWAKE_GOV`, `CPU_IDLE_GOV`, `CPU_SLEEP_GOV`)
  and a `SWAP_MB` swap file guards against running out of memory.

**Choosing the photos.** Your own photos win; as soon as there are any, only they are shown:

1. **From your computer:** put JPEG/PNG files in `.private/photos/` and run `scripts/install.sh`.
   Only new or changed files are copied; files you delete are removed from the panel.
2. **From Home Assistant** (easiest from a phone): open **Media, My media** and upload photos.
   Every panel mirrors them every `PHOTO_SYNC_MINS` (30) minutes, subfolders included. To use one
   folder only, set `HA_PHOTO_FOLDER` to its name.

With no photos of your own, the panels show the photos added through the Brilliant app, then
Brilliant's stock art. `STOCK_ART=mix` always adds the stock art (featured, nature, still art,
animals) to your photos; `STOCK_ART=off` never shows it. Stock images are read in place from the
firmware and never copied. `PHOTO_DIRS` (advanced) names exact panel folders to use instead of
all of the above.

Brilliant's own services still run their screen-off logic. A systemd drop-in
(`systemd/message_bus-ha-kiosk.conf`) gives them stand-in files, so only `ha-kiosk-display` drives
the real backlight.

<br clear="right">

---

## 7. Configuration reference

Every setting below goes in `.private/kiosk.env` (all panels). To change one for a single panel,
put just that line in `.private/<panel>.kiosk.env`; for a group, in `.private/<group>.group.env`
([section 2.2](#22-get-the-code-and-create-your-private-config)). Then run `scripts/install.sh all`
(or `scripts/install.sh <panel>`), or use the Home Assistant integration's `set_env` action.

Rules: only the keys below are accepted (a typo is an error, not a no-op); values may not contain
`"`, `'`, `` ` ``, `$`, `\` or control characters; ports must be 1 to 65535. On the panel the
file is `/etc/ha-kiosk/kiosk.env`, with the saved copy `ha-kiosk-setup` rebuilds it from in
`/var/lib/ha-kiosk/config/kiosk.env`.

### Home Assistant and layout

| Key | Default | Values | What it does |
|---|---|---|---|
| `HA_URL` | (required) | `http(s)://host:port[/path]` | Home Assistant on the LAN, for example `http://192.168.1.10:8123`. Native mode uses the scheme, host and port; web mode loads the full URL. |
| `LAYOUT_DASHBOARD` | `panel-layout` | dashboard URL, or empty | Home Assistant dashboard to build the screens from. Empty = use `layout.json`. |
| `KIOSK_MODE` | `native` | `native`, `web` | `native` is this UI. `web` shows Home Assistant's own dashboard in a browser; it works but is slow on this CPU. |
| `HA_ZOOM` | `2.0` | number | Web mode only: page zoom. 2.0 makes the 720x1280 screen behave like a 360x640 phone. |
| `TZ` | `EST5EDT,M3.2.0,M11.1.0` | POSIX TZ string | Time zone of the UI clock (the panel runs in UTC). |
| `TZ_LOCAL` | `EST5EDT,M3.2.0,M11.1.0` | POSIX TZ string | Time zone of the display schedule (`SLEEP_START` / `SLEEP_END`). Set both the same. |
| `WEATHER_FX` | `full` | `off`, `sky`, `full` | Animated sky behind the clock on Home (beta). `sky` = still sky that follows the weather and sun, about 4 % CPU. `full` adds rain, snow, clouds, fog, leaves and lightning and costs about one CPU core in bad weather, so the panel runs warm. **`sky` is recommended** (the example file sets it). |
| `CLIMATE_CONTROLLER` | (empty) | climate entity | A climate entity that drives your thermostats, such as a Flair "structure". Thermostat tiles then send mode and setpoint to it and only the fan to the thermostat. Empty = every tile controls its own entity. |
| `CLIMATE_THERMOSTATS` | auto (those with fan modes) | comma-separated climate entities | Which climate entities count as thermostats for `CLIMATE_CONTROLLER`. |
| `SECURITY_TAB` | `1` | `1`, `0` | `0` hides the Security tab on this panel. |
| `SECURITY_ACCESS` | `control` | `control`, `view`, `off` | Security tab rights on this panel: full control, status only, or no tab, alarm, locks or doorbell pop-ups ([section 2.13](#213-multi-unit-and-rental-properties-lite-groups)). |
| `SECURITY_DEVICES` | (empty = all) | comma-separated names | Ring/Schlage devices this panel may see and control, by name, case-insensitive. `alarm` includes the Ring Alarm. |
| `DOORBELL` | `1` | `1`, `0` | Doorbell pop-up on a press. `0` = no pop-ups (camera tiles still work). |
| `DOORBELL_SECS` | `45` | seconds | How long the pop-up stays open. |
| `DOORBELL_MOTION` | `0` | `1`, `0` | `1` = also pop up on camera motion events. |
| `DOORBELL_CAMERAS` | auto (matched by name) | `event.x=camera.y,...` | Explicit doorbell-to-camera pairs. |
| `CAMERA_LIVE` | (empty) | `camera.x=rtsp://host:port/path,...` | H.264 RTSP streams for live video. CuboAI cameras with the H.264 transcode on are found automatically. |
| `CAMERA_LIVE_ON` | `1` | `1`, `0` | `0` = full-screen cameras show stills only. |

### Display

| Key | Default | Values | What it does |
|---|---|---|---|
| `SLEEP_START` / `SLEEP_END` | `21:00` / `06:30` | `HH:MM`, 24 h local | Night window: screen off. |
| `NIGHT_MODE` | `1` | `1`, `0` | Night schedule on/off. Also the MQTT **Night mode** switch. |
| `DAY_LEVEL` | `15` | `0`-`15` | Awake backlight level. Also the MQTT **Day brightness** number. |
| `DIM_LEVEL` | `4` | `0`-`15` | Idle level when `SCREENSAVER=0`. |
| `MIN_LEVEL` | `5` | `0`-`15` | Darkest awake level with smart dimming (dark room, lights off, after sunset). |
| `IDLE_DIM_SECS` | `300` | seconds | Without a touch before the screensaver or dim (day). |
| `NIGHT_WAKE_SECS` | `180` | seconds | How long a touch at night keeps the screen on. |
| `SCREENSAVER` | `1` | `1`, `0` | `1` = photo slideshow, `0` = just dim. Also the MQTT **Screensaver** switch. |
| `SCREENSAVER_WEATHER` | `1` | `1`, `0` | Show the current weather on the screensaver. |
| `PHOTO_LEVEL` | `12` | `0`-`15` | Screensaver backlight level. |
| `PHOTO_SECS` | `15` | seconds | Seconds per photo. |
| `HA_PHOTO_FOLDER` | (empty = all of My media) | folder name | Folder in Home Assistant's **My media** to sync photos from. |
| `PHOTO_SYNC_MINS` | `30` | minutes, `0` = off | How often panels check Home Assistant for new or removed photos. |
| `PHOTO_DIRS` | (empty) | space-separated panel paths | Advanced: use exactly these folders on the panel, ignoring the photo rules. |
| `STOCK_ART` | `fallback` | `fallback`, `mix`, `off` | Brilliant's preloaded art: only when there are no photos, always added, or never. |
| `MOTION_WAKE` | `day` | `day`, `always`, `off` | Wake on the faceplate motion sensor. Also the MQTT **Motion wake** switch. |
| `TOUCH_DEVICE` | `/dev/input/event0` | device path | The touchscreen input device the display service watches for touches. Change only if your panel differs. |

### Smart dimming

| Key | Default | Values | What it does |
|---|---|---|---|
| `SMART_DIM` | `1` | `1`, `0` | Follow the room's light level. Also the MQTT **Smart dimming** switch. |
| `LIGHT_SENSOR` | (empty) | illuminance sensor entity | Lux sensor in the room; overrides the estimate when available. Usually per panel. |
| `LUX_DARK` / `LUX_BRIGHT` | `5` / `300` | lux | Lux at or below which the screen is at `MIN_LEVEL`, and lux for full brightness. |
| `ROOM_LIGHTS` | (empty) | comma-separated light/switch entities | Lights in the room, for the estimate. A light counts by its brightness; a switch counts as fully on. Per panel. |
| `SUN_DARK_ELEV` / `SUN_BRIGHT_ELEV` | `-4` / `10` | degrees | Sun elevation counted as dark and as full daylight. |
| `AMBIENT_SECS` | `60` | seconds | How often to re-evaluate. |

### This switch and gestures

| Key | Default | Values | What it does |
|---|---|---|---|
| `LOAD_ENTITY` | (empty) | light entity | Home Assistant entity of this panel's wired load. Tiles for it are controlled locally and gestures drive it. Per panel. |
| `ROOM_SPEAKERS` | (empty) | comma-separated media_player entities | Speakers for three-finger volume. Per panel. |
| `GESTURE_2_TAP` | `load.toggle` | action | Two-finger tap. |
| `GESTURE_3_TAP` | (empty) | action | Three-finger tap. |
| `GESTURE_2_SWIPE_LEFT`, `GESTURE_2_SWIPE_RIGHT`, `GESTURE_3_SWIPE_LEFT`, `GESTURE_3_SWIPE_RIGHT` | (empty) | action | Horizontal swipes. |
| `GESTURE_2_SWIPE_UP`, `GESTURE_2_SWIPE_DOWN` | built-in dimming | action | Setting either replaces two-finger dimming. |
| `GESTURE_3_SWIPE_UP`, `GESTURE_3_SWIPE_DOWN` | built-in volume | action | Setting either replaces three-finger volume. |

Actions: `load.toggle`, `load.on`, `load.off`, `page:<n>`, `<domain>.<service>`,
`<domain>.<service>:<entity_id>` ([section 5](#5-this-switch-local-control-and-gestures)).

### MQTT

| Key | Default | Values | What it does |
|---|---|---|---|
| `MQTT_HOST` | (empty) | host or IP | Broker address. Empty = the kiosk does not connect to a broker (no Home Assistant device, no intercom, no capture events, no discovery). |
| `KIOSK_MQTT` | `1` | `1`, `kiosk`, `0` | MQTT provider: `1` = built-in kiosk MQTT publishes the whole panel; `kiosk` = brilliant-mqtt publishes the panel and the kiosk only its own entities; `0` = legacy, no kiosk MQTT ([section 2.7](#27-connect-the-panels-to-home-assistant-over-mqtt)). |
| `MQTT_DEVICE_ID` | (empty) | device identifier | With `KIOSK_MQTT=kiosk`: the brilliant-mqtt Home Assistant device the kiosk's entities join. |
| `MQTT_PORT` | `1883` | port | Broker port. |
| `MQTT_USER` / `MQTT_PASSWORD` | (empty) | text | Broker login. `kiosk.env` is root-only on the panel. |
| `MQTT_BASE` | `brilliant_kiosk` | topic prefix | Prefix for state and command topics (`<MQTT_BASE>/<node>/...`). A custom prefix stops the Home Assistant integration's discovery; add such a panel by hand. |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | topic prefix | Home Assistant's MQTT discovery prefix. |
| `MOTION_CLEAR_SECS` | `60` | seconds | How long the Motion sensor stays on after motion. |
| `DIAG_SECS` | `60` | seconds | Poll interval of the read-only diagnostics (other loads' power, temperatures, firmware, HomeKit, Shelly BLE, dimming calibration). |

### Voice (optional)

| Key | Default | Values | What it does |
|---|---|---|---|
| `KIOSK_VOICE` | `0` | `1`, `0` | Run the voice satellite and announcement speaker ([section 2.9](#29-voice-assistant-and-announcements-optional-off-by-default)). Per panel. |
| `VOICE_PORT` | `10700` | port | Wyoming TCP port, LAN address only. |
| `VOICE_NAME` | `Brilliant panel` | text | Satellite name shown in Home Assistant's Wyoming integration. |
| `VOICE_WAKE` | `off` | `off`, `ha` | `off` = push-to-talk; `ha` = stream the mic continuously for Home Assistant's wake-word engine. |
| `VOICE_ALLOW` | host in `HA_URL` | comma-separated hosts | Hosts allowed to connect. |
| `VOICE_BIND` | auto | IP address | LAN address to listen on (default: the one that reaches Home Assistant). |
| `VOICE_LISTEN_SECS` | `15` | seconds | Longest listen after Talk or a wake word. |
| `VOICE_TTS_ENGINE` | `tts.google_translate_en_com` | tts entity | Text-to-speech entity for plain-text announcements. |
| `VOICE_MIC` | `default` | ALSA device | Capture device: `default`, `dsnoop_filtered`, `plug_dsnoop_48000`. |
| `VOICE_SPEAKER` | `dmix_filtered` | ALSA device | Playback device. `dmix_filtered` follows Brilliant's volume; `default` plays at full level. |
| `VOICE_DEBUG` | `0` | `1`, `0` | Verbose logging of the voice service. |

### Intercom (optional)

| Key | Default | Values | What it does |
|---|---|---|---|
| `KIOSK_INTERCOM` | `0` | `1`, `0` | Run the intercom and play what other panels send ([section 2.10](#210-intercom-between-panels-optional-off-by-default)). Needs MQTT. Per panel. |
| `INTERCOM_MIC` | `0` | `1`, `0` | This panel can also talk (opens the mic while talking). `0` = receive only. |
| `INTERCOM_PORT` | `10710` | port | UDP port for audio; the local control port is this + 1. |
| `INTERCOM_MAX_SECS` | `30` | seconds | Longest single talk. |
| `INTERCOM_MIC_DEVICE` | `default` | ALSA device | Capture device. |
| `INTERCOM_SPEAKER` | `dmix_filtered` | ALSA device | Playback device. |
| `INTERCOM_BIND` | auto | IP address | Address to send and receive on (default: the one that reaches `MQTT_HOST`). |

### Capture (optional)

| Key | Default | Values | What it does |
|---|---|---|---|
| `KIOSK_CAPTURE` | `0` | `1`, `0` | Capture screen and clip API ([section 2.11](#211-voice-and-video-capture-api-optional-off-by-default)). Per panel. |
| `CAPTURE_VIDEO` | `0` | `1`, `0` | Also camera snapshots and video clips (needs `KIOSK_CAPTURE=1`). |
| `CAPTURE_PORT` | `10720` | port | LAN port that serves clips; the local control port is this + 1. |
| `CAPTURE_ALLOW` | host in `HA_URL` | comma-separated hosts | Hosts allowed to download clips. |
| `CAPTURE_BIND` | auto | IP address | LAN address the clip server listens on. |
| `CAPTURE_MAX_SECS` | `30` | seconds | Longest voice clip (video is always 10 s or less). |
| `CAPTURE_TTL` | `300` | seconds | How long a clip stays downloadable. |
| `CAPTURE_MIC` | `default` | ALSA device | Capture device (shared with Brilliant and the voice service). |
| `CAPTURE_UPLOAD` | `0` | `1`, `0` | Also upload every clip into Home Assistant's media library and add `media_id` to the event. Needs an admin token. |
| `CAPTURE_UPLOAD_DIR` | (empty = top level) | folder under `/media` | Upload folder; it must already exist. |

### Groups and access (optional)

| Key | Default | Values | What it does |
|---|---|---|---|
| `PANEL_GROUP` | (empty) | `a-z`, `0-9`, `-` | Group this panel belongs to. Adds `.private/<group>.group.env` as a settings layer and limits the intercom to the group. Set it in `.private/<panel>.kiosk.env`. |
| `ALLOW_MIC` | `1` | `1`, `0` (`off`, `false`, `no`) | `0` locks the voice satellite, intercom mic and audio capture. A `0` in any layer wins. |
| `ALLOW_CAMERA` | `1` | `1`, `0` (`off`, `false`, `no`) | `0` locks snapshots and video capture. A `0` in any layer wins. |

### System

| Key | Default | Values | What it does |
|---|---|---|---|
| `CPU_AWAKE_GOV` / `CPU_IDLE_GOV` / `CPU_SLEEP_GOV` | `performance` / `schedutil` / `powersave` | Linux cpufreq governor | CPU governor while awake, during the screensaver or dim, and at night. |
| `SWAP_MB` | `256` | MB, `0` = none | Swap file size (the Brilliant kernel has no zram). |
| `SWAPPINESS` | `10` | `0`-`100` | Use swap only under memory pressure. |

---

## 8. Maintenance: updates, channels, rollback, uninstall

### Release channels

| Channel | Where | Use it when |
|---|---|---|
| **Stable** | Tags `vX.Y.Z` on [GitHub releases](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/latest); current **v1.4.0** | You want tested releases. |
| **Nightly** | Prereleases `nightly-YYYYMMDD`, built from the `nightly` branch whenever it changed | You want new features early and can live with rough edges. |

Each release has a bundle `brilliant-ha-kiosk-<version>.tar.gz` with a `.sha256` file. A nightly
bundle's version is `<last stable>-nightly.YYYYMMDD.g<commit>`, for example
`v1.4.0-nightly.20261108.g89ec239`. The [changelog](../CHANGELOG.md) lists what is on each
channel. Only the newest 15 nightlies are kept on GitHub.

Switching channels with the scripts is a checkout and an install:

```bash
git fetch --tags
git checkout v1.4.0 && scripts/install.sh all          # stable
git checkout nightly && git pull && scripts/install.sh all   # nightly
```

The Home Assistant integration has a **Release channel** select per panel and an update entity
that offers the newest release of that channel ([home-assistant-integration.md](home-assistant-integration.md)).

### Updating

```bash
git pull                     # on the branch or tag you use
scripts/install.sh all       # or scripts/install.sh <panel>
```

Every install goes through `ha-kiosk-setup` on the panel ([section 2.4](#24-install)): it verifies
the bundle, keeps the previous release, waits up to 60 s for the UI to connect to Home
Assistant, and rolls back (exit 5) or restores Brilliant's own screen (exit 6) if it does not.
`install.sh` prints which.

**What an install does with your settings.** `install.sh` only reads `.private/`. On the panel it
replaces the settings with the fresh combination of `kiosk.env` + group file + panel file,
replaces the token and the fallback layout, updates the program files, and keeps: choices made
from Home Assistant switches (`/var/lib/ha-kiosk/overrides.env`), photos synced from Home
Assistant, and the security and capture state. Edits made by hand on the panel are overwritten.

### ha-kiosk-setup on the panel

Once installed, `/var/lib/ha-kiosk/ha-kiosk-setup` survives firmware updates. Run it as root on
the panel. Every command prints one JSON object (status or an error with an exit code).

| Command | Does |
|---|---|
| `install --bundle FILE --sha256 HEX [--size BYTES] [--env FILE] [--token FILE] [--layout FILE] [--allow-offline]` | Verify and install a bundle, then activate it. `--allow-offline` accepts a UI that comes up without reaching Home Assistant. |
| `status` | JSON: `state` (`not_installed`, `unmanaged`, `active`, `disabled`, `failed`, `in_progress`), version, previous version, releases, UI heartbeat, services, MQTT mode, firmware, last result |
| `diagnostics` | `status` plus the last 250 journal and setup-log lines, secrets redacted |
| `rollback` | Activate the previous release |
| `disable` / `enable` | Hand the screen back to Brilliant's UI and keep everything / start the kiosk again |
| `reapply` | Re-activate the current release (after a firmware update replaced `/etc`) |
| `uninstall` | Remove the kiosk, its code and every secret; keep photos and preferences in `/var/lib/ha-kiosk-keep` for a later reinstall |
| `purge` | Uninstall and also delete photos and preferences |
| `check-env FILE` | Validate a settings file without installing (`-` = stdin) |
| `wait [--timeout SECS]` | Wait for a `--detach` job (default 100 s); exit 9 if still running |

Turning the kiosk on or off restarts Brilliant's message bus, which alone takes 60 to 90 s. Add
`--detach` to any changing command to run it in the background and return at once, then call
`wait`. Exit codes: 0 ok, 1 error, 2 usage, 3 bundle verification failed, 4 settings or token
rejected, 5 rolled back to the previous release, 6 Brilliant's UI restored, 7 not installed,
8 busy, 9 still in progress. The full contract is in [INTEGRATION.md](INTEGRATION.md).

### Installing from a release bundle (without this repository)

Fleet tools and the Home Assistant integration install the GitHub release bundle directly. By
hand, on the panel as root:

```sh
V=v1.4.0; SHA=<sha256 from the release's .sha256 file>
echo "$SHA  brilliant-ha-kiosk-$V.tar.gz" | sha256sum -c
tar -xzOf brilliant-ha-kiosk-$V.tar.gz brilliant-ha-kiosk-$V/ha-kiosk-setup > setup.py
/data/switch-embedded/env/bin/python setup.py install --bundle brilliant-ha-kiosk-$V.tar.gz \
    --sha256 $SHA --env /path/kiosk.env --token /path/token
```

On an update, `--env` and `--token` can be left out: the tool keeps the panel's settings and
token. Underneath, each release's `install-local.sh` does the file work. It is non-interactive
and safe to re-run, and a copy stays at `/etc/ha-kiosk/install-local.sh`:

| `install-local.sh` option | Effect |
|---|---|
| `--env FILE` | Install this `kiosk.env` (otherwise the panel's current one is kept) |
| `--token FILE` | Install this Home Assistant token (otherwise kept) |
| `--layout FILE` | Local fallback `layout.json` (otherwise kept, else the bundled example) |
| `--no-mqtt` | Writes `KIOSK_MQTT=0` (legacy; prefer `KIOSK_MQTT=kiosk` for brilliant-mqtt) |
| `--no-restart` | Install files and units without starting anything |
| `--uninstall [--purge]` | Restore the stock Brilliant UI (with `--purge`: delete all kiosk files, settings, photos and the swap file) |
| `--version` | Print the bundle version |

### Logs and live state

```bash
scripts/logs.sh panel1 100          # last 100 lines from the UI service
```

On the panel: `journalctl -u ha-kiosk -u ha-kiosk-display -u ha-kiosk-bridge -f` (add
`-u ha-kiosk-voice`, `-u ha-kiosk-intercom`, `-u ha-kiosk-capture` as needed). The Home Assistant
integration's `logs` action returns the same, redacted.

| File on the panel | Contents |
|---|---|
| `/run/ha-kiosk-display/state` | `awake`, `photos`, `dim` or `asleep` |
| `/run/ha-kiosk-display/levels` | Current smart-dimming levels (awake, screensaver) |
| `/run/ha-kiosk/heartbeat.json` | The UI's heartbeat and Home Assistant connection state |
| `/var/lib/ha-kiosk/overrides.env` | Runtime overrides from the MQTT switches |
| `/var/lib/ha-kiosk/setup.log` | `ha-kiosk-setup` log |
| `echo 2 > /run/ha-kiosk-display/page` | Switch the UI to tab 2 (for testing) |

### Brilliant OS updates

Over-the-air updates stay enabled. They replace the read-only system image; everything the kiosk
installs lives in `/etc` and `/var`, which updates are expected to keep. If an update ever brings
the stock UI back, run `/var/lib/ha-kiosk/ha-kiosk-setup reapply` on the panel (or
`scripts/install.sh` again, or the integration's **Reapply kiosk** button).

### Uninstall

```bash
scripts/uninstall.sh panel1            # stock UI back; kiosk files stay for a quick reinstall
scripts/uninstall.sh panel1 --purge    # also delete all kiosk files, settings, photos and the swap file
```

On the panel, `ha-kiosk-setup uninstall` removes the code and every secret and keeps photos and
preferences; `ha-kiosk-setup purge` removes those too. Revoke the Home Assistant token and the
MQTT login if the panel leaves your home. Deleting a panel in the Home Assistant integration does
not uninstall the kiosk.

### Security notes

* The Home Assistant token is stored only on the panel (root-only) and in your `.private/`
  folder. Revoke it in Home Assistant at any time.
* The local bridge listens on `127.0.0.1` only. The voice and capture services open one LAN
  port each, only while they run and only for the allowed hosts; the intercom uses UDP on a port
  Brilliant's firewall already accepts.
* Pin the panel's host key (`PANEL_HOSTKEY`) and change the root password if you have shared it.
* Panel logs can contain Brilliant's HomeKit setup code while a pairing window is open.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Red **"Connecting to Home Assistant"** banner | `HA_URL` must be the LAN address the panel can reach (not a VPN `100.x` address or `homeassistant.local`). Check `.private/token`; `scripts/logs.sh panel1` shows `auth invalid` for a bad token. |
| `install.sh` says **settings rejected** | An unknown key or a value with `"`, `'`, `` ` ``, `$` or `\`. The message names the line. Only the keys in [section 7](#7-configuration-reference) are accepted. |
| `install.sh` ends with **"the previous version is running again"** (exit 5) or **"Brilliant's own screen is back"** (exit 6) | The new release did not connect to Home Assistant within 60 s. Check `HA_URL` and the token, then `ssh root@<panel> /var/lib/ha-kiosk/ha-kiosk-setup diagnostics`. |
| Screen stays **black after a touch** | `systemctl status ha-kiosk-display` on the panel. Make sure the drop-in `/etc/systemd/system/message_bus.service.d/ha-kiosk-display.conf` exists; re-run `install.sh`. |
| **Clock is wrong** by hours | Set `TZ` and `TZ_LOCAL` to your POSIX time zone. |
| **Layout doesn't update** when you save in Home Assistant | The token's user must be an administrator. Check `LAYOUT_DASHBOARD`. The log shows `no live layout updates` if the subscription was refused. |
| A tile shows **Unavailable** | The entity id does not exist in Home Assistant, or the device is offline. |
| An icon is **blank** | Use a Material Design icon name (`mdi:...`). |
| **Gestures don't control the light** | Set `LOAD_ENTITY`. Check the bridge: `systemctl status ha-kiosk-bridge`, then `wget -qO- http://127.0.0.1:8765/load` on the panel. |
| **Three-finger volume** does nothing | Set `ROOM_SPEAKERS` to your `media_player` entity ids. |
| **Motion wake** never fires | Brilliant's motion detection may be off on your panel. Check with `tools/brilliant_bus.py get faceplate_peripheral`. Use `MOTION_WAKE=off` if it misfires. |
| **Screensaver shows the wrong photos** | Add your own (`.private/photos/` or Home Assistant **Media, My media**); then only yours are shown. `journalctl -u ha-kiosk-display` says which source is in use. |
| New Home Assistant photos don't appear | Wait up to `PHOTO_SYNC_MINS`, or `systemctl restart ha-kiosk-bridge`. `journalctl -u ha-kiosk-bridge` shows `photos: N from HA, +added -removed`. |
| **Favorites empty** on the Music tab | Sonos: star stations and playlists in the Sonos app. Other players: check the player's media browser in Home Assistant; the tab shows what Home Assistant shows there. |
| Panel doesn't appear in Home Assistant (MQTT) | Check `MQTT_HOST`, `MQTT_USER`, `MQTT_PASSWORD`. `journalctl -u ha-kiosk-bridge` shows `mqtt: connected` or the error. Home Assistant's MQTT integration must be set up. |
| Duplicate entities for the wired light | brilliant-mqtt and the kiosk's built-in MQTT are both publishing. Set `KIOSK_MQTT=kiosk` and `MQTT_DEVICE_ID`, or stop brilliant-mqtt. |
| A Home Assistant switch (screensaver, night, ...) "doesn't stick" | It is saved in `/var/lib/ha-kiosk/overrides.env` and wins over `kiosk.env`. Delete that file to go back to `kiosk.env`, or use the integration's `set_env`, which purges the override for keys it sets. |
| The panel runs **warm** or the UI stutters | `WEATHER_FX=full` costs about one CPU core in bad weather. Set `WEATHER_FX=sky`. |
| **Voice**: Home Assistant can't add the Wyoming device | `systemctl status ha-kiosk-voice` on the panel; is `KIOSK_VOICE=1` and `ALLOW_MIC` not `0`? The log shows `Wyoming satellite on <ip>:10700, accepting <HA ip>`. If Home Assistant connects from another address (a Docker host), set `VOICE_ALLOW`. |
| **Voice**: Talk ends at once with `stt-stream-failed` | The pipeline's speech-to-text does not work (for example no Home Assistant Cloud subscription). Pick another engine in *Settings, Voice assistants*. |
| **Intercom**: no microphone button | Needs `KIOSK_INTERCOM=1` **and** `INTERCOM_MIC=1` on this panel, MQTT, and another intercom panel online in the same group. `journalctl -u ha-kiosk-intercom` lists `peers: ...`. |
| **Intercom**: the other panel stays silent | Press **Intercom test tone**. The receiving panel's log should show `listening to <room>`; if not, check UDP `INTERCOM_PORT` between the panels and its speaker volume. |
| **Capture**: clip URL gives 403 | The downloading host is not in `CAPTURE_ALLOW` (default: the host in `HA_URL`). |
| **Security** tab missing | Brilliant reports no Ring or Schlage devices (link them in the Brilliant app), or `SECURITY_TAB=0` / `SECURITY_ACCESS=off`. |
| The stock UI came back after a firmware update | `/var/lib/ha-kiosk/ha-kiosk-setup reapply` on the panel. |
| Want the stock UI back | `scripts/uninstall.sh panel1` |

---

## 10. Tools for tinkerers

| Tool | Runs on | What it does |
|---|---|---|
| `tools/brilliant_bus.py` | Panel | `list`, `get`, `watch`, `set` on Brilliant's message bus. Writes are check-and-set and only allowed for settable variables. |
| `tools/inject_touch.py` | Panel | Replays taps and multi-finger swipes into the touchscreen, for testing gestures remotely. |
| `tools/gen_icons.py` | Computer | Regenerates `native/icons.json` from the Material Design Icons CSS. |
| `scripts/publish_layout.py` | Computer | Creates the *Panel Layout* dashboard from a `layout.json` (`--force` overwrites). |
| `scripts/publish_dashboard.py` | Computer | Web mode: publishes a dashboard YAML (`.private/wall-panel.yaml` by default) as the `wall-panel` dashboard. Needs `pip install pyyaml websockets`. |
| `scripts/build-bundle.sh [version]` | Computer | Builds `dist/brilliant-ha-kiosk-<version>.tar.gz`, its `.sha256` and a `release.json`, reproducibly. |

```bash
# on the panel
cd /data/switch-embedded
env/bin/python /etc/ha-kiosk/brilliant_bus.py list
env/bin/python /etc/ha-kiosk/brilliant_bus.py get gangbox_peripheral_0
env/bin/python /etc/ha-kiosk/brilliant_bus.py watch faceplate_peripheral movement_detected
```

How the bus works is documented in
[hardware-and-services.md, section 6](hardware-and-services.md).
The Home Assistant integration's `bus_get` and `bus_set` actions do the same from Home Assistant.

---

*Brilliant HA Kiosk. Apache License 2.0, 2026 Mike Ippolito (releases before v1.3.0: MIT; see NOTICE).*
