#!/usr/bin/env python3
"""Smart dimming: estimate how bright the panel's room is, from Home Assistant, and print the
backlight levels to use:  "<awake level> <screensaver level> <ambient 0..1>".

The panels have no usable light sensor (the faceplate lux sensor reads 0; the camera lights its
red privacy LED), so the room light level is estimated from:
  * daylight: the sun's elevation (sun.sun), 0 at SUN_DARK_ELEV, 1 at SUN_BRIGHT_ELEV degrees
  * room lights: ROOM_LIGHTS (comma-separated entity ids); a light counts by its brightness,
    a switch/other entity counts as fully on
The brighter of the two wins.

If the room has a real light sensor in HA (any illuminance sensor: Zigbee/Z-Wave motion sensors,
Hue, ESPHome BH1750...), set LIGHT_SENSOR to it: its lux reading replaces the estimate, mapped on a
log scale from LUX_DARK (darkest level) to LUX_BRIGHT (full brightness). If it is unavailable the
estimate is used. Settings come from /etc/ha-kiosk/kiosk.env.
"""
import json
import math
import re
import sys
import urllib.request


def env(path="/etc/ha-kiosk/kiosk.env"):
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r'\s*([A-Z_][A-Z0-9_]*)\s*=\s*"?([^"\n#]*)"?', line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    except OSError:
        pass
    return out


def main():
    e = {**env(), **env("/var/lib/ha-kiosk/overrides.env")}   # HA/MQTT overrides win
    base = re.match(r"(https?://[^/\s]+)", e.get("HA_URL", ""))
    token = open("/etc/ha-kiosk/token", encoding="utf-8").read().strip()
    day = int(e.get("DAY_LEVEL", 15))
    photo = int(e.get("PHOTO_LEVEL", 12))
    low = int(e.get("MIN_LEVEL", 5))
    dark, bright = float(e.get("SUN_DARK_ELEV", -4)), float(e.get("SUN_BRIGHT_ELEV", 10))
    lights = [x.strip() for x in e.get("ROOM_LIGHTS", "").split(",") if x.strip()]

    def state(entity):
        req = urllib.request.Request(f"{base.group(1)}/api/states/{entity}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    sun = state("sun.sun")
    elev = float(sun["attributes"].get("elevation", 0))
    daylight = min(1.0, max(0.0, (elev - dark) / (bright - dark)))

    room = 0.0
    for ent in lights:
        try:
            s = state(ent)
        except Exception:
            continue
        if s["state"] != "on":
            continue
        b = s["attributes"].get("brightness")
        room = max(room, (b / 255.0) if (ent.startswith("light.") and b is not None) else 1.0)

    ambient = max(daylight, room)
    detail = f"sun={elev:.1f} room={room:.2f}"

    sensor = e.get("LIGHT_SENSOR", "").strip()
    if sensor:
        lux_dark, lux_bright = float(e.get("LUX_DARK", 5)), float(e.get("LUX_BRIGHT", 300))
        try:
            lux = float(state(sensor)["state"])
            ambient = min(1.0, max(0.0, math.log10(max(lux, 0.1) / lux_dark) / math.log10(lux_bright / lux_dark)))
            detail = f"{sensor}={lux:g}lx"
        except Exception:
            detail += f" ({sensor} unavailable)"
    awake = round(low + (day - low) * ambient)
    saver = round(low + (photo - low) * ambient)
    print(awake, saver, f"{ambient:.2f}", detail)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:          # HA unreachable etc.: caller keeps its fixed levels
        print(f"ambient: {ex}", file=sys.stderr)
        sys.exit(1)
