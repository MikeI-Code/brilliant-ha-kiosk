import pytest

from pybrilliantkiosk import KioskVersion, is_newer, parse_version


def test_parse():
    assert parse_version("v1.4.0") == KioskVersion(1, 4, 0)
    assert parse_version("1.4.0") == KioskVersion(1, 4, 0)
    assert parse_version("v1.4.0-nightly.20261108.g89ec239") == KioskVersion(1, 4, 0, 1, 20261108)
    assert parse_version("v1.4.0-nightly.20261108") == KioskVersion(1, 4, 0, 1, 20261108)
    assert parse_version(" v2.10.3 ") == KioskVersion(2, 10, 3)


@pytest.mark.parametrize(
    "text", [None, "", "nightly-20261108", "v1.4", "v1.4.0-rc1", "v1.4.0-nightly.2026.gabc", "dev"]
)
def test_unparseable(text):
    assert parse_version(text) is None


@pytest.mark.parametrize(
    ("candidate", "installed"),
    [
        ("v1.5.0", "v1.4.0"),
        ("v1.10.0", "v1.9.0"),  # numeric, not lexical
        ("v1.4.1", "v1.4.0"),
        ("v1.4.0-nightly.20261108.g1", "v1.4.0"),  # a nightly built after v1.4.0 is newer
        ("v1.4.0-nightly.20261109.g2", "v1.4.0-nightly.20261108.g1"),  # dates order nightlies
        ("v1.5.0", "v1.4.0-nightly.20261108.g1"),  # the next stable beats nightlies of the last
        ("v1.5.0-nightly.20261110.g3", "v1.4.0"),
    ],
)
def test_newer(candidate, installed):
    assert is_newer(candidate, installed)
    assert not is_newer(installed, candidate)  # and never the reverse: no downgrades offered


@pytest.mark.parametrize(
    ("candidate", "installed"),
    [
        ("v1.4.0", "v1.4.0"),
        ("v1.4.0", "v1.4.0-nightly.20261108.g1"),  # stable channel must not downgrade a nightly
        ("v1.4.0-nightly.20261108.g1", "v1.4.0-nightly.20261108.g2"),  # same day: equal
        ("v1.5.0", None),  # unknown installed version: nothing is offered
        ("v1.5.0", "weird"),
        (None, "v1.4.0"),
        ("nightly-20261108", "v1.4.0"),  # a tag is not a version
    ],
)
def test_not_newer(candidate, installed):
    assert not is_newer(candidate, installed)
