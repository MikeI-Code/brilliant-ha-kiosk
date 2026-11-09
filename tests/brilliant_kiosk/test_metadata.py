"""The files hassfest, HACS and CI read: they must agree with each other and with the code."""

from __future__ import annotations

import ast
import json
import re
from importlib import metadata
from pathlib import Path

import custom_components.brilliant_kiosk as package
import pytest
import yaml
from custom_components.brilliant_kiosk.pinned import PINNED_RELEASES
from homeassistant.const import __version__ as HA_VERSION

COMPONENT = Path(package.__file__).parent
ROOT = COMPONENT.parent.parent


def _json(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


# -- manifest, HACS, CI: one Home Assistant version everywhere ----------------------------------


def test_the_test_environment_runs_the_minimum_home_assistant_of_hacs_json() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["homeassistant"] == HA_VERSION  # tested == minimum supported


def test_ci_installs_the_exact_test_package_matching_the_installed_home_assistant() -> None:
    lines = (ROOT / "requirements_test.txt").read_text(encoding="utf-8").splitlines()
    pins = dict(
        line.split("==") for line in lines if "==" in line and not line.lstrip().startswith("#")
    )
    installed = metadata.version("pytest-homeassistant-custom-component")
    assert pins["pytest-homeassistant-custom-component"] == installed
    assert f"homeassistant=={HA_VERSION}" in (
        metadata.requires("pytest-homeassistant-custom-component") or []
    )
    assert set(pins) == {"pytest-homeassistant-custom-component", "ruff", "mypy"}  # all exact


def test_the_workflow_uses_the_requirements_file_and_checks_formatting() -> None:
    workflow = (ROOT / ".github" / "workflows" / "integration.yml").read_text(encoding="utf-8")
    assert "pip install -r requirements_test.txt" in workflow
    assert workflow.count("ruff format --check") == 2  # library job and integration job
    assert "cache-dependency-path: pyproject.toml" not in workflow  # that file lists no packages
    assert "requirements_test.txt" in workflow.split("cache-dependency-path:")[-1].split("- run")[0]
    assert 'python-version: "3.14"' in workflow  # Home Assistant 2026.3 needs Python >= 3.14.2


def test_manifest_links_and_quality_scale() -> None:
    manifest = _json("manifest.json")
    base = "https://github.com/MikeI-Code/brilliant-ha-kiosk"
    # The docs live where users land: the distribution repo's README (the full integration doc).
    assert manifest["documentation"] == "https://github.com/MikeI-Code/ha-brilliant-kiosk"
    assert manifest["issue_tracker"] == f"{base}/issues"
    assert (
        manifest["quality_scale"] == "platinum"
    )  # every rule in quality_scale.yaml is done/exempt


def test_the_readme_the_manifest_points_at_links_the_integration_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/home-assistant-integration.md" in readme
    assert (ROOT / "docs" / "home-assistant-integration.md").is_file()


# -- quality_scale.yaml -------------------------------------------------------------------------


def test_every_quality_rule_is_done_or_exempt_with_a_reason() -> None:
    rules = yaml.safe_load((COMPONENT / "quality_scale.yaml").read_text(encoding="utf-8"))["rules"]
    todo = {name for name, rule in rules.items() if rule["status"] == "todo"}
    assert todo == set()
    for name, rule in rules.items():
        assert rule["status"] in {"done", "exempt", "todo"}, name
        if rule["status"] == "exempt":
            assert rule["comment"].strip(), name
    docs = {name for name in rules if name.startswith("docs-")}
    assert {name for name in docs if rules[name]["status"] == "done"} >= {
        "docs-actions",
        "docs-high-level-description",
        "docs-installation-instructions",
        "docs-removal-instructions",
        "docs-configuration-parameters",
        "docs-installation-parameters",
        "docs-data-update",
        "docs-examples",
        "docs-known-limitations",
        "docs-supported-devices",
        "docs-supported-functions",
        "docs-troubleshooting",
        "docs-use-cases",
    }


def _doc_sections() -> set[str]:
    text = (ROOT / "docs" / "home-assistant-integration.md").read_text(encoding="utf-8")
    return {m.lower() for m in re.findall(r"^## (.+)$", text, re.M)}


@pytest.mark.parametrize(
    "section",
    [
        "high-level description",
        "installation",
        "removal",
        "configuration parameters",
        "installation parameters",
        "actions",
        "data update",
        "examples",
        "known limitations",
        "supported devices",
        "supported functions",
        "troubleshooting",
        "use cases",
    ],
)
def test_the_documentation_has_every_section_the_docs_rules_ask_for(section: str) -> None:
    assert section in _doc_sections()


def test_every_action_is_documented_with_its_fields() -> None:
    text = (ROOT / "docs" / "home-assistant-integration.md").read_text(encoding="utf-8")
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    for name, definition in services.items():
        assert f"`brilliant_kiosk.{name}`" in text, name
        for field in definition["fields"]:
            if field != "config_entry_id":
                assert f"`{field}`" in text, (name, field)


# -- translations -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_every_translation_key_the_code_uses_exists(name: str) -> None:
    texts = _json(name)
    # Exceptions and issues (the entity modules use translation_key for entity names).
    for name_ in ("coordinator.py", "errors.py", "services.py", "update.py"):
        source = COMPONENT / name_
        text = source.read_text(encoding="utf-8")
        for key in re.findall(r'translation_key="(\w+)"', text):
            assert key in texts["exceptions"] or key in texts["issues"], (source.name, key)
    errors = texts["config"]["error"] | texts["options"]["error"]
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    used = set(re.findall(r'errors\["base"\] = "(\w+)"', flow))
    used |= set(re.findall(r'errors\[[\w_]+\] = "(\w+)"', flow))
    used |= set(re.findall(r'return "(\w+)"', flow))
    used |= set(re.findall(r'return "(\w+)", ', flow))
    used |= {"release_not_pinned", "nightly_not_allowed"}  # InstallRefused.key
    used -= {"mqtt_provider", "unknown_error"}
    assert used <= set(errors), used - set(errors)
    aborts = set(re.findall(r'reason="(\w+)"', flow))
    known = set(texts["config"]["abort"]) | set(texts["options"]["abort"])
    assert aborts <= known, aborts - known


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_services_strings_icons_and_yaml_agree(name: str) -> None:
    texts = _json(name)
    yaml_services = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    icons = _json("icons.json")["services"]
    assert set(texts["services"]) == set(yaml_services) == set(icons)
    for service, definition in yaml_services.items():
        assert set(texts["services"][service]["fields"]) == set(definition["fields"]), service


def test_removed_issues_have_no_leftover_texts() -> None:
    for name in ("strings.json", "translations/en.json"):
        issues = _json(name)["issues"]
        assert "voice_not_in_wyoming" not in issues
        assert "host_key_changed" not in issues  # a changed key is a re-authentication now
        assert "env_drift_unsafe" in issues
        assert "host_key_changed" not in _json(name)["config"]["abort"]


def test_en_json_resolves_the_references_of_strings_json() -> None:
    assert "[%key:" not in json.dumps(_json("translations/en.json"))


# -- the pinned release table -------------------------------------------------------------------


def test_pinned_releases_are_well_formed_and_include_the_first_stable_release() -> None:
    assert "v1.4.0" in PINNED_RELEASES
    for tag, pinned in PINNED_RELEASES.items():
        assert re.fullmatch(r"v\d+\.\d+\.\d+", tag), tag  # stable tags only, never a nightly
        assert re.fullmatch(r"[0-9a-f]{64}", pinned.sha256), tag
        assert 0 < pinned.size < 50 * 1024 * 1024, tag
    # Checked against the GitHub release assets (API digest and size, .sha256 asset, local sha256).
    assert PINNED_RELEASES["v1.4.0"].sha256 == (
        "6ea396194a70a5e130325fb21c1c9c24f310a95043c32bf704aa8fd9d8b85cf1"
    )
    assert PINNED_RELEASES["v1.4.0"].size == 774344


def test_the_integration_never_reads_other_integrations_config_entries() -> None:
    """No code path may look into another integration's entry (MQTT broker, Wyoming hosts)."""
    for source in COMPONENT.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "async_entries":
                pytest.fail(f"{source.name} lists config entries of other integrations")
    consts = (COMPONENT / "const.py").read_text(encoding="utf-8")
    assert "WYOMING" not in consts


# -- every form field explains itself ----------------------------------------------------------


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_every_data_field_has_a_data_description(name: str) -> None:
    texts = _json(name)
    for section in ("config", "options"):
        for step_id, step in texts[section]["step"].items():
            data = step.get("data", {})
            described = step.get("data_description", {})
            assert set(data) == set(described), (section, step_id, set(data) ^ set(described))
            for field, text in described.items():
                assert text.strip(), (section, step_id, field)


def test_every_field_of_every_form_is_in_the_strings() -> None:
    """The schema of each step, not just the strings, is what the user sees: compare them."""
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    texts = _json("strings.json")
    fields = {
        "user": {"host", "port", "username", "password", "private_key", "expected_fingerprint"},
        "install": {"install", "ha_url", "token", "mqtt_host", "mqtt_user", "mqtt_password"},
        "reauth_confirm": {"password", "private_key"},
        "reconfigure": {"host", "port"},
        "reauth_host_key": {"trust_new_key"},
        "reconfigure_host_key": {"trust_new_key"},
    }
    for step_id, names in fields.items():
        assert set(texts["config"]["step"][step_id]["data"]) == names, step_id
        assert set(texts["config"]["step"][step_id]["data_description"]) == names, step_id
    assert set(texts["options"]["step"]["init"]["data_description"]) == {
        "mqtt_provider",
        "mqtt_device_id",
        "manage_installs",
    }
    assert "CONF_NEW_HOST_KEY" in flow  # the trust box exists in the code these strings describe


def test_the_library_version_is_the_one_the_manifest_pins() -> None:
    import pybrilliantkiosk

    manifest = _json("manifest.json")
    assert manifest["requirements"] == [f"pybrilliantkiosk=={pybrilliantkiosk.__version__}"]
    pyproject = (ROOT / "lib" / "pybrilliantkiosk" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{pybrilliantkiosk.__version__}"' in pyproject
    assert pybrilliantkiosk.__version__ == "0.1.4"
    assert pybrilliantkiosk.__version__ == metadata.version("pybrilliantkiosk")


def test_the_md5_error_and_the_unreadable_device_id_have_texts() -> None:
    for name in ("strings.json", "translations/en.json"):
        texts = _json(name)["config"]
        assert "SHA256" in texts["error"]["md5_fingerprint"]
        assert "device id" in texts["abort"]["device_id_unavailable"]
        assert "MD5" not in texts["step"]["user"]["data_description"][
            "expected_fingerprint"
        ].replace("MD5 fingerprints are not accepted", "")
