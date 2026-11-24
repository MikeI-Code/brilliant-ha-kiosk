# SSH guard (optional)

Keeps a Brilliant panel reachable over SSH **across firmware updates**. Brilliant's updates
replace the system partition and can reset the "Root SSH Login" state — the guard quietly
restores it on every boot so you don't get locked out of a panel you manage.

It is **opt-in and separate from the kiosk UI.** Installing the kiosk does not install this.

## What it does

On each boot (and every 30 min):

1. Restores your SSH key into `root`'s `authorized_keys`.
2. Re-enables the SSH service if an update turned it off.
3. Re-allows root login if an update reset the SSH config to defaults.
4. If an update ever removes OpenSSH entirely, starts a bundled fallback SSH server (optional).

Everything persistent lives in `/var/lib/ha-ssh-guard`, which updates don't touch. It is
**fail-safe**: every step is best-effort and it never blocks or fails the boot.

## Install

From a checkout, with the same `.private/<panel>.env` files the kiosk installer uses:

```sh
# recommended: install your public key so access is key-based (not password)
scripts/install-ssh-guard.sh panel1 ~/.ssh/id_ed25519.pub

# all panels at once
scripts/install-ssh-guard.sh all ~/.ssh/id_ed25519.pub

# without a key: keeps your current access, just maintains the enable-state
scripts/install-ssh-guard.sh panel1
```

You can also drop a key at `.private/ssh-guard.authorized_keys` and it's picked up automatically.

## Remove

```sh
scripts/uninstall-ssh-guard.sh panel1    # or: all
```

This removes the guard only. It does **not** disable SSH — your current access stays as-is.

## Optional: fallback SSH server

The guard's steps 1–3 cover the realistic case (an update resetting the enable state). Step 4 is
belt-and-suspenders for the unlikely case where an update deletes the OpenSSH server binary. To
enable it, put a static ARM (`armv7`) `dropbear` binary at `native/dropbear-armv7` before
installing; the installer bundles it and the guard only uses it if OpenSSH ever goes missing
(fallback port `2222`, override with `HA_SSH_GUARD_PORT`). Without the binary, steps 1–3 still
work.

## Files

| Path (on panel) | Purpose |
|---|---|
| `/var/lib/ha-ssh-guard/ha-ssh-guard.sh` | the guard (persistent) |
| `/var/lib/ha-ssh-guard/authorized_keys` | your key (persistent) |
| `/var/lib/ha-ssh-guard/dropbear` | optional fallback server |
| `/etc/systemd/system/ha-ssh-guard.service` | runs the guard at boot |
| `/etc/systemd/system/ha-ssh-guard.timer` | re-checks every 30 min |

## Wiring it into the Home Assistant integration (todo)

To offer this as a checkbox during setup instead of a script, the integration can push the same
files over its existing SSH connection — no new transport needed:

- Add a `Panel.install_ssh_guard(authorized_keys: str)` method in
  `lib/pybrilliantkiosk/src/pybrilliantkiosk/panel.py` that uses the existing private
  `_write(...)` (file push) and `_run(...)` (command) helpers to perform the steps in
  `scripts/install-ssh-guard.sh`.
- Expose it as an **admin service** `brilliant_kiosk.install_ssh_guard` (see `services.py` /
  `services.yaml`), or an options-flow boolean, so it is explicitly user-triggered and never part
  of the kiosk UI.

Kept out of this change on purpose: it spans the library, the integration services, and their
translations, and should be reviewed and tested as its own PR.
