#!/usr/bin/env bash
# Install the HA panel UI on a Brilliant panel and switch the screen over to it.
# Usage: scripts/install.sh [panel]   (panel name = .private/<panel>.env, default panel1)
set -euo pipefail
PANEL="${1:-${PANEL:-panel1}}"
. "$(dirname "$0")/_remote.sh"
[ -f "$PRIVATE/kiosk.env" ] || { echo "Create $PRIVATE/kiosk.env from config/kiosk.env.example first"; exit 1; }
LAYOUT="$PRIVATE/layout.json"; [ -f "$LAYOUT" ] || LAYOUT="$ROOT/native/layout.example.json"
echo "Installing to $PANEL ($PANEL_HOST), layout: ${LAYOUT#"$ROOT"/}"
remote 'mkdir -p /etc/ha-kiosk/native/fonts /var/lib/ha-kiosk /etc/systemd/system/ha-kiosk.service.d'
push "$ROOT/kiosk/kiosk.qml"            /etc/ha-kiosk/kiosk.qml
for f in "$ROOT"/native/*.qml "$ROOT"/native/*.js "$ROOT"/native/icons.json "$ROOT"/native/qmldir "$ROOT"/native/fonts/*.ttf; do
  push "$f" "/etc/ha-kiosk/native/${f#"$ROOT"/native/}"
done
push "$LAYOUT"                          /etc/ha-kiosk/native/layout.json
# kiosk.env = shared .private/kiosk.env + this panel's .private/<panel>.kiosk.env (e.g. ROOM_LIGHTS)
cat "$PRIVATE/kiosk.env" $( [ -f "$PRIVATE/$PANEL.kiosk.env" ] && echo "$PRIVATE/$PANEL.kiosk.env" ) > "$PRIVATE/.kiosk.env.$PANEL.tmp"
push "$PRIVATE/.kiosk.env.$PANEL.tmp"   /etc/ha-kiosk/kiosk.env
rm -f "$PRIVATE/.kiosk.env.$PANEL.tmp"
remote 'chmod 600 /etc/ha-kiosk/kiosk.env'     # may hold the MQTT password
push "$ROOT/systemd/ha-kiosk.service"   /etc/systemd/system/ha-kiosk.service
push "$ROOT/systemd/ha-kiosk-display.service" /etc/systemd/system/ha-kiosk-display.service
push "$ROOT/kiosk/ha-kiosk-display.sh"   /etc/ha-kiosk/ha-kiosk-display.sh
push "$ROOT/kiosk/ha-kiosk-swap.sh"      /etc/ha-kiosk/ha-kiosk-swap.sh
push "$ROOT/kiosk/ha-kiosk-ambient.py"   /etc/ha-kiosk/ha-kiosk-ambient.py
push "$ROOT/tools/brilliant_bus.py"      /etc/ha-kiosk/brilliant_bus.py
push "$ROOT/tools/inject_touch.py"       /etc/ha-kiosk/inject_touch.py
push "$ROOT/kiosk/ha-kiosk-bridge.py"    /etc/ha-kiosk/ha-kiosk-bridge.py
push "$ROOT/kiosk/ha-kiosk-mqtt.py"      /etc/ha-kiosk/ha-kiosk-mqtt.py
push "$ROOT/kiosk/ha-kiosk-photos.py"    /etc/ha-kiosk/ha-kiosk-photos.py
# Your own screensaver photos: mirror .private/photos/ to /etc/ha-kiosk/photos (only new or changed
# files are copied; photos you deleted locally are removed from the panel).
if [ -d "$PRIVATE/photos" ]; then
  remote 'mkdir -p /etc/ha-kiosk/photos'
  have=$(remote 'cd /etc/ha-kiosk/photos && for f in *; do [ -f "$f" ] && echo "$f $(stat -c %s "$f")"; done' || true)
  want=" "; sent=0
  for f in "$PRIVATE"/photos/*; do
    [ -f "$f" ] || continue
    case "$(echo "$f" | tr 'A-Z' 'a-z')" in *.jpg|*.jpeg|*.png) ;; *) continue ;; esac
    n=$(basename "$f" | tr -c 'A-Za-z0-9._\n-' '_')
    want="$want$n "
    if ! printf '%s\n' "$have" | grep -qxF "$n $(wc -c < "$f" | tr -d ' ')"; then
      push "$f" "/etc/ha-kiosk/photos/$n"; sent=$((sent + 1))
    fi
  done
  remote "cd /etc/ha-kiosk/photos && for f in *; do case \"$want\" in *\" \$f \"*) ;; *) [ -f \"\$f\" ] && rm -f -- \"\$f\" ;; esac; done; true"
  echo "Screensaver photos: $(echo $want | wc -w) in .private/photos ($sent copied)"
fi
push "$ROOT/systemd/ha-kiosk-bridge.service" /etc/systemd/system/ha-kiosk-bridge.service
push "$ROOT/systemd/ha-kiosk-swap.service" /etc/systemd/system/ha-kiosk-swap.service
# Neutralise the drop-in left by early testing (overwrite with an empty [Service] section).
remote 'printf "[Service]\n" > /etc/systemd/system/ha-kiosk.service.d/native.conf'
# HA long-lived token for auto-login
if [ -f "$PRIVATE/token" ]; then push "$PRIVATE/token" /etc/ha-kiosk/token; remote "chmod 600 /etc/ha-kiosk/token"; fi
# Stop Brilliant's hardware service from turning the screen off behind our back (see the drop-in).
remote 'mkdir -p /etc/systemd/system/message_bus.service.d'
push "$ROOT/systemd/ha-kiosk-tmpfiles.conf" /etc/tmpfiles.d/ha-kiosk.conf
push "$ROOT/systemd/message_bus-ha-kiosk.conf" /run/ha-kiosk-dropin.new
remote 'systemd-tmpfiles --create /etc/tmpfiles.d/ha-kiosk.conf
        D=/etc/systemd/system/message_bus.service.d/ha-kiosk-display.conf
        if ! cmp -s /run/ha-kiosk-dropin.new "$D"; then
          mv /run/ha-kiosk-dropin.new "$D"; systemctl daemon-reload
          echo "Restarting Brilliant services (message_bus) to apply the display drop-in"; systemctl restart message_bus
        fi'
remote 'systemctl daemon-reload &&
        systemctl disable --now switch_ui_app &&
        systemctl enable --now ha-kiosk-swap && systemctl restart ha-kiosk-swap && systemctl enable ha-kiosk-display ha-kiosk-bridge && systemctl enable ha-kiosk && systemctl restart ha-kiosk-bridge ha-kiosk ha-kiosk-display && sleep 5 && systemctl --no-pager status ha-kiosk | head -12'
