#!/usr/bin/env bash
# Install the optional SSH keep-alive guard on a Brilliant panel.
#
# This is separate from the kiosk UI. It keeps the panel reachable over SSH across Brilliant
# firmware updates, which can otherwise reset the "Root SSH Login" state and lock you out. The
# guard installs into /var/lib/ha-ssh-guard (survives updates) plus a systemd unit + timer in
# /etc, and re-asserts the SSH enable state on every boot. Opt-in; undo with uninstall-ssh-guard.sh.
#
# Usage: scripts/install-ssh-guard.sh [panel] [path/to/authorized_keys]
#   panel            .private/<panel>.env (default panel1), or "all" for every panel file
#   authorized_keys  public key(s) to install for key-based root login. If omitted, uses
#                    .private/ssh-guard.authorized_keys when present; otherwise the guard keeps
#                    your existing access working and only maintains the enable state.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PANEL="${1:-${PANEL:-panel1}}"
KEYS="${2:-}"

if [ "$PANEL" = all ]; then
  found=0; failed=""
  for f in "$ROOT"/.private/*.env; do
    n="$(basename "$f" .env)"
    case "$n" in *.kiosk|*.group|kiosk|.*) continue ;; esac
    grep -q '^PANEL_HOST=' "$f" || continue
    found=1
    echo "=== $n"
    "$0" "$n" "$KEYS" || failed="$failed $n"
  done
  [ "$found" = 1 ] || { echo "No panel files found in $ROOT/.private"; exit 1; }
  [ -z "$failed" ] || { echo "Failed:$failed"; exit 1; }
  exit 0
fi

PANEL="$PANEL" . "$ROOT/scripts/_remote.sh"
[ -n "$KEYS" ] || { [ -f "$ROOT/.private/ssh-guard.authorized_keys" ] && \
  KEYS="$ROOT/.private/ssh-guard.authorized_keys"; } || true

GUARD_DIR=/var/lib/ha-ssh-guard
echo "Installing SSH guard on $PANEL ($PANEL_HOST)"

remote "mkdir -p $GUARD_DIR && chmod 700 $GUARD_DIR"
push "$ROOT/ssh-guard/ha-ssh-guard.sh" "$GUARD_DIR/ha-ssh-guard.sh"
remote "chmod 700 $GUARD_DIR/ha-ssh-guard.sh"

if [ -n "$KEYS" ] && [ -f "$KEYS" ]; then
  push "$KEYS" "$GUARD_DIR/authorized_keys"
  remote "chmod 600 $GUARD_DIR/authorized_keys"
  echo "  key-based root login from: $(basename "$KEYS")"
else
  echo "  no key provided: keeping existing access, maintaining enable-state only"
fi

# Optional static dropbear fallback (used only if a future update removes OpenSSH's server).
if [ -f "$ROOT/native/dropbear-armv7" ]; then
  push "$ROOT/native/dropbear-armv7" "$GUARD_DIR/dropbear"
  remote "chmod 700 $GUARD_DIR/dropbear"
  echo "  dropbear fallback bundled"
fi

push "$ROOT/ssh-guard/ha-ssh-guard.service" /etc/systemd/system/ha-ssh-guard.service
push "$ROOT/ssh-guard/ha-ssh-guard.timer"   /etc/systemd/system/ha-ssh-guard.timer
remote "systemctl daemon-reload \
  && systemctl enable --now ha-ssh-guard.service ha-ssh-guard.timer \
  && $GUARD_DIR/ha-ssh-guard.sh"

echo "SSH guard installed on $PANEL."
