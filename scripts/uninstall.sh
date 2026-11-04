#!/usr/bin/env bash
# Restore the stock Brilliant UI.
# Usage: scripts/uninstall.sh [panel] [--purge]   (--purge also deletes all kiosk files and settings)
set -euo pipefail
PANEL="${1:-${PANEL:-panel1}}"
PURGE="${2:-}"
. "$(dirname "$0")/_remote.sh"
remote "if [ -x /etc/ha-kiosk/install-local.sh ]; then sh /etc/ha-kiosk/install-local.sh --uninstall $PURGE;
        else echo 'Older install: run scripts/install.sh $PANEL once, then uninstall again'; exit 1; fi"
