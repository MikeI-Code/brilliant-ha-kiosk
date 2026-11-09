# Brilliant panel hardware and service architecture

*First written 2026-11-04; updated 2026-11-07 (video decoding, shared audio, bus client) and
2026-11-09 (bus API checked against `tools/brilliant_bus.py`, `kiosk/ha-kiosk-bridge.py` and
`lib/pybrilliantkiosk/src/pybrilliantkiosk/bus.py`; load power unit corrected).*

Hardware, kernel interfaces, Brilliant's daemons and the message-bus protocol of the Brilliant
Control (NXP i.MX6 DualLite), as found on two panels running Brilliant OS v26.10.21.1.

Markers: **verified** = exercised on a panel or used by shipped kiosk code; *inferred* = read from
code, configs or binaries but not exercised. The original survey is in
[device-survey.md](device-survey.md); measurements are in
[resource-utilization-and-optimizations.md](resource-utilization-and-optimizations.md).

---

## 1. Camera subsystem

| Parameter | Value | Status |
|---|---|---|
| Sensor | OmniVision OV9732, 720p CMOS, MIPI CSI-2 | verified (driver, V4L2) |
| Driver | `ov9732_mipi` on `ipu0:/csi0` (`mxc_v4l2_cap0`) | verified |
| V4L2 nodes | Capture `/dev/video0`; IPU post-processing `/dev/video16`, `/dev/video17` (`DISP3 BG`) | verified |
| I2C control | `/sys/bus/i2c/devices/1-0036` (bus 1, address `0x36`) | verified |
| Access | Standard V4L2 (`v4l2src`/`imxv4l2videosrc` in GStreamer, `ffmpeg`, OpenCV). The kiosk's Capture service (`CAPTURE_VIDEO=1`) takes snapshots and clips this way | verified (2026-11-07) |
| Privacy LED | The red LED lights whenever the camera is on, so it is never used for light metering | verified |
| Stock RTSP | `switch-ui` links `gst_rtsp_server`, so it probably served the camera over RTSP; nothing listens on 8554 with the kiosk running | *inferred* |
| `hardware_peripheral.camera_on` | `0` while idle; read-only on the bus | verified |

### 1.1 Video decoding (verified 2026-11-07)

| Path | Result |
|---|---|
| VPU (CODA960, `/dev/mxc_vpu`, GStreamer `imxvpu`) | Decodes H.264, MPEG-2/4, H.263, VC-1, VP8, JPEG; encodes H.264, MPEG-4, JPEG. **No H.265/HEVC** |
| Display sinks | `qmlglsink` (Qt), `imxeglvivsink`, `imxg2dvideosink`, `imxipuvideosink`; Qt Multimedia's GStreamer backend (`gst-pipeline:` sources with `qtvideosink`) |
| Network sources | `rtspsrc`, `webrtcbin`, `hlsdemux` |
| Software decode | GStreamer has no `avdec_*`; `/usr/bin/ffmpeg` is built with `--disable-decoders` (audio only). A static FFmpeg 7.0.2 (NEON) decoded 2560×1440 H.265 at 8 fps on both cores |
| Stock `switch-ui` | `RTSP Receiver`, `WebRTCReceiver`, `SkybellReceiver`: `rtph264depay ! h264parse ! imxvpudec_h264 ! glupload ! qmlglsink`, one pipeline behind a semaphore (*inferred* from the binary) |

The kiosk plays live H.264 RTSP the same way (`native/Doorbell.qml`): 720p at about 10 % CPU.

---

## 2. Motion and light sensing

The faceplate has its own sensors, reported over the faceplate UART (`/dev/ttymxc0`; the
Brilliant configs also name `/dev/faceplate_uart`, presumably the same port, *inferred*), owned by
`faceplate_peripheral`. Variables of that peripheral, read over the bus (verified):

| Variable | Notes |
|---|---|
| `movement_detected`, `pir_motion_score` | Motion; thresholds `pir_motion_detection_low/high_threshold` |
| `enable_pir_motion_score`, `enable_screen_motion_detection`, `enable_light_motion_detection` | Which detectors are on |
| `lux`, `enable_lux` | `enable_lux` is 0. Setting it to 1 makes the faceplate poll `glux`, which answers **0** in a lit room: no usable light sensor (verified 2026-11-04, set back to 0) |
| `coldest_internal_temperature`, `hottest_internal_temperature` | Faceplate temperatures (34–36 °C seen). The kiosk publishes the hottest one over MQTT |

`motion_detection_config_peripheral` holds `trigger_screen`, `trigger_screen_off` and
`trigger_screen_off_timeout_sec` (60–600 s seen; 600 on the surveyed panel). Brilliant's hardware
service polls the faceplate (`gmot`, `gdim`, `gpwr`, ... commands, *inferred* from the binaries)
and acts on these. The kiosk does not read the PIR directly: it watches Brilliant's own
screen-on decision through the shadow backlight files (section 3), which is what `MOTION_WAKE` and
the MQTT `motion` sensor are based on.

* **Touch**: Synaptics DSX (`dsx-i2c`, I2C bus 0 `0-0020`), 720×1280, 10 touch points, events on
  `/dev/input/event0` (verified; the kiosk reads it for wake-on-touch and writes one sync event to
  wake the screen).
* **No physical slider on these panels**: `brilliant_captouch` (I2C bus 1 `1-000a`) has no driver
  bound ("waiting for supplier") and `hardware_peripheral.num_cap_touch_sliders = 0` (verified).
* **Wired load** (`gangbox_peripheral_0`, more gangboxes as `gangbox_peripheral_N`): `on` (`0`/`1`),
  `intensity` (0 to `max_intensity_value`, 1000 when absent), `power` (**deciwatts**, read-only:
  a 24 W load read 134 at about 60 % and 172 higher up, 13.4 W and 17.2 W; Brilliant pushes
  changes only above a threshold), `temperature`, `dimmable`, `minimum_dim_level`,
  `maximum_dim_level`, `dimming_edge`, `min_dim_level_calibration_status` (0 idle, 1 running,
  2 success), `brightness_curve`, `display_name`. Writing `on`/`intensity` over the bus switches
  or dims it immediately (verified; `ha-kiosk-bridge.py` does this for the UI and MQTT).

---

## 3. Screen behaviour and power management

* **Kernel controls**: `/sys/class/backlight/backlight_lcd/brightness` (0–15), `bl_power` (0 on,
  4 off), `/sys/class/graphics/fb0/blank` (write-only here). Verified.
* **Two owners, by design**: Brilliant's `hardware_peripheral` (in `message_bus.service`) runs its
  own screen logic (`screen_on`, `screen_brightness`, motion/idle screen-off). Left alone, it
  blanked `fb0` and zeroed the backlight behind the kiosk. The drop-in
  `systemd/message_bus-ha-kiosk.conf` bind-mounts dummy files from `/run/ha-kiosk-shadow/`
  (created by `systemd/ha-kiosk-tmpfiles.conf`) over the three sysfs files **for the Brilliant
  services only**. `kiosk/ha-kiosk-display.sh` owns the real files and reads the dummies as the
  motion signal: Brilliant turning *its* screen from off to on means someone walked up. Verified.
* **Rendering**: Qt `eglfs` + `eglfs_viv` on the Vivante GC880, straight to `/dev/fb0` (no X11 or
  Wayland). Framebuffer 720×1280, virtual 720×3840 (three buffers). Verified.
* **Display states** (`/run/ha-kiosk-display/state`, written by `ha-kiosk-display.sh`; defaults
  from the script):

  | State | Backlight | CPU governor | When |
  |---|---|---|---|
  | `awake` | `DAY_LEVEL` (15) or the smart-dim level | `CPU_AWAKE_GOV` (`performance`) | Touched within `IDLE_DIM_SECS` (300 s), or `NIGHT_WAKE_SECS` (180 s) at night |
  | `photos` | `PHOTO_LEVEL` (12) or the smart-dim level | `CPU_IDLE_GOV` (`schedutil`) | Idle by day with `SCREENSAVER=1` |
  | `dim` | `DIM_LEVEL` (4) | `CPU_IDLE_GOV` | Idle by day with `SCREENSAVER=0` |
  | `asleep` | 0, `bl_power` 4 | `CPU_SLEEP_GOV` (`powersave`) | `SLEEP_START`–`SLEEP_END` (21:00–06:30) with `NIGHT_MODE=1` |

  Motion wakes the screen from `photos`/`dim` when `MOTION_WAKE` allows it (`day` default, `always`,
  `off`). The schedule is re-checked about every 30 s while idle.

---

## 4. Microphone and audio

* **ALSA cards** (`/proc/asound/cards`, verified): card 0 `Loopback` (virtual), card 1
  `soundplayback` (`simple-card - sound-playback`, internal amplifier and speaker), card 2
  `soundcapture` (`simple-card - sound-capture`, microphone array).
* **Brilliant's voice stack** (*inferred* from `voice.ini` and the installed packages): a Python
  uWSGI vassal with Alexa Voice Service and the Pryon Lite wake-word engine (`pryon_lite-2.14.0`);
  capture through `dsnoop_48000` (48 kHz); AEC with input delay −100 ms, capture latency 0.12 s,
  playback latency 0.16 s.
* **Shared use** (verified 2026-11-07): Brilliant's `voice` daemon keeps running; the kiosk's voice
  satellite, intercom and Capture open the ALSA `default` capture device (`dsnoop`) and play
  through `dmix_filtered` alongside it. Opening `hw:2,0` directly conflicts with the daemon.

---

## 5. Core services and IPC

Brilliant's daemons talk **Apache Thrift over Unix domain sockets** (and to Brilliant's cloud over
a websocket). The Thrift schemas (`thrift_types/*/ttypes.py`) are readable Python; the bus client
(`bus/message_bus*.so`, `lib/…`) is Cython-compiled, which is why this project has its own client
(section 6).

* **Message bus socket**: `/var/run/brilliant/server_socket` (verified).
* **Fork server**: `/var/run/brilliant/fork_server.socket`; a uWSGI emperor (`message_bus.service`)
  runs one vassal per `/var/run/brilliant/processes/*.ini` (*inferred* from the unit and configs).
* **UART bridges** (*inferred* from the `.ini` files): faceplate `/dev/faceplate_uart`
  (`faceplate_peripheral.ini`); gangbox relay and dimmer `/dev/gangbox_uart0`, `/dev/gangbox_uart1`
  (`gangbox_peripherals.ini`).
* **Thrift schemas** in `/data/switch-embedded/env/lib/python3.10/site-packages/thrift_types/`:
  `protocol` (the bus envelope), `message_bus` (`MessageBusService`), `hardware`,
  `device_metrics`, `peripheral_interfaces`, `shelly` (BLE readings), `crypto`
  (`PublicKey`, `EncryptedMessage`, used for alarm disarm), `security_system` (`SystemMode`).
* **Serialisation**: `lib.serialization` (`serialize`/`deserialize`, default
  `SerializationProtocol.BASE64_THRIFT_BINARY`), importable from `/data/switch-embedded` with
  Brilliant's Python `env/bin/python` (verified; all kiosk bus code imports it this way).
* **Variable store**: `/var/brilliant/<device-id>.history/<n>/<date>` is a base64 Thrift log of
  every peripheral's variables. A convenient read-only view of the bus; treat it as sensitive, it
  also holds pairing data (*inferred*).

`/data/switch-embedded` is part of the OSTree image and is replaced by updates: change behaviour
with systemd drop-ins and files under `/etc` and `/var` (as the display drop-in does), never by
editing `/data`.

---

## 6. Message bus API

Worked out from the Thrift definitions and confirmed with live calls (2026-11-04 onwards).
Implemented in `tools/brilliant_bus.py` (CLI: `list`, `get`, `watch`, `set`; class `Bus`), used
by `kiosk/ha-kiosk-bridge.py` and `kiosk/ha-kiosk-mqtt.py` on the panel, and reproduced in
`pybrilliantkiosk.bus` (an identity script run over SSH, and command/parse helpers for the CLI).

**Transport** (verified). Unix stream socket `/var/run/brilliant/server_socket`. One message per
line: the base64 Thrift-binary serialisation of `thrift_types.protocol.ttypes.Message`, newline
terminated. The clients use a 10 s socket timeout.

**Envelope** (verified). `Message(message_type, sequence_number, body_union)`; `MessageType`
`REQUEST=0`, `RESPONSE=1`, `HELLO=2`; `MessageBodyUnion` has exactly one of `request`, `response`,
`hello`. Sequence numbers are per client and increasing.

**Handshake** (verified). The client speaks first: `Hello(name=<peer name>, aliases=[])`. The bus
answers with its own `Hello(name='message_bus', domain=<this panel's device id>)`. The `domain` is
how every kiosk component learns the device id; the HA integration identifies a panel by it
(`brilliant_<first 12 characters>`), using only Brilliant's Python and libraries, so it works on a
stock panel without any kiosk file.

**Calls** (verified). `Request(command=<method name>, args_serialized=serialize(<method>_args),
is_oneway=False)`, where `<method>_args` and `<method>_result` are the generated classes on
`thrift_types.message_bus.MessageBusService`. The `Response` has `status` (`StatusResponse`;
`status_code` 0 = ok, else `error_msg`) and `result_serialized` (a `<method>_result`; the value is
its `.success`). Other message types may arrive before the response and are skipped.

`MessageBusService` methods:

| Method | Use | Status |
|---|---|---|
| `get_device(device_id)` | A device with all its peripherals and variables. Besides the panel's own id, the bridge reads `ring_virtual_device`, `schlage` and `configuration_virtual_device` this way | verified |
| `get_peripheral(device_id, peripheral_name)` | One peripheral. Each variable has `value` (string), `timestamp`, `externally_settable` | verified |
| `get_all()` | Everything on the bus | *inferred* (not used by the kiosk) |
| `set_variables_request(device_id, peripheral_name, variables, last_set_timestamps)` | Change variables. **Check-and-set**: pass each variable's current `timestamp` or the call fails ("timestamp stale"); only `externally_settable` variables can be written. Returns `modified_variables` (`variable_name`, `variable.value`). Used for the wired load on this panel and, cross-device, for Schlage locks and the Ring alarm | verified |
| `subscribe(SubscriptionRequest(device_id, peripheral_type, peripheral_id_glob))` | Change notifications | *inferred*; the kiosk polls (`watch` re-reads every second) |
| `register_peripheral`, `update_peripheral_status`, `set_updated_variables`, `delete_peripheral`, `register_virtual_device`, `set_home_id`, `handle_notification` | Used by peripherals themselves; do not call | *inferred* |

**Peripheral types** seen in `peripheral_type` (from the Thrift enum, used by the bridge): 1 lock,
2 doorbell, 59 camera, 89 security system.

**Peripherals on a panel** (24 on the surveyed panel): `hardware_peripheral`,
`faceplate_peripheral`, `gangbox_peripheral_0` (the wired load; `_1`, ... for more gangboxes),
`motion_detection_config_peripheral`, `switch_ui`, `voice_peripheral`, `homekit_peripheral`,
`ble_peripheral` (Shelly BLE readings, `shelly_sensor_readings` as base64 Thrift
`ShellySensorReadings`), `wifi_peripheral`, `art_config_peripheral` (`display_name` holds the room
name, e.g. "Kitchen"), and others. Variables the kiosk reads for MQTT: `hardware_peripheral.
cpu_temperature`, `current_release_tag` (firmware), `homekit_peripheral.paired`, `adk_enabled`.

**Integration ownership.** `configuration_virtual_device` has peripherals such as
`ring_configuration` and `schlage_configuration` whose `owner` variable is the device id of the
panel hosting that integration; the bridge publishes Ring and lock state to MQTT only from that
panel (verified in use).

**Findings from live calls**

* With the stock UI gone, `faceplate_peripheral` has `enable_lux`, `enable_pir_motion_score` and
  `enable_screen_motion_detection` at 0; the faceplate is still polled for motion.
* `enable_lux=1` works but the faceplate reports 0 lux in a lit room (section 2).
* `hardware_peripheral.camera_on` is read-only.
* `motion_detection_config_peripheral`: `trigger_screen=1`, `trigger_screen_off=1`,
  `trigger_screen_off_timeout_sec=600`: Brilliant's own screen-off logic, now fenced off by the
  display drop-in.
* A malformed client is logged and dropped by the bus ("Exception in triage process") with no
  effect on the other peripherals.
* The bus restarts (and is unreachable for one to two minutes) whenever the kiosk is switched on
  or off, because `install-local.sh` restarts `message_bus` when the display drop-in changes.

Writes (`set`) change Brilliant's own settings; the same bus drives the wall switch. Use with care.
