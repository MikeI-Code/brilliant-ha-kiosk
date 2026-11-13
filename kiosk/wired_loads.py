class WiredLoads:
    def __init__(self, bus, labels):
        if len(labels) != 3 or any(not label for label in labels) or len(set(labels)) != 3:
            raise ValueError("Configure three distinct physical-load labels in strip order")
        self.labels = tuple(labels)
        self.bus = bus

    def _target(self, strip):
        if strip not in (1, 2, 3):
            raise ValueError("Unknown strip")
        name = "gangbox_peripheral_{}".format(strip - 1)
        expected_label = self.labels[strip - 1]
        variables = self.bus.peripheral(name).variables
        if variables["display_name"].value.strip() != expected_label:
            raise ValueError("Circuit assignment has changed. Verify the strip mapping.")
        if variables["dimmable"].value != "1":
            raise ValueError("Circuit is not configured for dimming.")
        return name, variables

    def level(self, strip):
        _, variables = self._target(strip)
        power = variables["on"].value
        maximum = int(variables["max_intensity_value"].value)
        level = int(variables["intensity"].value)
        if power not in ("0", "1") or maximum != 1000 or not 0 <= level <= maximum:
            raise ValueError("Unexpected wired load state or calibration.")
        return level if power == "1" else 0, maximum

    def _write(self, name, variables, values):
        if any(k not in variables or not variables[k].externally_settable for k in values):
            raise ValueError("Requested control is unavailable.")
        self.bus.call("set_variables_request", device_id=self.bus.device_id,
                      peripheral_name=name, variables=values,
                      last_set_timestamps={k: variables[k].timestamp for k in values})

    def toggle(self, strip):
        name, variables = self._target(strip)
        current = variables["on"].value
        if current not in ("0", "1"):
            raise ValueError("Cannot toggle a circuit with unknown power state.")
        self._write(name, variables, {"on": "0" if current == "1" else "1"})

    def set_level(self, strip, level):
        values = {"on": "1", "intensity": str(level)} if level else {"on": "0"}
        # An explicit CAS rejection means this absolute level was not accepted.
        # Read fresh metadata once. Never retry a toggle or an ambiguous failure.
        for attempt in range(2):
            name, variables = self._target(strip)
            maximum = int(variables["max_intensity_value"].value)
            if maximum != 1000 or not 0 <= level <= maximum:
                raise ValueError("Requested level outside configured dimmer range.")
            changes = {k:v for k,v in values.items() if variables[k].value != v}
            if not changes:
                return
            try:
                self._write(name, variables, changes)
                return
            except RuntimeError as err:
                if attempt or "Check-and-set timestamp stale" not in str(err):
                    raise
