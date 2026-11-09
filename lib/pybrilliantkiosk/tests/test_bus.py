import pytest

from pybrilliantkiosk import (
    BusPeripheral,
    BusVariable,
    InvalidArgument,
    bus,
    parse_listing,
    parse_set_result,
    parse_variables,
)

LISTING = """device 4cb2-abc
  faceplate_peripheral  (12 variables)
  light_peripheral  (3 variables)
not a peripheral
"""
GET = """enable_lux = 0
lux = 12.5   [read-only]
name = a = b
empty =
ro_empty =    [read-only]
"""


def test_parse_listing():
    listing = parse_listing(LISTING)
    assert listing.device_id == "4cb2-abc"
    assert listing.peripherals == (
        BusPeripheral("faceplate_peripheral", 12),
        BusPeripheral("light_peripheral", 3),
    )
    assert parse_listing("").device_id == ""


def test_parse_variables():
    assert parse_variables(GET) == (
        BusVariable("enable_lux", "0", False),
        BusVariable("lux", "12.5", True),
        BusVariable("name", "a = b", False),
        BusVariable("empty", "", False),
        BusVariable("ro_empty", "", True),
    )


def test_parse_set_result():
    out = parse_set_result("set enable_lux = 1\nnoise\n")
    assert out == (BusVariable("enable_lux", "1", False),)


def test_commands_quote_everything():
    assert bus.list_command().endswith("brilliant_bus.py list")
    assert bus.get_command("faceplate_peripheral").endswith("get faceplate_peripheral")
    cmd = bus.set_command("p", {"a": "x y; rm -rf /", "b": True, "c": 3})
    assert cmd.endswith("set p 'a=x y; rm -rf /' b=true c=3")
    assert cmd.startswith("cd /data/switch-embedded && env/bin/python ")


@pytest.mark.parametrize("name", ["", "a b", "a;b", "$(x)", "a/b", "x" * 200])
def test_invalid_names(name):
    with pytest.raises(InvalidArgument):
        bus.get_command(name)
    with pytest.raises(InvalidArgument):
        bus.set_command("p", {name: 1})


def test_invalid_set_inputs():
    with pytest.raises(InvalidArgument):
        bus.set_command("p", {})
    with pytest.raises(InvalidArgument):
        bus.set_command("p", {"a": "x\ny"})
