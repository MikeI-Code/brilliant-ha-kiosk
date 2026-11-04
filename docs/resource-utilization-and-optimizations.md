# Resource Utilization & Optimization Analysis (2026-11-04)

Hardware headroom, background services and untapped features on the Brilliant Control
(i.MX6 DualLite, 2× Cortex-A9, 1 GB RAM), measured on panel 1. Revised the same day after
checking each claim on the device; what was done about each item is noted inline.

---

## 1. Summary

* The native Qt UI (`ha-kiosk.service`) runs on the GC880 GPU alongside Brilliant's uWSGI
  services (`message_bus.service`). About 340 MB of RAM is available.
* Brilliant's services use **~432 MB** in total (PSS: shared pages counted once). The ten
  non-core integration/Alexa/analytics services account for **~156 MB** of that, not the
  200–300 MB that summing per-process RSS suggests (RSS counts the shared zygote pages ~25 times).
* The camera, microphones, speaker and faceplate light sensor are unused by the kiosk.

---

## 2. Unused Hardware

| Hardware | Node | State | Possible use | Caveats |
|---|---|---|---|---|
| Camera | OV9732, `/dev/video0` | Powered off (`camera_on = 0`, read-only); capture times out | A feed in HA | Turning it on lights the red privacy LED, so it's not used for light metering |
| Microphones | ALSA card 2 (`hw:2,0`), `dsnoop_48000` | Held by the Alexa `voice` service | HA Assist voice satellite (Wyoming) | Needs `voice.ini` stopped first |
| Speaker | ALSA card 1 (`hw:1,0`) | Alexa chimes only | TTS, chimes, Assist replies | |
| Light sensor | Faceplate `lux` | Reads **0** even when enabled over the bus | None: smart dimming uses HA instead | Hardware appears not to have a working sensor |
| Motion sensor | Faceplate PIR | Used by Brilliant; **now also wakes the kiosk** (`MOTION_WAKE`) | Presence sensor in HA | Read indirectly, via Brilliant's screen-on decisions |
| Slider | `brilliant_captouch`, faceplate UART | Owned by `faceplate_peripheral` | Dimming from the slider | Do **not** open `/dev/ttymxc0` from QML: a second reader on the UART conflicts with Brilliant |

---

## 3. System Tuning

### A. Memory: swap ✅ done
* No swap was configured. The kernel has **no zram** (`CONFIG_ZRAM`/`ZSMALLOC` not built), so zram
  is not an option.
* Done: `ha-kiosk-swap.service` adds a 256 MB swap file (`/var/lib/ha-kiosk-swap/swapfile`) with
  `vm.swappiness=10`, so it only absorbs spikes (web mode, big photos) and spares the eMMC.
  `SWAP_MB` in `kiosk.env` (0 disables).

### B. CPU governor ✅ done
* `schedutil` on both cores (396 / 792 / 996 MHz steps).
* Done: `ha-kiosk-display.sh` switches to `performance` while the screen is awake, `schedutil`
  for the screensaver/dim, and `powersave` at night (`CPU_*_GOV` in `kiosk.env`). CPU was 48–57 °C,
  far below the 95 °C trip point.

### C. Framebuffer buffering (unverified)
* The framebuffer has room for three buffers (720×3840). Nothing in the kiosk sets
  `FB_MULTI_BUFFER`, and the previous version of this doc's claim that it is currently `2` was not
  confirmed. Worth an A/B test (`Environment=FB_MULTI_BUFFER=3`) if tearing is visible.

---

## 4. Brilliant Background Services

27 uWSGI processes run under the emperor. Approximate PSS:

| Service | PSS | Notes |
|---|---|---|
| `voice` (Alexa, Pryon wake word) | 21 MB | Holds the microphones |
| `analytics` | 17 MB | Brilliant telemetry |
| `sonos_peripherals` | 18 MB | |
| `qolsys_peripherals` | 18 MB | |
| `schlage_peripherals` | 15 MB | |
| `shelly`, `somfy`, `hue_bridge`, `bluesound`, `hunter_douglas` | 13–14 MB each | |

**Essential** (do not touch): `message_bus`, `hardware`, `faceplate_peripheral`,
`gangbox_peripherals`, `execution`, `config_peripherals`, `wifi`, `homekit` (if you use HomeKit).

**Careful with "redundant"**: HA also talks to Sonos/Shelly/Hue, but the *physical* Brilliant slider
and buttons can be bound to those devices through Brilliant's own integrations. Disabling an
integration service can silently break a wall control. Disable one at a time and test the switch.

**Where to change it**: `/data/switch-embedded` is part of the OSTree OS image and is replaced by
updates. Make changes with systemd drop-ins or files in `/etc` (as the display drop-in does), not by
editing `/data`.

---

## 5. Roadmap

1. ✅ **Display**: Brilliant screen-off fenced off, motion wake, photo screensaver, CPU governor, swap.
2. ✅ **Smart dimming**: from an HA light sensor in the room (`LIGHT_SENSOR`) if there is one,
   otherwise estimated from the sun and the room's lights (`ROOM_LIGHTS`). See README.
3. **Voice**: stop `voice.ini`, run a Wyoming satellite on `hw:2,0` / `hw:1,0` for HA Assist.
4. **Presence in HA**: publish motion (from the shadow files) to HA as a `binary_sensor` per panel.
5. **Service trimming**: one service at a time, with the wall switch tested after each.
6. ✅ **Message-bus client**: `tools/brilliant_bus.py` (protocol in hardware doc section 6). Next:
   publish faceplate motion and the wall load to HA, and enable `enable_pir_motion_score` for
   direct motion.
