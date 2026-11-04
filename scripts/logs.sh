#!/usr/bin/env bash
# Show recent kiosk logs from a panel. Usage: scripts/logs.sh [panel] [lines]
set -euo pipefail
PANEL="${1:-${PANEL:-panel1}}"
. "$(dirname "$0")/_remote.sh"
remote "journalctl -u ha-kiosk --no-pager -n ${2:-50}"
