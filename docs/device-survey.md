# Brilliant panel — device survey (2026-11-03)

*A snapshot from the first look at the panel, kept for reference. Current details are in
[hardware-and-services.md](hardware-and-services.md) and
[resource-utilization-and-optimizations.md](resource-utilization-and-optimizations.md); where
this page and those disagree, those are right. Corrections made on 2026-11-09 are marked in the
text and listed under "Status of these findings" at the end.*

## Hardware
| | |
|---|---|
| SoC | NXP i.MX6 DualLite (2 × Cortex-A9; first written "Quad/DualLite", corrected: later measurements show two cores), ARMv7, 1 GB RAM, 7.3 GB eMMC |
| Display | 720×1280 portrait, `/dev/fb0`, Vivante GC880 GPU (`/dev/galcore`, `/dev/dri/card0`) |
| Touch | Synaptics DSX capacitive (`synaptics_dsx`) |
| Camera | OmniVision OV9732 MIPI (`/dev/video0`), RTSP served by switch-ui on :8554 (*unverified*: read from the binary and the firewall rules; nothing listens on 8554 once `switch_ui_app` is stopped) |
| Audio | simple-card playback + capture, ALSA loopback |
| Radio | Broadcom Wi-Fi (`brcmfmac`) + BT (`hciattach` on `ttymxc2`) |
| Switch hardware | Faceplate (sensors; corrected: no slider on these panels, see hardware-and-services.md section 2) and gangbox (relay/dimmer) over UART, own firmware |

## OS
- Brilliant Embedded Linux 4.0.11 — Yocto kirkstone / Qt Boot2Qt, kernel 5.15.71.
- **OSTree** A/B images, GPG-signed by Brilliant. `update_manager` polls the
  `linux/brilliant-imx6/brilliant-embedded-image/stable` branch every 5 min.
- `/usr` read-only; `/etc` 3-way-merged on update; `/var` persistent.

| Partition | Mount | Use |
|---|---|---|
| mmcblk0p1 64M | `/boot` | kernel / DT / bootloader env |
| mmcblk0p2 4M | `/var/device_variables` (ro) | device ID, PKI, HomeKit token, SSH keys |
| mmcblk0p3 1G | — | unmounted (recovery/factory?) |
| mmcblk0p4 6.2G | `/` | OSTree sysroot |

Base services: systemd, connman + wpa_supplicant, bluetoothd, journald (volatile),
nftables firewall (input policy drop; allows 22, 5000-5010, 5455-5456, 6455, 8554, ≥32768).

## Application layer
- `/data/switch-ui/switch-ui` — Qt/QML + GStreamer/WebRTC native touchscreen app
  (`switch_ui_app.service`, `Type=notify`, watchdog 60 s, reboots after 6 failures in 2 min).
- `/data/switch-embedded` — Python 3.10 (Cython-compiled), run by a uWSGI **emperor**
  (`message_bus.service`) with one vassal per `/var/run/brilliant/processes/*.ini`.

| Group | Vassals |
|---|---|
| Core | message_bus (:5455, :5456 provisioning, :55441), execution, object_store (:6455/:7455), bootstrap, discovery, monitor, analytics, zygote |
| Panel HW | hardware, faceplate_peripheral, gangbox_peripherals, wifi, voice (Alexa + Pryon wake word) |
| Integrations (running) | homekit, sonos, schlage, shelly, somfy, hunter_douglas, bluesound, qolsys, art, control_notification |
| Integrations (shipped) | hue, lifx, nest, ecobee, ring, august, smartthings, tplink, wemo, honeywell, genie, enphase, butterflymx, remotelock |

Which integration vassals run depends on what the owner linked in the Brilliant app (Ring was
linked later on one panel). See resource-utilization-and-optimizations.md section 5 before
disabling any of them.

- Config: gflags flagfiles `configs/embedded-flags-base` + `/var/brilliant/device-flags`.
- State: `/var/brilliant`. Keys: SoftHSM (PKCS#11).
- Peers: TLS on :1443 to other Brilliant devices on the LAN; one AWS cloud connection.

## Kiosk-relevant findings
- Qt WebEngine 5.15.7 present: `/usr/lib/libQt5WebEngine*`, `/usr/lib/libexec/QtWebEngineProcess`,
  QML module `QtWebEngine`. `qml` and `qmlscene` runtimes in `/usr/bin`.
- QPA: `eglfs` (+ `eglfs_viv`), `linuxfb`, `vnc`. No Qt Virtual Keyboard.
- Test: `qml kiosk.qml -- https://demo.home-assistant.io/` loaded OK; ~260 MB RAM free
  with switch-ui stopped. Log warns `QtWebEngine::initialize()` not called (harmless so far).

What followed: the web mode (`KIOSK_MODE=web`, `kiosk/kiosk.qml`) proved too slow on this CPU and
the native Qt Quick UI (`native/main.qml`) became the default; the message bus turned out to be
usable from Brilliant's own Python, which gave local load control and the device id; and the
faceplate's light sensor turned out to be unusable (hardware-and-services.md section 2).

## Status of these findings (added 2026-11-09)

The survey was taken 2026-11-03 on the first panel, before any kiosk code ran. Each line below is
**verified** (exercised on the panel), *inferred* (read from configs, binaries or unit files, not
exercised) or *corrected* (the 2026-11-03 note was wrong and the correction is given).

| Finding | Status |
|---|---|
| Two Cortex-A9 cores (DualLite), not Quad | *corrected* |
| Display, touch, camera, audio cards, Wi-Fi and Bluetooth drivers, base services | verified |
| Camera RTSP on :8554 served by switch-ui | *inferred*; nothing listens on 8554 once `switch_ui_app` is stopped (2026-11-04) |
| Faceplate slider | *corrected*: sensors only, no slider |
| Distribution, kernel, partition table, Python services, Qt WebEngine and QPA plugins, the `qml` test | verified |
| OSTree update behaviour, `/etc` three-way merge, firewall ruleset, `/var/device_variables` contents, flagfiles, SoftHSM, peers on :1443 | *inferred* (OSTree defaults and configs; not yet observed across an update, see INTEGRATION.md section 11); the kiosk adds runtime firewall rules for its voice and capture ports |
