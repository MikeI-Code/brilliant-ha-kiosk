"""Command building and output parsing for ``brilliant_bus.py`` on the panel."""

from __future__ import annotations

import re
import shlex
import unicodedata
from collections.abc import Mapping

from .exceptions import InvalidArgument
from .models import BusListing, BusPeripheral, BusVariable

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SWITCH_DIR = "/data/switch-embedded"
BUS_SOCKET = "/var/run/brilliant/server_socket"
_BASE = f"cd {SWITCH_DIR} && env/bin/python /etc/ha-kiosk/brilliant_bus.py"
_DEVICE_ID_RE = re.compile(r"^device ([A-Za-z0-9_-]{12,64})$", re.M)
_ROOM_RE = re.compile(r"^room (.*)$", re.M)
#: The longest room name kept (Brilliant's own names are a word or two).
ROOM_MAX_LENGTH = 64
#: The HELLO of ``tools/brilliant_bus.py``, self-contained: it needs only Brilliant's own Python
#: and libraries, not one file of the kiosk, so it works on a stock panel (before the kiosk is
#: installed, and after it was removed). The bus answers a client's HELLO with its own whose
#: ``domain`` is the panel's device id (docs/hardware-and-services.md, section 6).
_IDENTITY_SCRIPT = f"""import socket, sys
sys.path.insert(0, {SWITCH_DIR!r})
import lib.serialization as S
from thrift_types.protocol import ttypes as P
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(10)
s.connect({BUS_SOCKET!r})
f = s.makefile("rb")
def send(number, kind, body):
    msg = P.Message(message_type=kind, sequence_number=number, body_union=body)
    s.sendall(S.serialize(msg).encode() + b"\\n")
def receive():
    line = f.readline()
    if not line:
        raise EOFError("closed")
    return S.deserialize(P.Message, line.strip().decode())
hello = P.Hello(name="ha_kiosk_identity", aliases=[])
send(1, P.MessageType.HELLO, P.MessageBodyUnion(hello=hello))
device = receive().body_union.hello.domain
print("device", device, flush=True)
try:
    from thrift_types.message_bus import MessageBusService as M
    args = M.get_peripheral_args(device_id=device, peripheral_name="art_config_peripheral")
    request = P.Request(
        command="get_peripheral", args_serialized=S.serialize(args), is_oneway=False
    )
    send(2, P.MessageType.REQUEST, P.MessageBodyUnion(request=request))
    while True:
        message = receive()
        if message.message_type == P.MessageType.RESPONSE:
            break
    response = message.body_union.response
    if response.status.status_code != 0:
        raise RuntimeError("refused")
    peripheral = S.deserialize(M.get_peripheral_result, response.result_serialized).success
    room = (peripheral.variables or {{}}).get("display_name")
    if room is not None:
        print("room", " ".join(str(room.value).splitlines()))
except Exception:
    pass
"""
_PERIPHERAL_RE = re.compile(r"^\s+(\S+)\s+\((\d+) variables\)\s*$")
_VARIABLE_RE = re.compile(r"^(\S+) =(?: (.*?))?(   \[read-only\])?$")
_SET_RE = re.compile(r"^set (\S+) =(?: (.*))?$")


def validate_name(name: str, what: str = "name") -> str:
    """Return the name if it is a safe peripheral/variable identifier."""
    if not NAME_RE.match(name):
        raise InvalidArgument(f"invalid {what}: {name!r}")
    return name


def identity_command() -> str:
    """Shell command printing ``device <id>`` and, if known, ``room <name>`` (stock bus)."""
    return f"cd {SWITCH_DIR} && env/bin/python -c {shlex.quote(_IDENTITY_SCRIPT)}"


def parse_device_id(output: str) -> str | None:
    """The device id in the output of :func:`identity_command` (None if it is not valid)."""
    match = _DEVICE_ID_RE.search(output)
    return match.group(1) if match else None


def parse_room(output: str) -> str | None:
    """The room name in the output of :func:`identity_command`, cleaned (None if absent or blank).

    Control characters are dropped, the text is stripped and cut to :data:`ROOM_MAX_LENGTH`.
    """
    match = _ROOM_RE.search(output)
    if match is None:
        return None
    text = "".join(c for c in match.group(1) if unicodedata.category(c)[0] != "C")
    return text.strip()[:ROOM_MAX_LENGTH].strip() or None


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
