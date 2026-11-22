"""What each physical strip controls: a wired load on this panel, or a Home Assistant light.

Adapted from brilliant-ha-kiosk PR #6 (wired_loads.py), generalised from exactly three wired
dimmers to any number of strips. A strip is one of:

  WiredStrip    the panel's own gangbox load (tap = toggle, slide = dim if the load is a dimmer)
  HaLightStrip  a Home Assistant light (tap = on/off, slide = brightness). With circuit=None the
                strip never touches the panel's wired load (bulbs that are powered elsewhere).
                With a circuit (bulbs fed by that gang) the circuit is switched on when needed and
                switched off only as a fallback, when Home Assistant is unreachable or the bulbs
                are offline.
"""
import json
import urllib.request

FULL = 1000   # every strip reports and accepts levels on a 0..1000 scale


class WiredStrip:
    def __init__(self, bus, index, label):
        self.bus, self.label = bus, label
        self.name = "gangbox_peripheral_{}".format(index)

    def _target(self):
        variables = self.bus.peripheral(self.name).variables
        if variables["display_name"].value.strip() != self.label:
            raise ValueError("Circuit assignment has changed. Verify the strip mapping.")
        return variables

    @staticmethod
    def _dimmable(variables):
        return "dimmable" in variables and variables["dimmable"].value == "1" and "intensity" in variables

    def _write(self, variables, values):
        if any(k not in variables or not variables[k].externally_settable for k in values):
            raise ValueError("Requested control is unavailable.")
        self.bus.call("set_variables_request", device_id=self.bus.device_id, peripheral_name=self.name,
                      variables=values, last_set_timestamps={k: variables[k].timestamp for k in values})

    def is_on(self):
        power = self._target()["on"].value
        if power not in ("0", "1"):
            raise ValueError("Unknown power state.")
        return power == "1"

    def switch(self, on):
        variables = self._target()
        want = "1" if on else "0"
        if variables["on"].value != want:
            self._write(variables, {"on": want})

    def level(self):
        variables = self._target()
        power = variables["on"].value
        if power not in ("0", "1"):
            raise ValueError("Unknown power state.")
        if not self._dimmable(variables):
            return (FULL if power == "1" else 0), FULL
        maximum = int(variables["max_intensity_value"].value)
        level = int(variables["intensity"].value)
        if maximum != FULL or not 0 <= level <= maximum:
            raise ValueError("Unexpected wired load state or calibration.")
        return (level if power == "1" else 0), maximum

    def toggle(self):
        variables = self._target()
        current = variables["on"].value
        if current not in ("0", "1"):
            raise ValueError("Cannot toggle a circuit with unknown power state.")
        self._write(variables, {"on": "0" if current == "1" else "1"})

    def set_level(self, level):
        # An explicit check-and-set rejection means this absolute level was not accepted.
        # Read fresh metadata once. Never retry a toggle or an ambiguous failure.
        for attempt in range(2):
            variables = self._target()
            if not 0 <= level <= FULL:
                raise ValueError("Requested level outside configured dimmer range.")
            if self._dimmable(variables):
                if int(variables["max_intensity_value"].value) != FULL:
                    raise ValueError("Unexpected dimmer calibration.")
                values = {"on": "1", "intensity": str(level)} if level else {"on": "0"}
            else:
                values = {"on": "1" if level else "0"}
            changes = {k: v for k, v in values.items() if variables[k].value != v}
            if not changes:
                return
            try:
                self._write(variables, changes)
                return
            except RuntimeError as err:
                if attempt or "Check-and-set timestamp stale" not in str(err):
                    raise


class HaLight:
    """Minimal Home Assistant REST client for one light entity."""

    def __init__(self, base, token, entity, timeout=2.5, opener=urllib.request.urlopen):
        self.base, self.token, self.entity = base.rstrip("/"), token, entity
        self.timeout, self.opener = timeout, opener

    def _request(self, path, payload=None):
        req = urllib.request.Request(
            self.base + path, method="POST" if payload is not None else "GET",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with self.opener(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode() or "null")

    def state(self):
        """(state string, level 0..1000)."""
        s = self._request("/api/states/" + self.entity)
        brightness = (s.get("attributes") or {}).get("brightness")
        level = int(round(float(brightness) * FULL / 255)) if s.get("state") == "on" and brightness is not None else 0
        return s.get("state"), level

    def turn_on(self, level=None):
        data = {"entity_id": self.entity}
        if level is not None:
            data["brightness_pct"] = max(1, min(100, int(round(level * 100 / FULL))))
        self._request("/api/services/light/turn_on", data)

    def turn_off(self):
        self._request("/api/services/light/turn_off", {"entity_id": self.entity})


class HaLightStrip:
    def __init__(self, circuit, light, report=print):
        self.circuit, self.light, self.report = circuit, light, report

    def level(self):
        if self.circuit is not None and not self.circuit.is_on():
            return 0, FULL
        state, level = self.light.state()
        if state not in ("on", "off"):
            raise ValueError("Bulbs are {} in Home Assistant.".format(state))
        return level, FULL

    def toggle(self):
        if self.circuit is None:               # bulbs are not on this panel's wiring: HA only
            state, _ = self.light.state()
            if state not in ("on", "off"):
                raise ValueError("Bulbs are {} in Home Assistant.".format(state))
            self.light.turn_off() if state == "on" else self.light.turn_on()
            return
        if not self.circuit.is_on():
            self.circuit.switch(True)          # bulbs power up and come on by themselves
            return
        try:
            state, _ = self.light.state()
            if state == "on":
                self.light.turn_off()
                return
            if state == "off":
                self.light.turn_on()
                return
            self.report("Bulbs are {} in Home Assistant: switching the circuit instead".format(state))
        except OSError as err:
            self.report("Home Assistant unreachable ({}): switching the circuit instead".format(err))
        self.circuit.switch(False)

    def set_level(self, level):
        if self.circuit is not None:
            self.circuit.switch(True)
        self.light.turn_on(level)


class Strips:
    """Routes GestureOutput's (strip, ...) calls to the strip objects. Strips are numbered from 1."""

    def __init__(self, strips):
        self.strips = dict(strips)

    def _get(self, strip):
        if strip not in self.strips:
            raise ValueError("Unknown strip")
        return self.strips[strip]

    def level(self, strip):
        return self._get(strip).level()

    def toggle(self, strip):
        self._get(strip).toggle()

    def set_level(self, strip, level):
        self._get(strip).set_level(level)
