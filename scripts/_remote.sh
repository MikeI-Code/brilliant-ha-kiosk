#!/usr/bin/env bash
# Shared helper: run a command on a panel. Uses plink (password) if present, else ssh (keys).
# Panel settings come from .private/<PANEL>.env (default PANEL=panel1); scripts take the panel
# name as their first argument, e.g. scripts/install.sh panel2.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRIVATE="${PRIVATE_DIR:-$ROOT/.private}"
PANEL="${PANEL:-panel1}"
if [ -f "$PRIVATE/$PANEL.env" ]; then set -a; . "$PRIVATE/$PANEL.env"; set +a
else echo "No $PRIVATE/$PANEL.env (copy config/panel.env.example)" >&2; exit 1; fi
: "${PANEL_HOST:?set PANEL_HOST in $PRIVATE/$PANEL.env}"; PANEL_USER="${PANEL_USER:-root}"
remote() {
  if command -v plink >/dev/null 2>&1 && [ -n "${PANEL_PASSWORD:-}" ]; then
    plink -ssh -batch ${PANEL_HOSTKEY:+-hostkey "$PANEL_HOSTKEY"} -pw "$PANEL_PASSWORD" "$PANEL_USER@$PANEL_HOST" "$1"
  else
    ssh "$PANEL_USER@$PANEL_HOST" "$1"
  fi
}
push() { remote "cat > '$2'" < "$1"; }   # push <local> <remote-path>
