#!/usr/bin/env bash
# Install or update the HA panel UI on a Brilliant panel from this checkout.
# Builds the release bundle, copies it and your private config to the panel, and runs the bundle's
# install-local.sh there (the same installer other tools use).
# Usage: scripts/install.sh [panel|all]   (panel name = .private/<panel>.env, default panel1;
#        all = every .private/*.env panel file, one after another)
set -euo pipefail
PANEL="${1:-${PANEL:-panel1}}"
if [ "$PANEL" = all ]; then
    PRIV="$(cd "$(dirname "$0")/.." && pwd)/.private"
    shift || true
    found=0; failed=""
    for f in "$PRIV"/*.env; do
        n="$(basename "$f" .env)"
        case "$n" in *.kiosk|*.group|kiosk|.*) continue ;; esac   # shared and per-panel settings files
        grep -q '^PANEL_HOST=' "$f" || continue
        found=1
        echo "=== $n"
        "$0" "$n" "$@" || failed="$failed $n"
    done
    [ "$found" = 1 ] || { echo "No panel files (.private/<panel>.env with PANEL_HOST) found"; exit 1; }
    [ -z "$failed" ] || { echo "Failed:$failed"; exit 1; }
    exit 0
fi
. "$(dirname "$0")/_remote.sh"
[ -f "$PRIVATE/kiosk.env" ] || { echo "Create $PRIVATE/kiosk.env from config/kiosk.env.example first"; exit 1; }
LAYOUT="$PRIVATE/layout.json"; [ -f "$LAYOUT" ] || LAYOUT="$ROOT/native/layout.example.json"
BUNDLE="$("$ROOT/scripts/build-bundle.sh" | tail -1)"
NAME="$(basename "$BUNDLE" .tar.gz)"
echo "Installing $NAME to $PANEL ($PANEL_HOST), layout: ${LAYOUT#"$ROOT"/}"

# kiosk.env layers, later wins: shared .private/kiosk.env -> .private/<group>.group.env (when the
# panel file .private/<panel>.kiosk.env sets PANEL_GROUP=<group>) -> .private/<panel>.kiosk.env
TMPENV="$PRIVATE/.kiosk.env.$PANEL.tmp"; trap 'rm -f "$TMPENV"' EXIT
clean() { tr -d '"\047 \r'; }
lastval() { sed -n "s/^$1=//p" "$2" 2>/dev/null | tail -n 1 | clean; }
PANELENV="$PRIVATE/$PANEL.kiosk.env"; [ -f "$PANELENV" ] || PANELENV=""
GROUP=""; [ -n "$PANELENV" ] && GROUP="$(lastval PANEL_GROUP "$PANELENV")"
GROUPENV=""
if [ -n "$GROUP" ]; then
    case "$GROUP" in *[!a-z0-9-]*) echo "PANEL_GROUP=$GROUP: use only a-z, 0-9 and -"; exit 1 ;; esac
    if [ -f "$PRIVATE/$GROUP.group.env" ]; then GROUPENV="$PRIVATE/$GROUP.group.env"
    else echo "note: group $GROUP has no .private/$GROUP.group.env (panel settings only)"; fi
    echo "group: $GROUP"
fi
isdeny() { case "$(echo "$1" | tr 'A-Z' 'a-z')" in 0|off|false|no) return 0 ;; esac; return 1; }
MIC_DENY=0; CAM_DENY=0
for f in "$PRIVATE/kiosk.env" $GROUPENV $PANELENV; do
    for v in $(sed -n 's/^ALLOW_MIC=//p' "$f" | clean); do isdeny "$v" && MIC_DENY=1; done
    for v in $(sed -n 's/^ALLOW_CAMERA=//p' "$f" | clean); do isdeny "$v" && CAM_DENY=1; done
done
{
    for f in "$PRIVATE/kiosk.env" $GROUPENV $PANELENV; do cat "$f"; [ -z "$(tail -c 1 "$f")" ] || echo; done
    # appended last so readers where the last assignment wins see the resolved group and any deny
    echo "PANEL_GROUP=$GROUP"
    [ "$MIC_DENY" = 1 ] && echo "ALLOW_MIC=0"
    [ "$CAM_DENY" = 1 ] && echo "ALLOW_CAMERA=0"
    true
} > "$TMPENV"
case "$MIC_DENY$CAM_DENY" in
    11) echo "privacy: mic blocked / camera blocked" ;;
    10) echo "privacy: mic blocked" ;;
    01) echo "privacy: camera blocked" ;;
esac

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
