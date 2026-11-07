# Changelog

All notable changes to brilliant-ha-kiosk. There are two channels (see the README's
[Releases](README.md#releases) section):

- **Stable:** tagged `vX.Y.Z` releases. The newest is marked *Latest* on GitHub.
- **Nightly:** the `nightly` branch, built each night into a `nightly-YYYYMMDD` prerelease when it
  changed. New work, including integrations with other services, lands here first.

## Unreleased (nightly)

Available in the newest [nightly prerelease](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases?q=nightly&expanded=true).
These changes will be part of the next stable release.

### New

- **Animated sky on Home** (beta). The clock and weather sit on a sky that follows the condition
  and the sun (dawn, day, dusk, night), with rain, snow, drifting clouds, fog, wind-blown leaves,
  stars, the sun or the moon, and lightning flashes. Drag a finger across it to blow the rain
  or snow aside; waking the screen gives a short flourish. `WEATHER_FX=off|sky|full` in
  `kiosk.env` (default `full`; `sky` keeps the colours without motion).
- **Full-screen weather page.** Tap the sky: wind (speed and direction), humidity, pressure,
  visibility, sunrise and sunset, day length, moon phase, a 24-hour temperature and rain-chance
  chart (drag along it to preview each hour's sky), and a 7-day forecast with highs, lows and
  rain chances.
- **Weather note** under the temperature, worked out on the panel from the hourly forecast:
  "Rain until about 3 PM", "Snow from 1 PM".
- Works with weather integrations that have no daily forecast (such as the National Weather
  Service): the daily view is built from the day/night forecast.

### Fixed

- Restarting the kiosk (for example after an install) no longer fills the screen with
  `ioctl ...: Bad file descriptor` text from Brilliant's boot splash.
- Forecast lookups that fail in the background no longer show a "Couldn't do that" message.

### Project

- Nightly channel: `nightly` branch and nightly prerelease bundles (`.github/workflows/nightly.yml`).

## [v1.4.0](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.4.0) — 2026-11-07 — live camera video, zoom, safer installs

### New

- **Live camera video.** The full-screen camera view plays H.264 video on the panel's hardware video decoder, the same way Brilliant's own UI played Ring video. It starts in about 4 s and shows a LIVE badge. CuboAI cameras with the integration's *H.264 transcode* turned on are found automatically; add other H.264 RTSP cameras with `CAMERA_LIVE`. The panel cannot decode H.265.
- **Pinch to zoom.** Pinch or double-tap to zoom (up to 4×), drag to pan, double-tap to reset. Works on live video and on stills.
- **Safer installs with `ha-kiosk-setup`.** Every install checks the bundle's SHA-256, keeps the previous version, and waits for the screen to come up and connect to Home Assistant. If it doesn't, the panel rolls back by itself. Also: `reapply` after firmware updates, `disable`/`enable`, `rollback`, `uninstall`/`purge`, `status`, `diagnostics`, and `--detach` + `wait` for tools with SSH time limits.
- **Matching Home Assistant theme** (`ha/themes/brilliant-kiosk.yaml`) so HA's web UI and phone app share the panel's look.
- **`KIOSK_MQTT=kiosk`** mode for running alongside other panel integrations, with a retained `kiosk_state` topic.
- Reproducible release bundles with `release.json` (name, version, SHA-256, size).

### Fixed

- CuboAI camera tiles stayed blank: their stills only work while the stream runs, so the panel now keeps it awake while the tile is on screen.
- Clips uploaded by the Capture tab (`CAPTURE_UPLOAD=1`) no longer show up in the photo screensaver.

### Upgrading

`git pull`, then `scripts/install.sh all`. See docs/MANUAL.md (Cameras, section 8 Maintenance) and docs/INTEGRATION.md section 0.

## [v1.3.2](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.3.2) — 2026-11-06 — docs

Documentation only: no change to the software on the panels.

- **Clearer setup files.** The README and manual now describe each file in `.private/` by its job, using the same names everywhere:
  - `kiosk.env`: the settings for every panel.
  - `panelN.env`: only how to reach that panel (IP, password).
  - `panelN.kiosk.env` (optional): only what differs for that panel. Without it, the panel uses `kiosk.env` as is.
  - `<group>.group.env` (optional): settings for a group of panels.
- **New `config/panel.kiosk.env.example`:** a template for the optional per-panel file.
- **What an install does with your settings:** `install.sh` never changes your files in `.private/`. It replaces each panel's settings with a fresh combination of your files, keeping per-panel features. Choices made from Home Assistant switches, photos synced from HA, and the Security and capture state are kept. `install.sh all` both upgrades and re-applies settings.

Panels on v1.3.1 don't need reinstalling.

## [v1.3.1](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.3.1) — 2026-11-06

Multi-unit groundwork, privacy locks, and a few fixes.

### Multi-unit and rental properties ("lite groups")
- **Settings in three layers:** the whole property (`.private/kiosk.env`), a group such as a unit (`.private/<group>.group.env`, joined with `PANEL_GROUP=`), and the panel itself.
- **Intercom stays within a group:** panels only see and accept panels in their own group.
- **Security access per panel or group:** `SECURITY_ACCESS=control|view|off` and `SECURITY_DEVICES=` limit what the Security tab shows and allows. Requests are refused in the panel service too, not just hidden on screen. A doorbell press only wakes panels allowed to see that doorbell.
- **Microphone and camera locks:** `ALLOW_MIC=0` / `ALLOW_CAMERA=0`. A lock at any layer wins over the layers below it. The panel then never enables voice, the intercom microphone or capture for that device, and each service checks again at runtime. A new **Privacy** sensor in Home Assistant shows each panel's policy.
- The manual's new section **2.13** explains what this does and doesn't isolate. Shared Home Assistant has no per-entity permissions, so real tenant isolation still needs a Brilliant home, Home Assistant and network per unit.

### Easier with many panels
- `scripts/install.sh all` installs or updates every panel in `.private/` in one go.
- README: one shared setup for any number of panels, a clearer quick start (design the screens in HA; the starter-layout script is optional), and the Music tab described as what it is: one screen for any Home Assistant music player.

### Fixes
- The Music tab opens on this room's speaker (`ROOM_SPEAKERS`) instead of the first in the list, and returns to it after the panel goes back Home.

Nothing changes for single-home setups that don't use the new settings; each panel just gains the Privacy sensor showing `open`.

### Upgrading
Run `scripts/install.sh all` from an updated checkout, or unpack the bundle below on each panel and run `sh install-local.sh`. Your settings, token, layout and photos are kept.

## [v1.3.0](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.3.0) — 2026-11-06

The biggest release yet: a faster UI, voice and video capture for your own automations, a Security tab built on Brilliant's own Ring and Schlage integrations, and a new license.

### Faster
- **Smoother swiping and scrolling.** Only the pages on screen are drawn, there are no file reads mid-swipe, and sliders draw in fewer steps.

### Security tab (Ring Alarm, Schlage locks, Ring doorbells and cameras)
Uses Brilliant's own Ring and Schlage integrations, linked in the Brilliant app. Home Assistant's Ring integration has no Ring Alarm support; on a Brilliant panel you get it.
- **Alarm:** status, **Arm home / Arm away**, and **Disarm** with your code (encrypted for the alarm, never stored).
- **Locks:** lock with one tap, unlock with two.
- **Doorbells & cameras:** each Ring device as a camera tile; a doorbell press wakes the panel and shows that door full-screen.
- **Safety:** each control is checked against the firmware without touching the devices. If a Brilliant update changes something, only that control is switched off, with the reason on the panel and in a new **Security controls** sensor in Home Assistant. Disarms are confirmed, and repeated failures pause panel disarm.
- Home Assistant gets read-only status: alarm sensor, lock sensors and a doorbell event.
- See *How the alarm and lock control works* in the manual.

### Voice and video capture API (opt-in, off by default)
- `KIOSK_CAPTURE=1` adds a **Capture** screen; `CAPTURE_VIDEO=1` adds snapshots and short clips. Nothing listens all the time.
- Every clip fires a **Capture** event in Home Assistant with a short-lived link. `CAPTURE_UPLOAD=1` also puts it in **Media > My media**. HA can start recordings and send text replies to the screen.
- Build your own automations on it, for example sending a clip to an AI task. Manual section 2.11 has the full API.

### More
- **House thermostat controller** (`CLIMATE_CONTROLLER`): for setups where one entity, such as a Flair structure, drives the thermostats. Thermostat tiles then send mode and setpoint to the controller and the fan to the thermostat.
- **Camera tiles** for camera cards on your dashboards.
- **Why Brilliant hardware:** a new README section on what Brilliant's engineering, services and integrations add.

### License
From this release the project is licensed under the **Apache License 2.0** (see `LICENSE` and `NOTICE`). Releases before v1.3.0 were published under the MIT License, and copies of them keep those terms.

### Upgrading
Run `scripts/install.sh <panel>` for each panel from an updated checkout, or unpack the bundle below on the panel and run `sh install-local.sh`. Your settings, token, layout and photos are kept.

## [v1.2.1](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.2.1) — 2026-11-06

### Fixes

- **Group volume keeps your mix.** On the Music tab, the volume bar for a speaker group now moves every speaker together and keeps their balance. The panel remembers each speaker's share, so turning the group down to zero and back up restores the mix instead of setting every speaker to the same level.
- **A volume bar for each speaker in a group.** When speakers are grouped, each one gets its own bar under the group bar, so you can set them individually.
- **No "Bad file descriptor" text when the kiosk restarts.** Restarting the UI (for example after an update) no longer flashes error text on the screen. The kiosk is back within a couple of seconds, and uninstalling still hands the screen to Brilliant's own UI.

### Upgrading
Run `scripts/install.sh <panel>` for each panel from an updated checkout, or unpack the bundle below on the panel and run `sh install-local.sh`. Your settings, token, layout and photos are kept.

---
*Released under the MIT License. From v1.3.0 the project is licensed under Apache-2.0 (see NOTICE).*

## [v1.2.0](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.2.0) — 2026-11-06

A big step for the panel UI, plus groundwork for installing and managing the kiosk from other tools.

### A more dependable UI
- **Taps you can trust.** Brightness and volume bars change only on a real drag, so brushing a tile can't change a light; after you let go the bar stays where you left it until the device confirms. Taps work straight away while the screen is dimmed.
- **Feedback for every command.** A tile shows an amber outline while a command is on its way, and a short message explains when Home Assistant rejects one.
- **Clear connection status.** The banner says whether the panel is connecting, reconnecting (and for how long), or has a rejected token; pages fade while data is stale. The panel's own wired light keeps working offline.
- **No blank screens.** A missing or empty dashboard now says why and what to do.

### Easier to read
- Brighter secondary text, larger status text, sensor values and section titles.
- Tiles that are on get a warm tint across the whole card; unavailable devices say so.
- Plain thermostat modes ("Heat/Cool", "Heating") and bigger −/+ buttons.
- After 2 minutes idle the panel returns to Home and scrolls pages to the top; a soft fade hints when a page scrolls.

### More devices you can control
- **Covers and valves:** open, stop, close and position. Garage doors and gates need two taps.
- **Locks:** lock with one tap, unlock with two.
- **Fans and humidifiers:** on/off with a speed or humidity bar.
- **Numbers, dropdowns and buttons:** sliders or −/+, option chips, and tap-to-press.
- **Media:** only the buttons a player supports are shown; favourites show a loading state and highlight what you just started.

### Groundwork for other installers
- `docs/INTEGRATION.md`: a draft contract for tools that install and manage the kiosk (installer commands, rollback, configuration, MQTT and display control).
- Published release bundles are never replaced, so their checksums can be pinned.
- Load power is now reported in watts (the panel reports tenths of a watt).
- The voice service sends the Home Assistant token only to the exact configured address.

### Upgrading
Run `scripts/install.sh <panel>` for each panel from an updated checkout, or unpack the bundle below on the panel and run `sh install-local.sh`. Your settings, token, layout and photos are kept.

---
*Released under the MIT License. From v1.3.0 the project is licensed under Apache-2.0 (see NOTICE).*

## [v1.1.5](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.1.5) — 2026-11-05

### What's new

- **Weather on the screensaver**: the photo slideshow now shows the current condition, temperature and today's high/low in the corner opposite the clock. It uses the same weather entity as the Home tab. Set `SCREENSAVER_WEATHER=0` in kiosk.env to hide it.

### Upgrading

Run `scripts/install.sh panelN` from an updated checkout, or unpack the bundle below on the panel and run `sh install-local.sh`. Your settings, token, layout and photos are kept.

---
*Released under the MIT License. From v1.3.0 the project is licensed under Apache-2.0 (see NOTICE).*

## [v1.1.0](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.1.0) — 2026-11-05

### New
- **Music tab works with any Home Assistant media player**: Sonos, WiiM/LinkPlay, Music Assistant, Bluesound, HEOS, Cast and more. Players without a favorites folder show their own library; new source/input picker; grouping only where the player supports it.
- **Installable release bundle**: every release now has `brilliant-ha-kiosk-<version>.tar.gz` with a `.sha256`, so other tools can install a pinned version.
- **On-panel installer** `install-local.sh`: non-interactive and safe to re-run, with `--env`, `--token`, `--layout`, `--no-mqtt`, `--no-restart`, `--uninstall` and `--purge`.
- **`KIOSK_MQTT=0`** turns the kiosk's MQTT publishing off when another tool already publishes the panel to Home Assistant.
- `scripts/uninstall.sh <panel> --purge` removes everything.

### Docs
- Manual: Universal Devices eisy / Polisy / ISY section, release-bundle install guide, `KIOSK_MQTT` setting.

### Upgrading
`git pull`, then `scripts/install.sh <panel>` for each panel. A view with the URL `sonos` keeps working; new layouts use `music`.

---
*Released under the MIT License. From v1.3.0 the project is licensed under Apache-2.0 (see NOTICE).*

## [v1.0.0](https://github.com/MikeI-Code/brilliant-ha-kiosk/releases/tag/v1.0.0) — 2026-11-04

First public release: a fast, native Home Assistant interface for Brilliant Control panels that keeps the panel working as a light switch.

### Highlights
- Native Qt Quick UI talking to Home Assistant over its websocket API: Home, Lights, Climate, Music and House tabs; swipe or tap between them
- Edit the screens in Home Assistant's own dashboard editor; panels update within a second of saving
- Music tab works with any Home Assistant media player (Sonos, WiiM/LinkPlay, Music Assistant, Bluesound, HEOS, Cast, ...): favorites or library tiles, source picker, grouping
- Local control of the wired light over Brilliant's message bus, even when Home Assistant is down
- Multi-touch gestures: two fingers dim or toggle the room light, three fingers set the room's speaker volume, plus custom gestures
- Photo screensaver from your own folder or Home Assistant's media library, night mode, motion wake
- Smart dimming from an HA light sensor, or the sun and the room's lights
- Optional MQTT discovery: each panel appears in Home Assistant as a device with its light, power, motion, display controls and gesture events
- CPU governor and swap tuning for the i.MX6

### Install
Turn on Root SSH Login on the panel, then follow the Quick start in the README or the full [Manual](docs/MANUAL.md). `scripts/uninstall.sh` restores the stock Brilliant interface.

Tested on Brilliant Control (Brilliant OS v26.10.21.1) with Home Assistant 2026.10.

---
*Released under the MIT License. From v1.3.0 the project is licensed under Apache-2.0 (see NOTICE).*
