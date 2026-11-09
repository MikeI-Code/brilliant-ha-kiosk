# Brilliant Panel Hardware & Service Architecture

*First written 2026-11-04; updated 2026-11-07 (video decoding, shared audio, bus client) and
2026-11-09 (bus API checked against `tools/brilliant_bus.py`, `kiosk/ha-kiosk-bridge.py` and
`lib/pybrilliantkiosk/src/pybrilliantkiosk/bus.py`; load power unit corrected).*

Hardware devices, kernel interfaces, daemon architecture and sensors on the Brilliant Control (NXP i.MX6 DualLite), as found on two panels running Brilliant OS v26.10.21.1. Items marked *unverified* were read from code or configs but not exercised.

The original survey is in [device-survey.md](device-survey.md); measurements are in
[resource-utilization-and-optimizations.md](resource-utilization-and-optimizations.md).

---

## 1. Camera Subsystem

| Parameter | Specification |
|---|---|
| **Sensor** | OmniVision OV9732 (720p HD CMOS image sensor, MIPI CSI-2) |
| **Driver** | `ov9732_mipi` attached to `ipu0:/csi0` (`mxc_v4l2_cap0`) |
| **V4L2 Nodes** | Primary capture: `/dev/video0`<br>Overlay / IPU post-proc: `/dev/video16`, `/dev/video17` (`DISP3 BG`) |
| **I2C Control Bus** | `/sys/bus/i2c/devices/1-0036` (I2C Bus 1, address `0x36`) |
| **Direct Access** | Standard Linux V4L2 API (`v4l2src`, `imxv4l2videosrc` in GStreamer, `ffmpeg`, or OpenCV). Stock `switch-ui` links `gst_rtsp_server`, so it likely served the camera over RTSP (*unverified*; nothing listens on 8554 now). `hardware_peripheral` reports `camera_on = 0` while idle. The kiosk's Capture tab (`CAPTURE_VIDEO=1`) uses it for snapshots and clips; the red privacy LED lights while it is on. |

### 1.1 Video decoding (verified 2026-11-07)

| Path | Status |
|---|---|
| **VPU** (CODA960, `/dev/mxc_vpu`, GStreamer `imxvpu` plugin) | H.264, MPEG-2/4, H.263, VC-1, VP8, JPEG decode; H.264/MPEG-4/JPEG encode. **No H.265/HEVC** |
| **Display paths** | `qmlglsink` (Qt), `imxeglvivsink`, `imxg2dvideosink`, `imxipuvideosink`; Qt Multimedia's GStreamer backend (`gst-pipeline:` sources with `qtvideosink`) |
| **Network sources** | `rtspsrc`, `webrtcbin`, `hlsdemux` |
| **Software decode** | GStreamer has no `avdec_*`; `/usr/bin/ffmpeg` is built with `--disable-decoders` (audio only). A static FFmpeg 7.0.2 (NEON) decoded 2560×1440 H.265 at 8 fps on both cores |
| **Stock `switch-ui`** | `RTSP Receiver`, `WebRTCReceiver` and `SkybellReceiver`: `rtph264depay ! h264parse ! imxvpudec_h264 ! glupload ! qmlglsink`, one pipeline shared behind a semaphore |

The kiosk plays live H.264 RTSP the same way (see `native/Doorbell.qml`): 720p at ~10% CPU.

---

## 2. Motion & Light Sensing

The faceplate has its own sensors, reported over the faceplate UART (`/dev/ttymxc0`, owned by
`faceplate_peripheral`; the Brilliant configs also name `/dev/faceplate_uart`, presumably the
same port, *unverified*). Variables seen in the panel's variable store
(`/var/brilliant/<device>.history/…`, base64 Thrift):

| Variable (`faceplate_peripheral`) | Notes |
|---|---|
| `movement_detected`, `pir_motion_score` | Motion; thresholds `pir_motion_detection_low/high_threshold` |
| `enable_pir_motion_score`, `enable_screen_motion_detection`, `enable_light_motion_detection` | Which detectors are on |
| `lux`, `enable_lux` | Ambient light. `enable_lux` is 0; turning it on (section 6) makes the faceplate report `glux` = **0** even in a lit room, so there is no usable light sensor |
| `coldest/hottest_internal_temperature` | Faceplate temperatures (~34–36 °C). The kiosk publishes the hottest one over MQTT |

`motion_detection_config_peripheral` holds `trigger_screen`, `trigger_screen_off` and
`trigger_screen_off_timeout_sec` (60–600 s seen). The hardware service polls the faceplate
(`gmot`, `gdim`, `gpwr`… commands) and acts on these. The kiosk does not read the PIR
directly: it watches Brilliant's own screen-on decision through the shadow backlight files
(section 3), which is what `MOTION_WAKE` and the MQTT `motion` sensor are based on.

* **Touch**: Synaptics DSX (`dsx-i2c`, I2C bus 0 `0-0020`), events on `/dev/input/event0` (the kiosk reads it for wake-on-touch and writes one sync event to
  wake the screen).
* **No physical slider on these panels**: `brilliant_captouch` (I2C bus 1, `1-000a`) has no driver
  bound ("waiting for supplier") and `hardware_peripheral` reports `num_cap_touch_sliders = 0`.
  Dimming is done on the touchscreen (Synaptics DSX, 720×1280, **10 touch points**).
* **Wired load** (`gangbox_peripheral_0`; more gangboxes appear as `gangbox_peripheral_N`): `on`,
  `intensity` (0–1000; 0 to `max_intensity_value`, 1000 when absent), `power` (**deciwatts**,
  read-only: a 24 W load read 134 at about 60 % and 172 higher up, i.e. 13.4 W and 17.2 W;
  Brilliant pushes changes only above a threshold), `temperature`, `dimmable`,
  `minimum_dim_level`, `maximum_dim_level`, `dimming_edge`, `min_dim_level_calibration_status`
  (0 idle, 1 running, 2 success), `brightness_curve`, `display_name`. Writing `on`/`intensity` over
  the bus switches/dims it immediately (verified); `ha-kiosk-bridge.py` exposes this to the UI and
  MQTT.

---

## 3. Screen Behavior & Power Management

* **Kernel controls**: `/sys/class/backlight/backlight_lcd/brightness` (0–15),
  `bl_power` (0 on, 4 off), `/sys/class/graphics/fb0/blank` (write-only here).
* **Two owners, by design**: Brilliant's `hardware_peripheral` (in `message_bus.service`) still runs
  its own screen logic: `screen_on`, `screen_brightness`, and motion/idle screen-off. Left alone, it
  blanked `fb0` and zeroed the backlight behind the kiosk, so the screen stayed black after a touch.
  A `message_bus` drop-in (`systemd/message_bus-ha-kiosk.conf`) bind-mounts dummy files from
  `/run/ha-kiosk-shadow/` (created by `systemd/ha-kiosk-tmpfiles.conf`) over those three sysfs files **for the Brilliant services only**.
  `ha-kiosk-display.sh` owns the real ones, and reads the dummies as a motion signal: Brilliant
  turning *its* screen on means someone walked up (`MOTION_WAKE`).
* **Rendering**: Qt `eglfs` + `eglfs_viv` on the Vivante GC880, straight to `/dev/fb0`
  (no X11/Wayland). Framebuffer is 720×1280, virtual 720×3840 (three buffers).
* **Display states** (`/run/ha-kiosk-display/state`):
  * `awake`: `DAY_LEVEL` (15), CPU governor `performance`.
  * `photos`: screensaver at `PHOTO_LEVEL` (12) after `IDLE_DIM_SECS` (300 s), CPU `schedutil`.
  * `dim`: `DIM_LEVEL` (4) when `SCREENSAVER=0`.
  * `asleep`: backlight off from `SLEEP_START` to `SLEEP_END` (21:00–06:30), CPU `powersave`;
    a touch wakes it for `NIGHT_WAKE_SECS` (180 s).
  * Motion wakes the screen from `photos`/`dim` (`MOTION_WAKE=day`, or `always` / `off`).
  * What puts the screen in each state, and its CPU governor (`CPU_AWAKE_GOV`, `CPU_IDLE_GOV`,
    `CPU_SLEEP_GOV`; defaults from `ha-kiosk-display.sh`):

    | State | Backlight | CPU governor | When |
    |---|---|---|---|
    | `awake` | `DAY_LEVEL` (15) or the smart-dim level | `CPU_AWAKE_GOV` (`performance`) | Touched within `IDLE_DIM_SECS` (300 s), or `NIGHT_WAKE_SECS` (180 s) at night |
    | `photos` | `PHOTO_LEVEL` (12) or the smart-dim level | `CPU_IDLE_GOV` (`schedutil`) | Idle by day with `SCREENSAVER=1` |
    | `dim` | `DIM_LEVEL` (4) | `CPU_IDLE_GOV` | Idle by day with `SCREENSAVER=0` |
    | `asleep` | 0, `bl_power` 4 | `CPU_SLEEP_GOV` (`powersave`) | `SLEEP_START`–`SLEEP_END` (21:00–06:30) with `NIGHT_MODE=1` |

    The schedule is re-checked about every 30 s while idle.

---

## 4. Microphone & Audio System

* **ALSA Sound Cards** (`/proc/asound/cards`):
  * **Card 1 (`soundplayback`)**: `simple-card - sound-playback` (internal amplifier / speaker).
  * **Card 2 (`soundcapture`)**: `simple-card - sound-capture` (onboard microphone array).
  * **Card 0 (`Loopback`)**: ALSA virtual loopback device.
* **Audio Processing & Voice Stack**:
  * Voice daemon (`voice.ini`) runs in Python via uWSGI vassal with Alexa Voice Service & Pryon Lite wake word engine (`pryon_lite-2.14.0`).
  * DSnoop device: `dsnoop_48000` (48 kHz capture).
  * Acoustic Echo Cancellation (AEC): Input delay `-100ms`, recommended capture latency `0.12s`, playback latency `0.16s`.
* **Shared use** (verified): Brilliant's Alexa `voice` daemon keeps running; the kiosk's voice
  satellite, intercom and Capture open the ALSA `default` capture device (`dsnoop`) and play
  through `dmix_filtered`, alongside it. Opening `hw:2,0` directly would conflict with the daemon.

---

## 5. Core Services & IPC Architecture

Brilliant's daemons talk **Apache Thrift over Unix domain sockets** (and to Brilliant's cloud over a websocket). The schemas (`ttypes.py`) are readable Python; the bus client itself (`bus/message_bus*.so`, `lib/…`) is compiled, so this project wrote its own client, `tools/brilliant_bus.py` (section 6):

* **Socket Hub**: `/var/run/brilliant/server_socket` (message bus server socket).
* **Zygote Fork Server**: `/var/run/brilliant/fork_server.socket` (uWSGI Emperor manages vassals under `/var/run/brilliant/processes/`).
* **Hardware UART Bridges**:
  * Faceplate Slider: `/dev/faceplate_uart` (`faceplate_peripheral.ini`).
  * Gangbox Relay & Dimmer: `/dev/gangbox_uart0`, `/dev/gangbox_uart1` (`gangbox_peripherals.ini`).
* **Data Models & RPC Definitions**:
  * Python Thrift schemas in `/data/switch-embedded/env/lib/python3.10/site-packages/thrift_types/`.
    Besides the modules below there are `protocol` (the bus envelope), `message_bus`
    (`MessageBusService`), `shelly` (BLE readings), `crypto` (`PublicKey`, `EncryptedMessage`, used
    for alarm disarm) and `security_system` (`SystemMode`).
  * Key modules:
    * `hardware/ttypes.py`: Relays, gangbox state, firmware update statuses.
    * `device_metrics/ttypes.py`: Telemetry, uptime, health metrics.
    * `peripheral_interfaces/`: Protocol specifications for lights, motion, and switches.

* **Variable store**: `/var/brilliant/<device-id>.history/<n>/20200923` is a base64 Thrift log of
  every peripheral's variables (`screen_on`, `lux`, `movement_detected`, temperatures, volumes…).
  It is a convenient read-only view of the bus. Treat it as sensitive: it also holds pairing data.
* **Serialisation**: `lib.serialization` (`serialize`/`deserialize`, default
  `SerializationProtocol.BASE64_THRIFT_BINARY`), importable from `/data/switch-embedded` with
  Brilliant's Python `env/bin/python` (verified; all kiosk bus code imports it this way).

`/data/switch-embedded` is part of the OSTree image and is replaced by updates: change behaviour
with systemd drop-ins and files under `/etc` and `/var` (as the display drop-in does), never by
editing `/data`.

---

## 6. Message Bus API (worked out from the Thrift definitions; verified on the panel)

Everything below comes from the readable `thrift_types` definitions and public class signatures,
and was confirmed with live read calls. `tools/brilliant_bus.py` implements it
(CLI: `list`, `get`, `watch`, `set`; class `Bus`), and is used by `kiosk/ha-kiosk-bridge.py` and
`kiosk/ha-kiosk-mqtt.py` on the panel. It is reproduced in `pybrilliantkiosk.bus` (an identity
script run over SSH, and command/parse helpers for the CLI).

**Transport**: Unix socket `/var/run/brilliant/server_socket`. One message per line: base64 of a
Thrift-binary `thrift_types.protocol.ttypes.Message` (Brilliant's `lib.serialization` default,
`SerializationProtocol.BASE64_THRIFT_BINARY`), newline terminated. The clients use a 10 s socket
timeout.

**Message**: `Message(message_type, sequence_number, body_union)`, with `MessageType`
`REQUEST=0`, `RESPONSE=1`, `HELLO=2`, and `body_union` one of `request`, `response`, `hello`. Sequence numbers are per client and increasing.

**Handshake**: the client speaks first with `Hello(name=<your peer name>, aliases=[])`. The bus answers
`Hello(name='message_bus', domain=<this panel's device id>)`. The `domain` is how every kiosk
component learns the device id; the Home Assistant integration identifies a panel by it
(`brilliant_<first 12 characters>`), using only Brilliant's Python and libraries, so it works on a
stock panel without any kiosk file.

**Calls**: `Request(command=<method>, args_serialized=serialize(<method>_args), is_oneway=False)`.
The `Response` has `status` (`StatusResponse`, `StatusCode` 0 = ok) and
`result_serialized` (`<method>_result`, value in `.success`; `status_code` 0 = ok, else `error_msg`). `<method>_args`
and `<method>_result` are the generated classes on `thrift_types.message_bus.MessageBusService`.
Other message types may arrive before the response and are skipped.

`MessageBusService` methods:

| Method | Use |
|---|---|
| `get_all()`, `get_device(device_id)` | Every peripheral and variable |
| `get_peripheral(device_id, peripheral_name)` | One peripheral's variables (`name`, `value` as string, `timestamp`, `externally_settable`) |
| `set_variables_request(device_id, peripheral_name, variables, last_set_timestamps)` | Change variables. **Check-and-set**: pass each variable's current `timestamp`, or it fails with "timestamp stale". Only `externally_settable` variables. |
| `subscribe(subscription_request)` | Change notifications (`SubscriptionRequest(device_id, peripheral_type, peripheral_id_glob)`) |
| `register_peripheral`, `update_peripheral_status`, `set_updated_variables`, `delete_peripheral`, `register_virtual_device`, `set_home_id`, `handle_notification` | Used by peripherals themselves; don't call these |

Besides the panel's own id, the bridge reads `ring_virtual_device`, `schlage` and
`configuration_virtual_device` with `get_device`. `set_variables_request` returns
`modified_variables` (`variable_name`, `variable.value`) and is used for the wired load on this
panel and, cross-device, for Schlage locks and the Ring alarm. The kiosk does not use `get_all()`
or `subscribe` (it polls; `watch` re-reads every second).

**Peripheral types** seen in `peripheral_type` (from the Thrift enum, used by the bridge): 1 lock,
2 doorbell, 59 camera, 89 security system.

**Peripherals on a panel** (24): `hardware_peripheral`, `faceplate_peripheral`,
`gangbox_peripheral_0` (the wired load), `motion_detection_config_peripheral`, `switch_ui`,
`voice_peripheral`, `homekit_peripheral`, `wifi_peripheral`, `art_config_peripheral` (`display_name` holds the
room name, e.g. "Kitchen"), `ble_peripheral` (Shelly BLE readings, `shelly_sensor_readings` as
base64 Thrift `ShellySensorReadings`), and others (`gangbox_peripheral_N` for more gangboxes).
Variables the kiosk reads for MQTT: `hardware_peripheral.cpu_temperature`,
`current_release_tag` (firmware), `homekit_peripheral.paired`, `adk_enabled`.

**Integration ownership.** `configuration_virtual_device` has peripherals such as
`ring_configuration` and `schlage_configuration` whose `owner` variable is the device id of the
panel hosting that integration; the bridge publishes Ring and lock state to MQTT only from that
panel (verified in use).

**Findings from live calls**

* With the stock UI gone, `faceplate_peripheral` has `enable_lux`, `enable_pir_motion_score` and
  `enable_screen_motion_detection` all at 0. The faceplate is still polled for motion (`gmot`).
* Setting `enable_lux=1` works (the faceplate service then polls `glux`), but the faceplate answers
  **0 lux** even with the room lit. These faceplates have no usable light sensor, so it was set back
  to 0. Smart dimming therefore uses HA (see README).
* `hardware_peripheral.camera_on` is read-only. Turning the camera on also lights its red privacy
  LED, so it isn't used for light metering.
* `motion_detection_config_peripheral`: `trigger_screen=1`, `trigger_screen_off=1`,
  `trigger_screen_off_timeout_sec=600`. This is Brilliant's own screen-off logic, now fenced off by
  the display drop-in.
* A malformed client is logged and dropped by the bus ("Exception in triage process"), with no effect
  on the other peripherals.
* The bus restarts (and is unreachable for one to two minutes) whenever the kiosk is switched on
  or off, because `install-local.sh` restarts `message_bus` when the display drop-in changes.

Writes (`set`) change Brilliant's own settings; the same bus drives the wall switch. Use with care.
