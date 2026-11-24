#!/usr/bin/env bash
# Remove the optional SSH guard from a Brilliant panel. This removes the guard only; it does not
# disable SSH itself (your current authorized_keys / sshd settings are left in place).
# Usage: scripts/uninstall-ssh-guard.sh [panel]   (default panel1, or "all")
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PANEL="${1:-${PANEL:-panel1}}"

if [ "$PANEL" = all ]; then
  for f in "$ROOT"/.private/*.env; do
    n="$(basename "$f" .env)"
    case "$n" in *.kiosk|*.group|kiosk|.*) continue ;; esac
    grep -q '^PANEL_HOST=' "$f" || continue
    echo "=== $n"; "$0" "$n"
  done
  exit 0
fi

PANEL="$PANEL" . "$ROOT/scripts/_remote.sh"
remote "systemctl disable --now ha-ssh-guard.timer ha-ssh-guard.service 2>/dev/null; \
  rm -f /etc/systemd/system/ha-ssh-guard.service /etc/systemd/system/ha-ssh-guard.timer; \
  systemctl daemon-reload 2>/dev/null; \
  rm -rf /var/lib/ha-ssh-guard; true"
echo "SSH guard removed from $PANEL (SSH itself left as-is)."
