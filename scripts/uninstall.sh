#!/usr/bin/env bash
# Restore the stock Brilliant UI. Leaves /etc/ha-kiosk in place. Usage: scripts/uninstall.sh [panel]
set -euo pipefail
PANEL="${1:-${PANEL:-panel1}}"
. "$(dirname "$0")/_remote.sh"
# Hand the screen back to Brilliant: neutralise the display drop-in (empty [Service]) and restart its services.
remote 'systemctl disable --now ha-kiosk ha-kiosk-display;
        D=/etc/systemd/system/message_bus.service.d/ha-kiosk-display.conf
        [ -f "$D" ] && printf "[Service]\n" > "$D" && systemctl daemon-reload && systemctl restart message_bus;
        systemctl enable --now switch_ui_app && systemctl is-active switch_ui_app'
