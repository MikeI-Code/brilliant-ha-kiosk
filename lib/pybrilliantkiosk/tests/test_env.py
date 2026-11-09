import pytest

from pybrilliantkiosk import EnvFile, InvalidArgument, is_secret
from pybrilliantkiosk.env import MASK, format_value

SAMPLE = """# header comment
KIOSK_MODE=native
DAY_LEVEL=15

# secrets
MQTT_PASSWORD=hunter2
HA_TOKEN=
DAY_LEVEL=12
NAME="quoted value"
SINGLE='it is'
  INDENT=1
#COMMENTED=1
garbage line
"""


def test_parse_last_wins_and_unquote():
    values = EnvFile.parse(SAMPLE).values()
    assert values["DAY_LEVEL"] == "12"
    assert values["NAME"] == "quoted value"
    assert values["SINGLE"] == "it is"
    assert values["INDENT"] == "1"
    assert "COMMENTED" not in values
    assert list(values)[:2] == ["KIOSK_MODE", "DAY_LEVEL"]


def test_roundtrip_preserves_everything():
    assert EnvFile.parse(SAMPLE).serialize() == SAMPLE
    assert EnvFile.parse("").serialize() == ""
    assert EnvFile.parse("A=1\r\nB=2\r\n").serialize() == "A=1\nB=2\n"


def test_set_updates_last_occurrence_in_place():
    env = EnvFile.parse(SAMPLE).set("DAY_LEVEL", "9")
    text = env.serialize()
    assert "DAY_LEVEL=15\n" in text
    assert "DAY_LEVEL=12" not in text
    assert text.index("DAY_LEVEL=9") > text.index("HA_TOKEN=")
    assert env.values()["DAY_LEVEL"] == "9"
    assert text.startswith("# header comment\n")


def test_set_appends_new_key_and_quotes():
    env = EnvFile.parse("A=1\n").set("B", "has space")
    assert env.serialize() == "A=1\nB='has space'\n"
    assert env.values()["B"] == "has space"
    assert EnvFile().set("EMPTY", "").serialize() == "EMPTY=\n"


def test_delete_removes_all_assignments():
    env = EnvFile.parse(SAMPLE).delete("DAY_LEVEL")
    assert "DAY_LEVEL" not in env.values()
    assert "# secrets" in env.serialize()


def test_apply():
    env = EnvFile.parse("A=1\nB=2\n").apply({"A": "x", "C": "3"}, ["B"])
    assert env.serialize() == "A=x\nC=3\n"


def test_masking():
    masked = EnvFile.parse(SAMPLE).masked_values()
    assert masked["MQTT_PASSWORD"] == MASK
    assert masked["HA_TOKEN"] == ""  # empty secrets stay empty
    assert masked["KIOSK_MODE"] == "native"
    assert is_secret("my_secret_thing")
    assert not is_secret("DAY_LEVEL")


@pytest.mark.parametrize("key", ["1BAD", "has space", "A-B", "", "A=B"])
def test_invalid_keys(key):
    with pytest.raises(InvalidArgument):
        EnvFile().set(key, "x")
    with pytest.raises(InvalidArgument):
        EnvFile().delete(key)


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\x00b", "it's a'"])
def test_invalid_values(value):
    with pytest.raises(InvalidArgument):
        format_value(value)
