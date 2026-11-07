# Resource Utilization & Optimization Analysis

Hardware headroom, background services and media capabilities of the Brilliant Control
(i.MX6 DualLite, 2× Cortex-A9 at up to 996 MHz, 1 GB RAM, Vivante GC880 GPU). First written
2026-11-04; **updated 2026-11-07 for v1.4.0**, with every figure re-measured on panel 1 while
the kiosk was running all of its optional services (voice, intercom, capture).

---

## 1. Summary

* **Memory:** about **260 MB available** of 997 MB, with 13 MB of the 256 MB swap file in use.
  Measured as PSS (shared pages counted once):

  | | PSS |
  |---|---|
  | Brilliant's services (26 uWSGI processes) | ~392 MB |
  | This kiosk, all services | ~126 MB |
  | `ha-kiosk` (the Qt UI) | 77 MB |
  | `ha-kiosk-bridge` | 18 MB |
  | `ha-kiosk-capture`, `-intercom`, `-voice` | 9–10 MB each |

* **CPU:** load average about 0.5 with the screen awake (governor `performance`), 56 °C. The
  kiosk itself sits near 0–15% except while scrolling or animating.
* **Live video** (new in v1.4.0) uses the hardware H.264 decoder: about 10% CPU for decoding and
  about 33% for the whole UI while a 720p stream plays, leaving about 60% of the CPU free (section 3).
* The camera, microphones and speaker, unused in the first version of this doc, now back optional
  features: Capture, the Assist voice satellite and the intercom.

---

## 2. Hardware

| Hardware | Node | Used by the kiosk | Notes |
|---|---|---|---|
| Camera | OV9732, `/dev/video0` | Capture tab: snapshots and video clips (`CAPTURE_VIDEO=1`) | Lights the red privacy LED while on, so it is never used for light metering. `ALLOW_CAMERA=0` locks it |
| Microphones | ALSA card 2, shared through `dsnoop` | Assist voice satellite (Wyoming, `KIOSK_VOICE=1`), intercom (`INTERCOM_MIC=1`), Capture | Shared with Brilliant's Alexa `voice` service, which keeps running. `ALLOW_MIC=0` locks them |
| Speaker | ALSA card 1, `dmix_filtered` | Assist replies, intercom, chimes | Shared with Brilliant |
| Video decoder | i.MX6 VPU (CODA960), `/dev/mxc_vpu` | Live camera video | **H.264 only.** No H.265/HEVC in hardware, in GStreamer or in the panel's `ffmpeg` (section 3) |
| Light sensor | Faceplate `lux` | No | Reads **0** even when enabled over the bus; smart dimming uses HA or the sun instead |
| Motion sensor | Faceplate PIR | Motion wake (`MOTION_WAKE`); published to HA over MQTT as `binary_sensor` | Read indirectly, through Brilliant's screen-on decisions |
| Slider | `brilliant_captouch`, faceplate UART | No, owned by `faceplate_peripheral` | Do **not** open `/dev/ttymxc0` from QML: a second reader on the UART conflicts with Brilliant |

---

## 3. Video

What the panel can play, from tests on panel 1 (2026-11-07) and from the stock UI binary
(`/data/switch-ui/switch-ui`), which played Ring and Nest video with the same parts:

| Path | Result |
|---|---|
| H.264 RTSP → `rtph264depay ! h264parse ! imxvpudec_h264` → Qt `VideoOutput` | **Works.** 1280×720 at the source's 15 fps, ~4 s to first frame, ~10% CPU to decode |
| H.265 in hardware | Not available. The CODA960 VPU has no HEVC decoder |
| H.265 in software (FFmpeg 7.0.2, ARM NEON build, both cores) | **8 fps** for a 2560×1440 stream, 0.55× real time, at 100% CPU. Not usable |
| Brilliant's stock UI | H.264 only (`rtspsrc`/`webrtcbin` → `imxvpudec_h264` → `qmlglsink`), one stream at a time |

So H.265 cameras need an H.264 stream from Home Assistant. CuboAI's per-camera **H.264
transcode** at 720p took about **60% of a Raspberry Pi 4** for one camera, only while it is being
watched. The kiosk plays one camera at a time.

Two details that matter in QML: `MediaPlayer.hasVideo` stays `false` for a `gst-pipeline:` source
(the kiosk waits for `VideoOutput.sourceRect` instead), and `qtvideosink sync=false` avoids
dropping late frames on a live stream.

---

## 4. System Tuning

### A. Memory: swap ✅ done
* The kernel has **no zram** (`CONFIG_ZRAM`/`ZSMALLOC` not built).
* `ha-kiosk-swap.service` adds a 256 MB swap file (`/var/lib/ha-kiosk-swap/swapfile`) with
  `vm.swappiness=10`, so it only absorbs spikes (web mode, big photos) and spares the eMMC.
  `SWAP_MB` in `kiosk.env` (0 disables).

### B. CPU governor ✅ done
* `ha-kiosk-display.sh` uses `performance` while the screen is awake, `schedutil` for the
  screensaver/dim and `powersave` at night (`CPU_*_GOV`). Steps: 396 / 792 / 996 MHz. CPU runs
  48–57 °C, far below the 95 °C trip point.

### C. GPU budget
* The GC880 is the real limit for the UI: no translucent overlays over scrolling content, and the
  original slider feel was kept. Video overlays (title bar, LIVE badge) are solid for the same reason.

### D. Framebuffer buffering (unverified)
* The framebuffer has room for three buffers (720×3840). Nothing in the kiosk sets
  `FB_MULTI_BUFFER`. Worth an A/B test (`Environment=FB_MULTI_BUFFER=3`) if tearing is visible.

---

## 5. Brilliant Background Services

26 uWSGI processes run under the emperor. The largest (PSS):

| Service | PSS | Notes |
|---|---|---|
| `message_bus` | 41 MB | Essential |
| `hardware` | 29 MB | Essential |
| `execution` | 20 MB | Essential |
| `wifi` | 19 MB | Essential |
| `analytics` | 18 MB | Brilliant telemetry |
| `discovery_peripheral`, `qolsys_peripherals`, `voice` (Alexa), `object_store_peripheral`, `gangbox_peripherals` | 15–17 MB each | `gangbox_peripherals` is essential |
| `wemo`, `homekit`, `somfy`, `hue_bridge`, `lifx`, `hunter_douglas`, `shelly`, `bluesound` and others | 11–14 MB each | |

**Essential** (do not touch): `message_bus`, `hardware`, `faceplate_peripheral`,
`gangbox_peripherals`, `execution`, `config_peripherals`, `wifi`, `homekit` (if you use HomeKit).

**Careful with "redundant"**: HA also talks to Sonos/Shelly/Hue, but the *physical* Brilliant slider
and buttons can be bound to those devices through Brilliant's own integrations. Disabling an
integration service can silently break a wall control. Disable one at a time and test the switch.
With ~260 MB available there is currently no memory pressure that needs trimming.

**Where to change it**: `/data/switch-embedded` is part of the OSTree OS image and is replaced by
updates. Make changes with systemd drop-ins or files in `/etc` (as the display drop-in does), not by
editing `/data`.

---

## 6. Roadmap

1. ✅ **Display**: Brilliant screen-off fenced off, motion wake, photo screensaver, CPU governor, swap.
2. ✅ **Smart dimming**: from an HA light sensor (`LIGHT_SENSOR`), or estimated from the sun and the
   room's lights (`ROOM_LIGHTS`).
3. ✅ **Voice**: Assist voice satellite over Wyoming, sharing the microphones with Brilliant.
4. ✅ **Presence in HA**: faceplate motion and the wired load published over MQTT.
5. ✅ **Message-bus client**: `tools/brilliant_bus.py`; the wired load dims locally in ~0.1 s.
6. ✅ **Live camera video** on the H.264 hardware decoder, with pinch, pan and reset (v1.4.0).
7. **Service trimming**: only if memory gets tight, one service at a time, testing the wall switch
   after each.
8. **Two live streams at once** (e.g. picture-in-picture): the VPU could likely decode two 720p
   streams, but Brilliant's own UI never did, and the transcode load on the HA host doubles. Untested.
