import ast
import re
import subprocess
from pathlib import Path

import pytest

from pybrilliantkiosk import EnvFile, InvalidArgument, is_secret
from pybrilliantkiosk.env import (
    _BAD_VALUE_RE,
    MASK,
    format_value,
    render_line,
    validate_setting_key,
)

SETUP_TOOL = Path(__file__).resolve().parents[3] / "kiosk" / "ha-kiosk-setup"

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
    assert "DAY_LEVEL=15\n" in text  # the earlier assignment is left alone
    assert "DAY_LEVEL=12" not in text
    assert text.index('DAY_LEVEL="9"') > text.index("HA_TOKEN=")
    assert env.values()["DAY_LEVEL"] == "9"
    assert text.startswith("# header comment\n")


def test_set_appends_new_key_double_quoted():
    # kiosk.env is sourced by root shell scripts: values are written KEY="value" like
    # ha-kiosk-setup's render_env, never bare.
    env = EnvFile.parse("A=1\n").set("B", "has space")
    assert env.serialize() == 'A=1\nB="has space"\n'
    assert env.values()["B"] == "has space"
    assert EnvFile().set("EMPTY", "").serialize() == 'EMPTY=""\n'
    assert render_line("K", "v") == 'K="v"'
    # The written text parses back to the same value (the quotes are not part of it).
    assert EnvFile.parse(env.serialize()).values()["B"] == "has space"


def test_delete_removes_all_assignments():
    env = EnvFile.parse(SAMPLE).delete("DAY_LEVEL")
    assert "DAY_LEVEL" not in env.values()
    assert "# secrets" in env.serialize()


def test_apply():
    env = EnvFile.parse("A=1\nB=2\n").apply({"A": "x", "C": "3"}, ["B"])
    assert env.serialize() == 'A="x"\nC="3"\n'


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


@pytest.mark.parametrize(
    "value", ["a\nb", "a\rb", "a\x00b", "it's", 'a"b', "a$b", "a`b", "a\\b", "x" * 1025]
)
def test_invalid_values(value):
    with pytest.raises(InvalidArgument):
        format_value(value)


def test_format_value_accepts_shell_metacharacters_that_quoting_makes_inert():
    assert format_value("has space #and=more") == "has space #and=more"
    assert format_value("a;b|c&d>e<f(g)*?~") == "a;b|c&d>e<f(g)*?~"
    assert format_value("") == ""


@pytest.mark.skipif(not SETUP_TOOL.is_file(), reason="kiosk/ha-kiosk-setup not in this checkout")
def test_value_charset_is_exactly_the_setup_tools_plus_single_quote():
    source = SETUP_TOOL.read_text(encoding="utf-8")
    match = re.search(r"^BAD_VALUE = re\.compile\((.*)\)$", source, re.MULTILINE)
    assert match, "BAD_VALUE not found in ha-kiosk-setup"
    setup_regex = re.compile(ast.literal_eval(match.group(1)))
    assert "or \"'\" in val" in source  # the tool rejects a single quote separately
    for code in range(0x100):
        char = chr(code)
        ours = bool(_BAD_VALUE_RE.search(char))
        theirs = bool(setup_regex.search(char)) or char == "'"
        assert ours == theirs, f"charset differs for {char!r}"


# Values that would execute or expand if written bare and sourced by `sh`.
INERT_VALUES = [
    "a;touch injected",
    "a|touch injected",
    "a&touch injected",
    "a>injected",
    "(touch injected)",
    "two words",
    "a b;c",
    "#not-a-comment",
    "*",
    "~",
    "a=b",
    "http://h:8123/p?x=1&y=2",
    "x" * 1024,
]


@pytest.mark.parametrize("value", INERT_VALUES)
def test_written_file_sourced_by_sh_keeps_metacharacters_inert(tmp_path: Path, value: str):
    text = EnvFile().set("SAFE", "1").set("PAYLOAD", value).serialize()
    env_file = tmp_path / "kiosk.env"
    env_file.write_text(text, encoding="utf-8")
    done = subprocess.run(  # noqa: S603
        ["sh", "-c", '. "$1"; printf %s "$PAYLOAD"; printf "|%s" "$SAFE"', "sh", str(env_file)],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout == f"{value}|1"
    assert done.stderr == ""
    assert not (tmp_path / "injected").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kiosk.env"]


def test_bare_lines_would_have_been_exploitable(tmp_path: Path):
    """Documents the bug the quoting fixes: the same payload written bare runs the command."""
    env_file = tmp_path / "bare.env"
    env_file.write_text("PAYLOAD=a;touch injected\n", encoding="utf-8")
    subprocess.run(["sh", "-c", '. "$1"', "sh", str(env_file)], cwd=tmp_path, check=True)  # noqa: S603, S607
    assert (tmp_path / "injected").exists()


@pytest.mark.parametrize("key", ["HA_URL", "GESTURE_2_TAP", "GESTURE_3_SWIPE_LEFT"])
def test_known_setting_keys(key):
    assert validate_setting_key(key) == key


@pytest.mark.parametrize("key", ["NOPE", "GESTURE_4_TAP", "bad key"])
def test_unknown_setting_keys(key):
    with pytest.raises(InvalidArgument):
        validate_setting_key(key)
