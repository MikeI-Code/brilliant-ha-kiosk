# Brilliant panel — device survey (2026-11-03)

## Hardware
| | |
|---|---|
| SoC | NXP i.MX6 Quad/DualLite, ARMv7, 1 GB RAM, 7.3 GB eMMC |
| Display | 720×1280 portrait, `/dev/fb0`, Vivante GPU (`/dev/galcore`, `/dev/dri/card0`) |
| Touch | Synaptics DSX capacitive (`synaptics_dsx`) |
| Camera | OmniVision OV9732 MIPI (`/dev/video0`), RTSP served by switch-ui on :8554 |
| Audio | simple-card playback + capture, ALSA loopback |
| Radio | Broadcom Wi-Fi (`brcmfmac`) + BT (`hciattach` on `ttymxc2`) |
| Switch hardware | Faceplate (slider) and gangbox (relay/dimmer) over UART, own firmware |

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

- Config: gflags flagfiles `configs/embedded-flags-base` + `/var/brilliant/device-flags`.
- State: `/var/brilliant`. Keys: SoftHSM (PKCS#11).
- Peers: TLS on :1443 to other Brilliant devices on the LAN; one AWS cloud connection.

## Kiosk-relevant findings
- Qt WebEngine 5.15.7 present: `/usr/lib/libQt5WebEngine*`, `/usr/lib/libexec/QtWebEngineProcess`,
  QML module `QtWebEngine`. `qml` and `qmlscene` runtimes in `/usr/bin`.
- QPA: `eglfs` (+ `eglfs_viv`), `linuxfb`, `vnc`. No Qt Virtual Keyboard.
- Test: `qml kiosk.qml -- https://demo.home-assistant.io/` loaded OK; ~260 MB RAM free
  with switch-ui stopped. Log warns `QtWebEngine::initialize()` not called (harmless so far).
