"""Library errors become translated Home Assistant errors, with a message that helps."""

from __future__ import annotations

import json
from pathlib import Path

import custom_components.brilliant_kiosk as package
import pytest
from custom_components.brilliant_kiosk.const import DOMAIN
from custom_components.brilliant_kiosk.errors import InstallRefused, to_ha_error
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pybrilliantkiosk import (
    ActivationRolledBack,
    AuthFailed,
    CannotConnect,
    ChecksumMismatch,
    CommandFailed,
    ConcurrentModification,
    DeviceIdUnavailable,
    HostKeyMismatch,
    InvalidArgument,
    NotInstalled,
    PanelError,
    ReleaseError,
    SettingsRejected,
    SetupBusy,
    SetupError,
    StockUIRestored,
    VerificationFailed,
)

COMPONENT = Path(package.__file__).parent
TEXTS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["exceptions"]


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (AuthFailed("x"), "invalid_auth"),
        (HostKeyMismatch("x"), "host_key_mismatch"),
        (SetupBusy("x"), "setup_busy"),
        (ConcurrentModification("x"), "concurrent_modification"),
        (CannotConnect("x"), "cannot_connect"),
        (ChecksumMismatch("x"), "checksum_mismatch"),
        (ReleaseError("x"), "release_error"),
        (CommandFailed("c", 1, "x"), "command_failed"),
        (VerificationFailed("x"), "verification_failed"),
        (SettingsRejected("x"), "settings_rejected"),
        (ActivationRolledBack("x"), "activation_rolled_back"),
        (StockUIRestored("x"), "stock_ui_restored"),
        (NotInstalled("x"), "not_installed"),
        (SetupError("x"), "setup_failed"),
        (SetupError("x", 2), "setup_failed"),
        (DeviceIdUnavailable("x"), "device_id_unavailable"),
        (PanelError("x"), "unknown_error"),
    ],
)
def test_every_error_has_its_own_translated_message(error: PanelError, key: str) -> None:
    result = to_ha_error(error)
    assert type(result) is HomeAssistantError
    assert result.translation_domain == DOMAIN
    assert result.translation_key == key
    assert result.translation_placeholders == {
        "error": "x" if key != "command_failed" else str(error)
    }
    assert TEXTS[key]["message"].strip()  # and there is text for it


def test_the_five_setup_failures_are_distinct_and_say_what_happened() -> None:
    keys = {
        to_ha_error(cls("detail")).translation_key
        for cls in (
            VerificationFailed,
            SettingsRejected,
            ActivationRolledBack,
            StockUIRestored,
            NotInstalled,
        )
    }
    assert len(keys) == 5  # none of them collapses into the generic message
    assert "rolled back to the previous" in TEXTS["activation_rolled_back"]["message"]
    assert "Brilliant's own screen" in TEXTS["stock_ui_restored"]["message"]
    assert "nothing was installed" in TEXTS["verification_failed"]["message"]
    assert "nothing was changed" in TEXTS["settings_rejected"]["message"]
    assert "Install it first" in TEXTS["not_installed"]["message"]


def test_a_busy_panel_is_still_busy_not_a_generic_setup_failure() -> None:
    assert to_ha_error(SetupBusy("x")).translation_key == "setup_busy"  # a SetupError subclass


def test_invalid_arguments_and_refused_installs_are_validation_errors() -> None:
    invalid = to_ha_error(InvalidArgument("bad"))
    assert isinstance(invalid, ServiceValidationError)
    assert invalid.translation_key == "invalid_argument"
    refused = to_ha_error(InstallRefused("release_not_pinned", "no", "v9"))
    assert isinstance(refused, ServiceValidationError)
    assert refused.translation_placeholders == {"error": "no", "tag": "v9"}


def test_the_host_key_message_points_at_re_authentication_not_at_a_repair() -> None:
    message = TEXTS["host_key_mismatch"]["message"]
    assert "repair" not in message.lower()
    assert "Devices & services" in message
