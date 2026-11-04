#!/usr/bin/env bash
# Install or update the HA panel UI on a Brilliant panel from this checkout.
# Builds the release bundle, copies it and your private config to the panel, and runs the bundle's
# install-local.sh there (the same installer other tools use).
# Usage: scripts/install.sh [panel]   (panel name = .private/<panel>.env, default panel1)
set -euo pipefail
PANEL="${1:-${PANEL:-panel1}}"
. "$(dirname "$0")/_remote.sh"
[ -f "$PRIVATE/kiosk.env" ] || { echo "Create $PRIVATE/kiosk.env from config/kiosk.env.example first"; exit 1; }
LAYOUT="$PRIVATE/layout.json"; [ -f "$LAYOUT" ] || LAYOUT="$ROOT/native/layout.example.json"
BUNDLE="$("$ROOT/scripts/build-bundle.sh" | tail -1)"
NAME="$(basename "$BUNDLE" .tar.gz)"
echo "Installing $NAME to $PANEL ($PANEL_HOST), layout: ${LAYOUT#"$ROOT"/}"

# kiosk.env = shared .private/kiosk.env + this panel's .private/<panel>.kiosk.env (e.g. ROOM_LIGHTS)
TMPENV="$PRIVATE/.kiosk.env.$PANEL.tmp"; trap 'rm -f "$TMPENV"' EXIT
cat "$PRIVATE/kiosk.env" $( [ -f "$PRIVATE/$PANEL.kiosk.env" ] && echo "$PRIVATE/$PANEL.kiosk.env" ) > "$TMPENV"

W=/tmp/ha-kiosk-install
remote "rm -rf $W && mkdir -p $W && chmod 700 $W"
push "$BUNDLE" "$W/bundle.tar.gz"
push "$TMPENV" "$W/kiosk.env"
push "$LAYOUT" "$W/layout.json"
ARGS="--env $W/kiosk.env --layout $W/layout.json"
if [ -f "$PRIVATE/token" ]; then push "$PRIVATE/token" "$W/token"; ARGS="$ARGS --token $W/token"; fi

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

remote "cd $W && tar -xzf bundle.tar.gz && sh $NAME/install-local.sh $ARGS; rc=\$?; rm -rf $W; exit \$rc"
remote 'systemctl --no-pager status ha-kiosk | head -5'
