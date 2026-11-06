# Brilliant → Home Assistant Panel

**Turn a Brilliant smart-home control into a fast, dark, native Home Assistant wall panel, while
it keeps working as a light switch.**

<p align="center">
  <img src="docs/images/home.png" width="19%" alt="Home tab: clock, weather, forecast, scenes and lights">
  <img src="docs/images/lights.png" width="19%" alt="Lights tab">
  <img src="docs/images/climate.png" width="19%" alt="Climate tab with thermostats">
  <img src="docs/images/sonos.png" width="19%" alt="Sonos tab with transport, volume and grouping">
  <img src="docs/images/house.png" width="19%" alt="House tab with presence, appliances and temperatures">
</p>

Brilliant panels are great hardware: a 720×1280 touchscreen, a dimmer, Wi-Fi, a speaker and
microphones in a light-switch box. This project replaces the stock touchscreen app with a
**native Qt Quick interface that talks to Home Assistant directly**. It keeps Brilliant's own
background services, so the wired light, HomeKit and over-the-air updates keep working.

Nothing is hacked or jail-broken. SSH access is a built-in feature of the panel's software that
you switch on, and everything this project installs lives in `/etc` and `/var`, which survive
Brilliant's OS updates. One script undoes it all.

---

## Why build on Brilliant hardware

There are cheaper ways to put a touchscreen on a wall: an ESP32 display, an old tablet. This
project deliberately builds on Brilliant panels, and I think the hardware and the team behind it
are worth supporting. *I'm not an employee of Brilliant or otherwise tied to the company; this is
a user's view.*

**What a Brilliant panel brings that a generic ESP32 panel can't:**

* **Integrations Home Assistant doesn't have.** Brilliant's own software links to Ring (doorbells,
  cameras and the **Ring Alarm**) and Schlage locks through each company's account linking, and
  keeps those links working. Home Assistant's Ring integration has no Ring Alarm support (at the
  time of writing); on a Brilliant panel this project shows the alarm and arms or disarms it, by
  using Brilliant's integration, which you link in the Brilliant app. Schlage locks work the same
  way, through Brilliant's own link.
* **An engineering team and back-end services.** Account linking, token refresh, cloud services
  and firmware updates are maintained by people whose job it is, not by you on a weekend.
* **Hardware made for the wall box.** A line-voltage dimmer that reports power and temperature, a
  microphone array, a speaker, a camera and motion sensing, in the space of a light switch.
* **Support for real devices.** When a vendor changes an API or a login flow, it's Brilliant's
  team that adapts. This project keeps using their services instead of re-implementing them.

A generic ESP32 panel is a great way to tinker, but you become the integration team, the back end
and the support desk. If this project is useful to you, support Brilliant: buy their panels and
plugs, keep your Brilliant account and integrations linked, and leave their services running.
That's why this project only replaces the screen and never removes Brilliant's background services.

---

## Features

| | |
|---|---|
| ⚡ **Native and fast** | Qt Quick on the GPU, talking to HA's websocket API. Instant taps, smooth scrolling and swiping, about 350 MB of RAM to spare. |
| 🏠 **Five tabs out of the box** | Home (clock, weather, 5-day forecast, scenes, key lights, thermostats, now playing), Lights, Climate, Sonos, House. Swipe or tap to switch. |
| ✏️ **Edit screens in Home Assistant** | Lay out the panel in HA's own drag-and-drop dashboard editor. Every panel rebuilds within a second of pressing **Save**. |
| 💡 **Local control of the wired light** | The panel dims its own load directly over Brilliant's message bus, in about 0.1 s, even if Home Assistant is down. |
| ✌️ **Multi-touch gestures** | Two fingers up/down dim the room light, a two-finger tap toggles it, three fingers up/down set the room's Sonos volume, plus your own custom gestures. |
| 🎵 **A real Sonos app** | Room picker, album art, transport, volume, grouping, and your Sonos Favorites (SiriusXM, Spotify, Apple Music, radio) as tap-to-play tiles. |
| 🌙 **Night mode, screensaver, motion wake** | Screen off overnight, a photo slideshow with a clock when idle (your photos from a folder or uploaded in Home Assistant), and it wakes when you walk up. |
| 📡 **Shows up in Home Assistant** | Optional MQTT discovery: each panel becomes an HA device with its wired light, power, motion, display state, screensaver/night switches, and gestures as automation triggers. |
| 🔆 **Smart dimming** | Brightness follows the room: from an HA light sensor if you have one, otherwise from the sun and the room's lights. |
| 🔔 **Doorbell pop-up** | Ring doorbells (through Brilliant's own Ring link) wake the panel and show that door's camera full-screen. Camera cards from HA dashboards work as tiles too. |
| 🛡️ **Ring Alarm and Schlage locks** | Alarm status on the Home screen, arm / disarm with your code, and lock / unlock (two taps to unlock), through Brilliant's integrations. Home Assistant gets read-only status. If a Brilliant update changes something, only the affected control switches off, with the reason on the panel and in HA. ([How this works](docs/MANUAL.md#how-the-alarm-and-lock-control-works)) |
| 🔒 **Private by design** | Your HA token, passwords and layout live in a git-ignored `.private/` folder. The local bridge listens on localhost only. |

<p align="center">
  <img src="docs/images/dim.png" width="30%" alt="Two-finger dimming with a live percentage readout">
  &nbsp;&nbsp;
  <img src="docs/images/saver.jpg" width="30%" alt="Photo screensaver with clock">
</p>
<p align="center"><sub>Left: two-finger dimming with a live readout. Right: the photo screensaver.</sub></p>

---

## Quick start

> Full step-by-step instructions, every setting, and troubleshooting are in the
> **[Manual](docs/MANUAL.md)**.

**You need:** a Brilliant Control on your Wi-Fi, a Home Assistant install on the same network, and
a computer with `bash` (Linux, macOS, or Git Bash on Windows) plus `ssh` or PuTTY's `plink`.

1. **Turn on SSH on the panel.** Brilliant's software has an official **Root SSH Login** setting
   under *Advanced Settings* (see the [manual](docs/MANUAL.md#21-turn-on-ssh-on-the-panel) for its
   caveats). No exploit is needed. Note the panel's IP address and the root password it gives you.
2. **Clone this repo** and create your private config:
   ```bash
   git clone https://github.com/MikeI-Code/brilliant-ha-kiosk.git
   cd brilliant-ha-kiosk
   mkdir -p .private
   cp config/panel.env.example  .private/panel1.env    # panel IP + root password
   cp config/kiosk.env.example  .private/kiosk.env     # HA address, schedule, features
   ```
3. **Create a Home Assistant token** (your profile → *Security* → *Long-lived access tokens*) and
   save it as `.private/token`.
4. **Install:**
   ```bash
   scripts/install.sh panel1
   ```
   The panel restarts its screen into the new interface (about 30 s).
5. **Make it yours:** create the editable *Panel Layout* dashboard in HA, then edit it there:
   ```bash
   HA_URL=http://<ha-ip>:8123 HA_TOKEN=$(cat .private/token) python3 scripts/publish_layout.py native/layout.example.json
   ```

To go back to the stock Brilliant interface at any time: `scripts/uninstall.sh panel1`.

---

## How it works

```mermaid
flowchart LR
  subgraph Panel["Brilliant panel (i.MX6, Linux)"]
    UI["Native UI<br/>Qt Quick (qmlscene)"]
    DISP["ha-kiosk-display<br/>backlight · night · screensaver<br/>motion wake · smart dim · CPU"]
    BR["ha-kiosk-bridge<br/>127.0.0.1:8765"]
    BUS[("Brilliant message bus<br/>(Thrift over a Unix socket)")]
    HW["Brilliant services<br/>dimmer · faceplate · HomeKit · OTA"]
    UI -- "/load" --> BR --> BUS --- HW
    DISP -. "state files" .- UI
  end
  HA["Home Assistant"]
  UI <-- "websocket API<br/>states · services · layout" --> HA
  DISP -- "REST: sun, lights, lux" --> HA
  BR <-- "MQTT discovery<br/>(optional)" --> HA
```

* The stock touchscreen app (`switch_ui_app`) is disabled. Everything else Brilliant runs keeps
  running.
* `ha-kiosk.service` runs the UI (`native/main.qml`) on the GPU. Its layout comes from an HA
  dashboard (or a local `layout.json`) and is cached on the panel for offline boots.
* `ha-kiosk-display` owns the screen: backlight, night schedule, screensaver, motion wake, smart
  dimming and the CPU governor. A small systemd drop-in stops Brilliant's own screen-off logic from
  fighting it.
* `ha-kiosk-bridge` talks to Brilliant's message bus, so the screen can control the wired load
  directly. With MQTT configured, it also publishes the panel to Home Assistant as a device. The bus protocol is documented in
  [docs/hardware-and-services.md](docs/hardware-and-services.md).

---

## Documentation

| | |
|---|---|
| 📘 [**Manual**](docs/MANUAL.md) | Installation, configuration reference, editing screens, gestures, maintenance, troubleshooting |
| 🔧 [Hardware & services](docs/hardware-and-services.md) | Hardware, kernel interfaces, Brilliant's services, and the message-bus API |
| 📊 [Resources & optimizations](docs/resource-utilization-and-optimizations.md) | Memory and CPU measurements, what is safe to change |
| 🔍 [Device survey](docs/device-survey.md) | Original OS and hardware survey |

## Repository layout

```
native/        Native UI (Qt Quick): main.qml, tiles, Sonos page, gestures, HA client
kiosk/         On-panel services: display manager, local bridge, smart dimming, swap, web-mode UI
systemd/       Units and drop-ins installed on the panel
scripts/       install / uninstall / logs, plus publishing layouts to Home Assistant
tools/         Message-bus CLI, touch injector for testing, icon map generator
config/        Example settings (copy into .private/)
docs/          Manual, hardware and service notes, screenshots
```

## Compatibility

Tested on Brilliant Control panels (single-gang, no physical slider) running Brilliant OS
**v26.10.21.1** (Yocto kirkstone, kernel 5.15) with **Home Assistant 2026.10**. Other Brilliant
models and firmware versions may differ; see the manual's troubleshooting section.

## Credits

Created by **Mike Ippolito** ([@MikeI-Code](https://github.com/MikeI-Code)).

Icons: [Material Design Icons](https://pictogrammers.com/library/mdi/) by Pictogrammers.
See also [brilliant-mqtt](https://github.com/joyfulhouse/brilliant-mqtt), a separate community
project that bridges Brilliant panels to Home Assistant over MQTT.

## License

[Apache License 2.0](LICENSE) © 2026 Mike Ippolito. Releases before v1.3.0 were published under the MIT License (see [`NOTICE`](NOTICE)). Third-party components keep their own licenses (see `NOTICE`).

*Brilliant is a trademark of its respective owner. This is an independent community project, not
affiliated with or endorsed by Brilliant or the Home Assistant project. Use at your own risk: it
changes how a mains-wired device behaves, so keep the stock UI one `uninstall.sh` away.*

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/mikeimb)
>
