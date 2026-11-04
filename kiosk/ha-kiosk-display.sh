#!/bin/sh
# Backlight manager for the Brilliant HA kiosk.
#  Day:   full brightness while touched; after IDLE_DIM_SECS without a touch it shows the photo
#         screensaver (SCREENSAVER=1, at PHOTO_LEVEL) or dims to DIM_LEVEL (SCREENSAVER=0).
#  Night: screen fully off (SLEEP_START..SLEEP_END); a touch wakes it for NIGHT_WAKE_SECS.
# Writes the current state to /run/ha-kiosk-display/state so the kiosk can swallow the waking touch
# and show the screensaver. Settings come from /etc/ha-kiosk/kiosk.env.

[ -f /etc/ha-kiosk/kiosk.env ] && . /etc/ha-kiosk/kiosk.env
# Runtime overrides from Home Assistant (MQTT switches), written by ha-kiosk-bridge
[ -f /var/lib/ha-kiosk/overrides.env ] && . /var/lib/ha-kiosk/overrides.env
SLEEP_START=${SLEEP_START:-21:00}
SLEEP_END=${SLEEP_END:-06:30}
DAY_LEVEL=${DAY_LEVEL:-15}
DIM_LEVEL=${DIM_LEVEL:-4}
IDLE_DIM_SECS=${IDLE_DIM_SECS:-300}
NIGHT_WAKE_SECS=${NIGHT_WAKE_SECS:-180}
SCREENSAVER=${SCREENSAVER:-1}
PHOTO_LEVEL=${PHOTO_LEVEL:-12}
# Screensaver photos, in order of preference:
#   1. your own: /etc/ha-kiosk/photos (from .private/photos via install.sh) and
#      /var/lib/ha-kiosk/ha-photos (synced from Home Assistant's media library by the bridge)
#   2. otherwise the Brilliant app's photos, 3. otherwise Brilliant's stock art.
# Setting PHOTO_DIRS explicitly overrides all of this.
OWN_PHOTO_DIRS="/etc/ha-kiosk/photos /var/lib/ha-kiosk/ha-photos"
BRILLIANT_PHOTO_DIRS="/var/brilliant/object_store/disk_cache /var/brilliant/object_store/upload_queue"
STOCK_DIR=/etc/brilliant/content_preload/art_library
# CPU governor: full speed while someone is using the screen, frugal otherwise.
CPU_AWAKE_GOV=${CPU_AWAKE_GOV:-performance}
CPU_IDLE_GOV=${CPU_IDLE_GOV:-schedutil}
CPU_SLEEP_GOV=${CPU_SLEEP_GOV:-powersave}
# Wake when someone walks up (faceplate motion sensor): day (default), always, or off
MOTION_WAKE=${MOTION_WAKE:-day}
# Smart dimming: scale DAY_LEVEL/PHOTO_LEVEL down to MIN_LEVEL as the room gets darker (estimated
# from the sun and ROOM_LIGHTS in HA, see ha-kiosk-ambient.py). Re-evaluated every AMBIENT_SECS.
SMART_DIM=${SMART_DIM:-1}
AMBIENT_SECS=${AMBIENT_SECS:-60}
export TZ=${TZ_LOCAL:-EST5EDT,M3.2.0,M11.1.0}

BL=/sys/class/backlight/backlight_lcd
TOUCH=${TOUCH_DEVICE:-/dev/input/event0}
STATE_DIR=/run/ha-kiosk-display
mkdir -p "$STATE_DIR"

# Screensaver playlist (see the photo sources above). The Brilliant cache has no file extensions.
list_images() {   # list_images <dir>... : JPEG/PNG files, detected by content
    find "$@" -type f 2>/dev/null | while read -r f; do
        case "$(head -c 4 "$f" | od -An -tx1 | tr -d ' \n')" in
            ffd8ff*|89504e47) echo "$f" ;;
        esac
    done
}
build_photos() {
    out="$STATE_DIR/photos.txt"
    if [ -n "$PHOTO_DIRS" ]; then list_images $PHOTO_DIRS > "$out.tmp"; src="PHOTO_DIRS"
    else
        list_images $OWN_PHOTO_DIRS > "$out.tmp"; src="your photos"
        [ -s "$out.tmp" ] || { list_images $BRILLIANT_PHOTO_DIRS > "$out.tmp"; src="Brilliant app photos"; }
    fi
    [ -s "$out.tmp" ] || { find "$STOCK_DIR" -type f \( -name '*.jpg' -o -name '*.png' \) > "$out.tmp" 2>/dev/null; src="Brilliant stock art"; }
    mv "$out.tmp" "$out"
    echo "display: $(wc -l < "$out") screensaver photos ($src)"
}

set_gov() {
    for g in /sys/devices/system/cpu/cpufreq/policy*/scaling_governor; do echo "$1" > "$g" 2>/dev/null; done
}

# Current smart-dim levels ("<awake> <photos>"), or the fixed ones.
levels() {
    if [ "$SMART_DIM" = 1 ] && [ -s "$STATE_DIR/levels" ]; then cat "$STATE_DIR/levels"
    else echo "$DAY_LEVEL $PHOTO_LEVEL"; fi
}

ambient_loop() {
    last=""
    while :; do
        if out=$(python3 /etc/ha-kiosk/ha-kiosk-ambient.py 2>/dev/null); then
            set -- $out
            echo "$1 $2" > "$STATE_DIR/levels.tmp" && mv "$STATE_DIR/levels.tmp" "$STATE_DIR/levels"
            case "$(cat "$STATE_DIR/state" 2>/dev/null)" in
                awake) lvl=$1 ;; photos) lvl=$2 ;; *) lvl="" ;;
            esac
            [ -n "$lvl" ] && [ "$(cat "$BL/brightness")" != "$lvl" ] && echo "$lvl" > "$BL/brightness"
            [ "$1 $2" != "$last" ] && echo "display: smart dim -> awake $1, screensaver $2 ($3 $4 $5)"
            last="$1 $2"
        fi
        sleep "$AMBIENT_SECS"
    done
}

set_state() {  # set_state <awake|dim|photos|asleep>
    case "$1" in
        awake)  lvl=$(levels | cut -d' ' -f1); gov=$CPU_AWAKE_GOV ;;
        photos) lvl=$(levels | cut -d' ' -f2); gov=$CPU_IDLE_GOV ;;
        dim)    lvl=$DIM_LEVEL;   gov=$CPU_IDLE_GOV ;;
        asleep) lvl=0;            gov=$CPU_SLEEP_GOV ;;
    esac
    set_gov "$gov"
    # State file first when going dark (so touches are swallowed), last when waking.
    [ "$1" != awake ] && echo "$1" > "$STATE_DIR/state"
    if [ "$lvl" -eq 0 ]; then
        echo 0 > "$BL/brightness"; echo 4 > "$BL/bl_power"
    else
        echo 0 > /sys/class/graphics/fb0/blank 2>/dev/null   # undo any blanking (Brilliant's own screen-off)
        echo 0 > "$BL/bl_power"; echo "$lvl" > "$BL/brightness"
    fi
    [ "$1" = awake ] && { sleep 1; echo awake > "$STATE_DIR/state"; }
    echo "display: $1 (level $lvl, cpu $gov)"
}

is_night() {
    [ "${NIGHT_MODE:-1}" = 1 ] || return 1
    now=$(date +%H%M); s=$(echo "$SLEEP_START" | tr -d :); e=$(echo "$SLEEP_END" | tr -d :)
    if [ "$s" -gt "$e" ]; then [ "$now" -ge "$s" ] || [ "$now" -lt "$e" ]
    else [ "$now" -ge "$s" ] && [ "$now" -lt "$e" ]; fi
}

# Returns 0 if the touchscreen produced an event within $1 seconds.
touched_within() { timeout "$1" head -c 16 "$TOUCH" >/dev/null 2>&1; }

idle_state() {
    if is_night; then echo asleep
    elif [ "$SCREENSAVER" = 1 ]; then echo photos
    else echo dim; fi
}

# Motion wake. Brilliant's hardware service still decides "screen on" from the faceplate motion
# sensor, but (see message_bus-ha-kiosk.conf) it now writes into dummy files. When its screen goes
# from off to on, someone walked up, so we wake too. (Brightness tweaks while on don't count.)
SHADOW=/run/ha-kiosk-shadow
brilliant_screen_on() {
    [ "$(cat "$SHADOW/blank" 2>/dev/null)" != 1 ] && [ "$(cat "$SHADOW/brightness" 2>/dev/null || echo 0)" -gt 0 ] &&
        [ "$(cat "$SHADOW/bl_power" 2>/dev/null || echo 0)" = 0 ]
}
motion_wake_allowed() {
    case "$MOTION_WAKE" in always) return 0 ;; off|0) return 1 ;; *) ! is_night ;; esac
}

trap '[ -n "$AMB_PID" ] && kill "$AMB_PID" 2>/dev/null; set_state awake; set_gov schedutil; exit 0' INT TERM
build_photos
AMB_PID=""
if [ "$SMART_DIM" = 1 ]; then ambient_loop & AMB_PID=$!; fi

while true; do
    set_state awake
    if is_night; then hold=$NIGHT_WAKE_SECS; else hold=$IDLE_DIM_SECS; fi
    while touched_within "$hold"; do :; done        # stay awake while being used
    cur=$(idle_state)
    [ "$cur" = photos ] && build_photos             # pick up newly synced photos
    set_state "$cur"
    # Idle: wake on touch or (if allowed) motion; re-check the schedule every ~30 s.
    was_on=0; brilliant_screen_on && was_on=1; n=0
    while ! touched_within 2; do
        now_on=0; brilliant_screen_on && now_on=1
        if [ $was_on = 0 ] && [ $now_on = 1 ] && motion_wake_allowed; then echo "display: motion"; break; fi
        was_on=$now_on
        n=$((n + 1)); [ $n -lt 15 ] && continue; n=0
        nxt=$(idle_state)
        [ "$nxt" != "$cur" ] && { cur=$nxt; set_state "$cur"; }
    done
done
