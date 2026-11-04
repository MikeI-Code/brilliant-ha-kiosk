#!/usr/bin/env python3
"""Talk to the Brilliant panel's local message bus (run ON the panel with Brilliant's Python):

  cd /data/switch-embedded
  env/bin/python /etc/ha-kiosk/brilliant_bus.py list                       # peripherals
  env/bin/python /etc/ha-kiosk/brilliant_bus.py get faceplate_peripheral   # variables
  env/bin/python /etc/ha-kiosk/brilliant_bus.py watch faceplate_peripheral movement_detected lux
  env/bin/python /etc/ha-kiosk/brilliant_bus.py set faceplate_peripheral enable_lux=0

How it works (see docs/hardware-and-services.md, "Message bus API"):
  * Unix socket /var/run/brilliant/server_socket; one message per line, each a base64 Thrift-binary
    `thrift_types.protocol.ttypes.Message` (lib.serialization, BASE64_THRIFT_BINARY).
  * The client speaks first with a HELLO (its name); the bus answers with its own HELLO whose
    `domain` is this panel's device id.
  * Calls are REQUEST messages: Request(command=<MessageBusService method>, args_serialized=
    <serialized <method>_args>); the RESPONSE carries a StatusResponse and <method>_result.
  * Writes are check-and-set: set_variables_request needs each variable's current timestamp, and
    only variables with externally_settable=True can be changed.
`set` changes Brilliant's own settings (the same bus drives the wall switch): use with care.
"""
import socket
import sys
import time

sys.path.insert(0, "/data/switch-embedded")
import lib.serialization as S  # noqa: E402  (Brilliant's libs, present on the panel)
from thrift_types.message_bus import MessageBusService as MBS  # noqa: E402
from thrift_types.protocol import ttypes as P  # noqa: E402

SOCKET = "/var/run/brilliant/server_socket"


class Bus:
    def __init__(self, name="ha_kiosk"):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(10)
        self.s.connect(SOCKET)
        self.f = self.s.makefile("rb")
        self.seq = 0
        self._send(P.MessageType.HELLO, P.MessageBodyUnion(hello=P.Hello(name=name, aliases=[])))
        self.device_id = self._recv().body_union.hello.domain

    def _send(self, mtype, body):
        self.seq += 1
        msg = P.Message(message_type=mtype, sequence_number=self.seq, body_union=body)
        self.s.sendall(S.serialize(msg).encode() + b"\n")

    def _recv(self):
        line = self.f.readline()
        if not line:
            raise EOFError("message bus closed the connection")
        return S.deserialize(P.Message, line.strip().decode())

    def call(self, method, **kwargs):
        args = getattr(MBS, method + "_args")(**kwargs)
        self._send(P.MessageType.REQUEST, P.MessageBodyUnion(
            request=P.Request(command=method, args_serialized=S.serialize(args), is_oneway=False)))
        while True:
            m = self._recv()
            if m.message_type != P.MessageType.RESPONSE:
                continue
            r = m.body_union.response
            if r.status.status_code != 0:
                raise RuntimeError(f"{method}: {r.status.error_msg} (code {r.status.status_code})")
            return S.deserialize(getattr(MBS, method + "_result"), r.result_serialized).success

    def device(self):
        return self.call("get_device", device_id=self.device_id)

    def peripheral(self, name):
        return self.call("get_peripheral", device_id=self.device_id, peripheral_name=name)

    def set(self, peripheral, values):
        cur = self.peripheral(peripheral).variables
        bad = [k for k in values if k not in cur or not cur[k].externally_settable]
        if bad:
            raise ValueError(f"not settable: {', '.join(bad)}")
        return self.call("set_variables_request", device_id=self.device_id, peripheral_name=peripheral,
                         variables=values, last_set_timestamps={k: cur[k].timestamp for k in values})

    def close(self):
        self.s.close()


def main(argv):
    if len(argv) < 2 or argv[1] not in ("list", "get", "watch", "set"):
        print(__doc__)
        return 2
    b = Bus()
    try:
        cmd = argv[1]
        if cmd == "list":
            print("device", b.device_id)
            for name, p in sorted(b.device().peripherals.items()):
                print(f"  {name}  ({len(p.variables or {})} variables)")
        elif cmd == "get":
            for k, v in sorted((b.peripheral(argv[2]).variables or {}).items()):
                print(f"{k} = {str(v.value)[:80]}{'' if v.externally_settable else '   [read-only]'}")
        elif cmd == "watch":
            names, last = argv[3:], None
            while True:
                v = b.peripheral(argv[2]).variables
                cur = {k: v[k].value for k in (names or sorted(v)) if k in v}
                if cur != last:
                    print(time.strftime("%H:%M:%S"), cur, flush=True)
                    last = cur
                time.sleep(1)
        elif cmd == "set":
            values = dict(a.split("=", 1) for a in argv[3:])
            r = b.set(argv[2], values)
            for mv in r.modified_variables or []:
                print("set", mv.variable_name, "=", mv.variable.value)
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
