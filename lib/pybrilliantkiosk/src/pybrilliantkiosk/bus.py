"""Command building and output parsing for ``brilliant_bus.py`` on the panel."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping

from .exceptions import InvalidArgument
from .models import BusListing, BusPeripheral, BusVariable

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_BASE = "cd /data/switch-embedded && env/bin/python /etc/ha-kiosk/brilliant_bus.py"
_PERIPHERAL_RE = re.compile(r"^\s+(\S+)\s+\((\d+) variables\)\s*$")
_VARIABLE_RE = re.compile(r"^(\S+) =(?: (.*?))?(   \[read-only\])?$")
_SET_RE = re.compile(r"^set (\S+) =(?: (.*))?$")


def validate_name(name: str, what: str = "name") -> str:
    """Return the name if it is a safe peripheral/variable identifier."""
    if not NAME_RE.match(name):
        raise InvalidArgument(f"invalid {what}: {name!r}")
    return name


def list_command() -> str:
    """Shell command listing peripherals."""
    return f"{_BASE} list"


def get_command(peripheral: str) -> str:
    """Shell command reading a peripheral's variables."""
    return f"{_BASE} get {shlex.quote(validate_name(peripheral, 'peripheral'))}"


def set_command(peripheral: str, values: Mapping[str, object]) -> str:
    """Shell command writing variables; every argument is shell-quoted."""
    if not values:
        raise InvalidArgument("no values to set")
    args: list[str] = []
    for key, value in values.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        if "\n" in text or "\r" in text or "\x00" in text:
            raise InvalidArgument(f"invalid value for {key!r}")
        args.append(shlex.quote(f"{validate_name(key, 'variable')}={text}"))
    name = shlex.quote(validate_name(peripheral, "peripheral"))
    return f"{_BASE} set {name} {' '.join(args)}"


def parse_listing(output: str) -> BusListing:
    """Parse ``list`` output (``device <id>`` then ``  name  (N variables)``)."""
    device_id = ""
    peripherals: list[BusPeripheral] = []
    for line in output.splitlines():
        if line.startswith("device "):
            device_id = line.split(" ", 1)[1].strip()
            continue
        match = _PERIPHERAL_RE.match(line)
        if match:
            peripherals.append(BusPeripheral(match.group(1), int(match.group(2))))
    return BusListing(device_id, tuple(peripherals))


def parse_variables(output: str) -> tuple[BusVariable, ...]:
    """Parse ``get`` output (``name = value`` with an optional read-only marker)."""
    result: list[BusVariable] = []
    for line in output.splitlines():
        match = _VARIABLE_RE.match(line)
        if match:
            result.append(
                BusVariable(match.group(1), match.group(2) or "", match.group(3) is not None)
            )
    return tuple(result)


def parse_set_result(output: str) -> tuple[BusVariable, ...]:
    """Parse ``set`` output (``set name = value`` per modified variable)."""
    result: list[BusVariable] = []
    for line in output.splitlines():
        match = _SET_RE.match(line)
        if match:
            result.append(BusVariable(match.group(1), match.group(2) or "", False))
    return tuple(result)
