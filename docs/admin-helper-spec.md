# Brilliant Kiosk admin helper: spec (target: HA Integration Quality Scale **Platinum**)

> **Superseded.** This is the original design draft of 2026-11-09 and is kept for its history. What
> was built is described in [home-assistant-integration.md](home-assistant-integration.md), which
> is the document to read and to keep current. Where the two disagree, that one is right. What
> changed from this draft:
>
> * **Transport through `ha-kiosk-setup`.** The library does not edit `/etc/ha-kiosk/kiosk.env`
>   or run `install-local.sh` by itself. It drives the on-panel `ha-kiosk-setup` tool (install,
>   reapply, rollback, status, check-env) and writes both copies of `kiosk.env` (running and
>   saved) together under the tool's lock, guarded by checksums. `Panel.install` takes
>   the bundle's pinned size, the settings and the token for a first install.
> * **Administrators only.** The integration's actions run commands as root on a panel, so they
>   are registered as admin actions (a user who is not an administrator is refused; automations
>   are not affected). Configuration, options and repair dialogs are administrator dialogs of
>   Home Assistant itself.
> * **No add-on yet.** `addon/brilliant_admin` (component 3 below) does not exist, and the
>   integration manages no `input_*`, counter or timer helpers. Everything is done by the
>   integration's own entities, actions and dialogs.
> * **`via_device`, not shared identifiers.** The panel has its own device, identified by
>   `(brilliant_kiosk, <node>)`, and it points to the MQTT integration's device with `via_device`
>   once that exists. The integration never claims the MQTT device's identifiers, which the draft
>   proposed ("reuse the MQTT device via identifiers").
> * **100 % coverage, not 95 %.** Both suites (the integration's and the library's) are gated at
>   100 % line coverage in CI, with `ruff`, `mypy --strict` and the Home Assistant tests pinned to
>   exact versions (`requirements_test.txt`).
> * **Smaller entity and discovery set, and a self-assessed tier.** There is no wake-screen
>   button, day-level number or switch entity; discovery is MQTT discovery of a running kiosk
>   (`kiosk_state` = `active`), not zeroconf/DHCP. The Platinum tier in
>   `custom_components/brilliant_kiosk/quality_scale.yaml` is the author's own assessment: neither
>   hassfest's quality-scale validation nor `.strict-typing` applies to a custom integration.


Status: draft 2026-11-09, branch `nightly`.

## Goal
Manage Brilliant kiosk panels from Home Assistant: panel config variables, HA helpers, Brilliant
message-bus variables, service restart/status/logs, version installs, screen/brightness and a
health dashboard.

## Components
1. **`pybrilliantkiosk`** (`lib/pybrilliantkiosk/`): a typed async Python library (`py.typed`,
   mypy --strict). It is the only code that talks to a panel. It uses `asyncssh` (password or key,
   host-key pinning) and returns dataclasses. API:
   - `Panel.health()` gives uptime, load, memory, VERSION, unit states, backlight and display state
   - `Panel.get_env()` / `Panel.set_env(changes, delete)` edits `/etc/ha-kiosk/kiosk.env` atomically
     and keeps a `.bak`; secrets are masked when read
   - `Panel.restart(unit)`, `start`, `stop`, `reboot()`, `logs(unit, lines)`
   - `Panel.bus_peripherals()`, `bus_get(p)`, `bus_set(p, values)` wrap `brilliant_bus.py` on the panel
   - `Panel.install(bundle: bytes, sha256)` uploads, verifies and runs `install-local.sh`, keeping
     the env and token
   - `Releases(session).list()` returns GitHub releases (stable and nightly) with their asset sha256
   - Typed exceptions: `CannotConnect`, `AuthFailed`, `HostKeyMismatch`, `CommandFailed`
   - Published to PyPI so it can be pinned in `manifest.json` (dependency-transparency)
2. **`custom_components/brilliant_kiosk`**: the integration, one config entry per panel.
3. **`addon/brilliant_admin`**: a thin ingress UI for bulk env and bus editing. It calls the
   integration's services and websocket commands and never talks SSH itself.

## Integration entities (per panel device; reuse the MQTT device via identifiers where present)
- sensor: version, uptime, load, memory, backlight (diagnostic)
- binary_sensor: one per kiosk unit (running), plus connectivity
- update: `update.<panel>_kiosk` with stable/nightly channel option, release notes and install
- button: restart kiosk, restart voice, reboot, wake screen
- number: day level, dim level, min level (written to kiosk.env, followed by a kiosk restart)
- select: release channel; switch: screensaver, smart dim, motion wake (env-backed)
- HA helpers: the integration doesn't create entities for them. The add-on UI manages
  `input_*`/`counter`/`timer` helpers through HA's own websocket helper APIs.

## Services (with translations and typed `ServiceValidationError`)
`set_env`, `get_env` (response), `bus_get` (response), `bus_set`, `logs` (response), `install_version`.

## Platinum checklist (quality_scale.yaml tracked in the repo)
- Bronze: config flow, unique ids, has-entity-name, runtime-data, test-before-configure,
  test-before-setup, appropriate polling (60 s coordinator), entity-event-setup, action-setup,
  docs (installation, removal, actions, high-level description), brands, common-modules,
  config-flow-test-coverage, dependency-transparency
- Silver: action-exceptions, config-entry-unloading, entity-unavailable, log-when-unavailable,
  parallel-updates, reauthentication-flow, integration-owner, test-coverage ≥95%, docs
  (configuration parameters, installation parameters)
- Gold: devices, diagnostics (redacted), discovery (zeroconf/DHCP on Brilliant MAC OUI, or a
  documented exemption), discovery-update-info, entity-category, entity-device-class,
  entity-disabled-by-default, entity-translations, exception-translations, icon-translations,
  reconfiguration-flow, repair-issues (host key changed, install failed, env drift), stale-devices,
  dynamic-devices (exempt: one device per entry), docs (data-update, examples, known limitations,
  supported devices/functions, troubleshooting, use cases)
- Platinum: async-dependency (asyncssh and aiohttp), inject-websession (HA's
  `async_get_clientsession` passed to `Releases`), strict-typing (`.strict-typing`, mypy strict in CI)

## Tests and CI
pytest-homeassistant-custom-component, with the library mocked at its boundary; ≥95% coverage of
the integration and the library. GitHub Actions runs ruff, mypy --strict, pytest and hassfest, plus
HACS validation.

## Open questions
1. Env drift: the env written by `scripts/install.sh` from `.private/` overwrites edits made in HA.
   Proposal: a repair issue shows the drift, and "export overrides" writes `panelN.kiosk.env` lines.
2. PyPI publishing of `pybrilliantkiosk` under your account (needed for dependency-transparency).
3. A Brilliant MAC OUI or another discovery signal, or the panels' MQTT discovery as the trigger.

## Build order
P1 library + tests; P2 integration Bronze; P3 Silver; P4 Gold + add-on UI; P5 Platinum typing/CI, docs.
