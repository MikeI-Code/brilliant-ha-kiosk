#!/bin/sh
# Install, update or remove the HA kiosk ON THE PANEL, from an unpacked release bundle.
# Non-interactive and idempotent, so other tools (fleet managers, HA integrations) can drive it.
#
#   install-local.sh [--env FILE] [--token FILE] [--layout FILE] [--no-mqtt] [--no-restart]
#   install-local.sh --uninstall [--purge]
#   install-local.sh --version
#
#   --env FILE     kiosk.env to install (default: keep the panel's current /etc/ha-kiosk/kiosk.env)
#   --token FILE   Home Assistant long-lived token (default: keep the current one)
#   --layout FILE  local layout.json fallback (default: keep current, else the bundled example)
#   --no-mqtt      set KIOSK_MQTT=0: don't publish the panel over MQTT (another tool already does)
#   --no-restart   install files and units but don't (re)start anything
#   --uninstall    stop the kiosk and give the screen back to Brilliant's own UI
#   --purge        with --uninstall: also delete all kiosk files, settings, photos and the swap file
#
# Exit status 0 on success. Output is plain log lines.
set -eu

BUNDLE="$(cd "$(dirname "$0")" && pwd)"
DEST=/etc/ha-kiosk
STATE=/var/lib/ha-kiosk
UNITS="ha-kiosk.service ha-kiosk-display.service ha-kiosk-bridge.service ha-kiosk-swap.service ha-kiosk-voice.service ha-kiosk-intercom.service"
DROPIN=/etc/systemd/system/message_bus.service.d/ha-kiosk-display.conf
TMPFILES=/etc/tmpfiles.d/ha-kiosk.conf

log() { echo "ha-kiosk: $*"; }
die() { echo "ha-kiosk: error: $*" >&2; exit 1; }

ENV_FILE= TOKEN_FILE= LAYOUT_FILE= NO_MQTT=0 RESTART=1 ACTION=install PURGE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_FILE="$2"; shift ;;
        --token) TOKEN_FILE="$2"; shift ;;
        --layout) LAYOUT_FILE="$2"; shift ;;
        --no-mqtt) NO_MQTT=1 ;;
        --no-restart) RESTART=0 ;;
        --uninstall) ACTION=uninstall ;;
        --purge) PURGE=1 ;;
        --version) cat "$BUNDLE/VERSION" 2>/dev/null || echo unknown; exit 0 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) die "unknown option $1" ;;
    esac
    shift
done
[ "$(id -u)" = 0 ] || die "run as root"

uninstall() {
    log "stopping the kiosk"
    systemctl disable --now ha-kiosk.service ha-kiosk-display.service ha-kiosk-bridge.service ha-kiosk-voice.service ha-kiosk-intercom.service 2>/dev/null || true
    if [ -f "$DROPIN" ]; then
        rm -f "$DROPIN"; systemctl daemon-reload
        log "restarting Brilliant services (message_bus) to give the screen back"
        systemctl restart message_bus || true
    fi
    if [ "$PURGE" = 1 ]; then
        systemctl disable --now ha-kiosk-swap.service 2>/dev/null || true
        for u in $UNITS; do rm -f "/etc/systemd/system/$u"; done
        rm -rf /etc/systemd/system/ha-kiosk.service.d "$TMPFILES" /run/ha-kiosk-shadow
        rm -rf "$DEST" "$STATE" /var/lib/ha-kiosk-swap
        systemctl daemon-reload
        log "removed all kiosk files"
    fi
    systemctl reset-failed ha-kiosk.service ha-kiosk-display.service ha-kiosk-bridge.service 2>/dev/null || true
    systemctl enable --now switch_ui_app
    log "stock Brilliant UI restored"
}

install_files() {
    [ -f "$BUNDLE/ha-kiosk/native/main.qml" ] || die "bundle is incomplete (no ha-kiosk/native/main.qml next to this script)"
    if [ -f "$BUNDLE/MANIFEST.sha256" ]; then
        (cd "$BUNDLE" && sha256sum -c MANIFEST.sha256 >/dev/null) || die "bundle checksum mismatch"
    fi
    mkdir -p "$DEST/native" "$STATE" /etc/systemd/system/ha-kiosk.service.d /etc/systemd/system/message_bus.service.d
    # Code: replace what the bundle ships; keep the panel's own files (kiosk.env, token, layout, photos).
    rm -rf "$DEST/native.new"
    cp -r "$BUNDLE/ha-kiosk/native" "$DEST/native.new"
    [ -f "$DEST/native/layout.json" ] && cp "$DEST/native/layout.json" "$DEST/native.new/layout.json"
    rm -rf "$DEST/native.old"; [ -d "$DEST/native" ] && mv "$DEST/native" "$DEST/native.old"
    mv "$DEST/native.new" "$DEST/native"; rm -rf "$DEST/native.old"
    for f in "$BUNDLE"/ha-kiosk/*; do
        [ -f "$f" ] && cp "$f" "$DEST/"
    done
    chmod 755 "$DEST"/*.sh "$DEST"/*.py 2>/dev/null || true
    cp "$BUNDLE/VERSION" "$DEST/VERSION" 2>/dev/null || true
    cp "$0" "$DEST/install-local.sh" 2>/dev/null || true   # keep the uninstaller on the panel

    if [ -n "$ENV_FILE" ]; then cp "$ENV_FILE" "$DEST/kiosk.env"; fi
    [ -f "$DEST/kiosk.env" ] || cp "$BUNDLE/kiosk.env.example" "$DEST/kiosk.env"
    chmod 600 "$DEST/kiosk.env"                         # may hold the MQTT password
    if [ "$NO_MQTT" = 1 ]; then
        grep -v '^KIOSK_MQTT=' "$DEST/kiosk.env" > "$DEST/kiosk.env.tmp" || true
        echo "KIOSK_MQTT=0" >> "$DEST/kiosk.env.tmp"; mv "$DEST/kiosk.env.tmp" "$DEST/kiosk.env"; chmod 600 "$DEST/kiosk.env"
    fi
    if [ -n "$TOKEN_FILE" ]; then cp "$TOKEN_FILE" "$DEST/token"; chmod 600 "$DEST/token"; fi
    if [ -n "$LAYOUT_FILE" ]; then cp "$LAYOUT_FILE" "$DEST/native/layout.json"
    elif [ ! -f "$DEST/native/layout.json" ]; then cp "$DEST/native/layout.example.json" "$DEST/native/layout.json"; fi

    for u in $UNITS; do cp "$BUNDLE/systemd/$u" "/etc/systemd/system/$u"; done
    printf '[Service]\n' > /etc/systemd/system/ha-kiosk.service.d/native.conf   # neutralise an early-testing drop-in
    cp "$BUNDLE/systemd/ha-kiosk-tmpfiles.conf" "$TMPFILES"
    systemd-tmpfiles --create "$TMPFILES"
    DROPIN_CHANGED=0
    if ! cmp -s "$BUNDLE/systemd/message_bus-ha-kiosk.conf" "$DROPIN"; then
        cp "$BUNDLE/systemd/message_bus-ha-kiosk.conf" "$DROPIN"; DROPIN_CHANGED=1
    fi
    systemctl daemon-reload
    log "installed $(cat "$DEST/VERSION" 2>/dev/null || echo 'unknown version') to $DEST"
}

start() {
    # Stop Brilliant's own screen-off logic from fighting the kiosk (see message_bus-ha-kiosk.conf).
    if [ "$DROPIN_CHANGED" = 1 ]; then
        log "restarting Brilliant services (message_bus) to apply the display drop-in"
        systemctl restart message_bus
    fi
    systemctl disable --now switch_ui_app 2>/dev/null || true
    systemctl enable ha-kiosk-swap.service ha-kiosk-display.service ha-kiosk-bridge.service ha-kiosk.service
    systemctl restart ha-kiosk-swap.service ha-kiosk-bridge.service ha-kiosk.service ha-kiosk-display.service
    # Voice satellite / announcements: opt-in (mic + speaker), only with KIOSK_VOICE=1 in kiosk.env.
    # last assignment wins (kiosk.env = shared file + per-panel file), like systemd's EnvironmentFile
    envflag() { [ "$(sed -n "s/^$1=//p" "$DEST/kiosk.env" | tail -n 1 | tr -d '\"\047 ')" = 1 ]; }
    if envflag KIOSK_VOICE; then
        systemctl enable ha-kiosk-voice.service; systemctl restart ha-kiosk-voice.service
        log "voice satellite enabled (KIOSK_VOICE=1)"
    else
        systemctl disable --now ha-kiosk-voice.service 2>/dev/null || true
    fi
    # Intercom between panels: opt-in, KIOSK_INTERCOM=1 (receive); the mic also needs INTERCOM_MIC=1.
    if envflag KIOSK_INTERCOM; then
        systemctl enable ha-kiosk-intercom.service; systemctl restart ha-kiosk-intercom.service
        log "intercom enabled (KIOSK_INTERCOM=1$(envflag INTERCOM_MIC && echo ', mic on' || echo ', receive only'))"
    else
        systemctl disable --now ha-kiosk-intercom.service 2>/dev/null || true
    fi
    sleep 3
    systemctl is-active --quiet ha-kiosk.service || die "ha-kiosk.service did not start (journalctl -u ha-kiosk)"
    log "kiosk running"
}

case "$ACTION" in
    uninstall) uninstall ;;
    install) install_files; [ "$RESTART" = 1 ] && start || log "not restarted (--no-restart)" ;;
esac
