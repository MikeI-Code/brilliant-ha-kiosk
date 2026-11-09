"""Command building and output parsing for ``brilliant_bus.py`` on the panel."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping

from .exceptions import InvalidArgument
from .models import BusListing, BusPeripheral, BusVariable

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SWITCH_DIR = "/data/switch-embedded"
BUS_SOCKET = "/var/run/brilliant/server_socket"
_BASE = f"cd {SWITCH_DIR} && env/bin/python /etc/ha-kiosk/brilliant_bus.py"
_DEVICE_ID_RE = re.compile(r"^device ([A-Za-z0-9_-]{12,64})$", re.M)
#: The HELLO of ``tools/brilliant_bus.py``, self-contained: it needs only Brilliant's own Python
#: and libraries, not one file of the kiosk, so it works on a stock panel (before the kiosk is
#: installed, and after it was removed). The bus answers a client's HELLO with its own whose
#: ``domain`` is the panel's device id (docs/hardware-and-services.md, section 6).
_DEVICE_ID_SCRIPT = f"""import socket, sys
sys.path.insert(0, {SWITCH_DIR!r})
import lib.serialization as S
from thrift_types.protocol import ttypes as P
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(10)
s.connect({BUS_SOCKET!r})
hello = P.Hello(name="ha_kiosk_identity", aliases=[])
msg = P.Message(
    message_type=P.MessageType.HELLO,
    sequence_number=1,
    body_union=P.MessageBodyUnion(hello=hello),
)
s.sendall(S.serialize(msg).encode() + b"\\n")
line = s.makefile("rb").readline()
print("device", S.deserialize(P.Message, line.strip().decode()).body_union.hello.domain)
"""
_PERIPHERAL_RE = re.compile(r"^\s+(\S+)\s+\((\d+) variables\)\s*$")
_VARIABLE_RE = re.compile(r"^(\S+) =(?: (.*?))?(   \[read-only\])?$")
_SET_RE = re.compile(r"^set (\S+) =(?: (.*))?$")


def validate_name(name: str, what: str = "name") -> str:
    """Return the name if it is a safe peripheral/variable identifier."""
    if not NAME_RE.match(name):
        raise InvalidArgument(f"invalid {what}: {name!r}")
    return name


def device_id_command() -> str:
    """Shell command printing ``device <id>``: Brilliant's id of the panel, from the stock bus."""
    return f"cd {SWITCH_DIR} && env/bin/python -c {shlex.quote(_DEVICE_ID_SCRIPT)}"


def parse_device_id(output: str) -> str | None:
    """The device id in the output of :func:`device_id_command` (None if it is not valid)."""
    match = _DEVICE_ID_RE.search(output)
    return match.group(1) if match else None


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
