# Brilliant panel device survey

*Taken 2026-11-03 on the first panel, before any kiosk code ran; tidied 2026-11-09. Kept as the
record of the first look. Current details are in
[hardware-and-services.md](hardware-and-services.md) and
[resource-utilization-and-optimizations.md](resource-utilization-and-optimizations.md); where
this page and those disagree, those are right.*

Status column: **verified** = exercised on the panel; *inferred* = read from configs, binaries or
unit files and not exercised; *corrected* = the 2026-11-03 note was wrong and the correction is given.

## Hardware

| Item | Finding | Status |
|---|---|---|
| SoC | NXP i.MX6 DualLite, 2 × Cortex-A9 (ARMv7), 1 GB RAM, 7.3 GB eMMC | *corrected*: the survey said "Quad/DualLite"; later measurements show two cores |
| Display | 720×1280 portrait, `/dev/fb0`, Vivante GC880 (`/dev/galcore`, `/dev/dri/card0`) | verified |
| Touch | Synaptics DSX capacitive (`synaptics_dsx`), `/dev/input/event0` | verified |
| Camera | OmniVision OV9732 MIPI, `/dev/video0` | verified |
| Camera RTSP | "served by switch-ui on :8554" | *inferred* from the binary and the firewall rules; nothing listens on 8554 once `switch_ui_app` is stopped (2026-11-04) |
| Audio | `simple-card` playback (card 1) and capture (card 2), ALSA loopback (card 0) | verified |
| Radio | Broadcom Wi-Fi (`brcmfmac`), Bluetooth (`hciattach` on `ttymxc2`) | verified (drivers loaded) |
| Switch hardware | Faceplate (sensors, no slider on these panels) and gangbox (relay/dimmer) over UART, each with its own firmware | verified; "slider" *corrected* in hardware-and-services.md section 2 |

## Operating system

| Item | Finding | Status |
|---|---|---|
| Distribution | Brilliant Embedded Linux 4.0.11, Yocto kirkstone / Qt Boot2Qt, kernel 5.15.71 | verified (`/etc/os-release`, `uname`) |
| Updates | OSTree A/B images, GPG-signed by Brilliant; `update_manager` polls the `linux/brilliant-imx6/brilliant-embedded-image/stable` branch every 5 min | *inferred* from the service and its config |
| Filesystems | `/usr` read-only; `/etc` three-way-merged on update; `/var` persistent | *inferred* (OSTree defaults); not yet observed across an update, see INTEGRATION.md section 11 |
| Firewall | nftables, input policy drop; allows 22, 5000–5010, 5455–5456, 6455, 8554 and ports ≥ 32768 | *inferred* from the ruleset; the kiosk adds runtime rules for its voice and capture ports |
| Base services | systemd, connman + wpa_supplicant, bluetoothd, journald (volatile) | verified |

| Partition | Size | Mount | Use | Status |
|---|---|---|---|---|
| mmcblk0p1 | 64 MB | `/boot` | kernel, device tree, bootloader environment | verified |
| mmcblk0p2 | 4 MB | `/var/device_variables` (ro) | device id, PKI, HomeKit token, SSH keys | verified (mount); contents *inferred* |
| mmcblk0p3 | 1 GB | not mounted | recovery or factory image | *inferred* |
| mmcblk0p4 | 6.2 GB | `/` | OSTree sysroot | verified |

## Application layer

| Item | Finding | Status |
|---|---|---|
| Stock UI | `/data/switch-ui/switch-ui`: Qt/QML + GStreamer/WebRTC native touchscreen app, `switch_ui_app.service` (`Type=notify`, 60 s watchdog, reboots after 6 failures in 2 min) | verified (unit file) |
| Python services | `/data/switch-embedded`, Python 3.10 (Cython-compiled), one uWSGI emperor (`message_bus.service`) with a vassal per `/var/run/brilliant/processes/*.ini` | verified |
| Configuration | gflags flagfiles `configs/embedded-flags-base` + `/var/brilliant/device-flags` | *inferred* |
| State and keys | `/var/brilliant`; SoftHSM (PKCS#11) | *inferred* |
| Peers | TLS on :1443 to other Brilliant devices on the LAN; one AWS cloud connection | *inferred* from sockets and configs |

Vassals seen on 2026-11-03:

| Group | Vassals |
|---|---|
| Core | message_bus (:5455, :5456 provisioning, :55441), execution, object_store (:6455/:7455), bootstrap, discovery, monitor, analytics, zygote |
| Panel hardware | hardware, faceplate_peripheral, gangbox_peripherals, wifi, voice (Alexa + Pryon wake word) |
| Integrations, running | homekit, sonos, schlage, shelly, somfy, hunter_douglas, bluesound, qolsys, art, control_notification |
| Integrations, shipped but idle | hue, lifx, nest, ecobee, ring, august, smartthings, tplink, wemo, honeywell, genie, enphase, butterflymx, remotelock |

Which integration vassals run depends on what the owner linked in the Brilliant app (Ring was
linked later on one panel). See resource-utilization-and-optimizations.md section 5 before
disabling any of them.

## Kiosk-relevant findings (2026-11-03)

| Finding | Status |
|---|---|
| Qt WebEngine 5.15.7 present (`/usr/lib/libQt5WebEngine*`, `/usr/lib/libexec/QtWebEngineProcess`, QML module `QtWebEngine`); `qml` and `qmlscene` in `/usr/bin` | verified |
| QPA plugins: `eglfs` (+ `eglfs_viv`), `linuxfb`, `vnc`; no Qt Virtual Keyboard | verified |
| `qml kiosk.qml -- https://demo.home-assistant.io/` loaded the HA web UI; about 260 MB RAM free with `switch-ui` stopped; a harmless `QtWebEngine::initialize()` warning | verified |

What followed: the web mode (`KIOSK_MODE=web`, `kiosk/kiosk.qml`) proved too slow on this CPU and
the native Qt Quick UI (`native/main.qml`) became the default; the message bus turned out to be
usable from Brilliant's own Python, which gave local load control and the device id; and the
faceplate's light sensor turned out to be unusable (hardware-and-services.md section 2).
