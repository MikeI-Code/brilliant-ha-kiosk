#!/usr/bin/env bash
# Build the release bundle: dist/brilliant-ha-kiosk-<version>.tar.gz (+ .sha256).
# Unpack it on a panel and run ./install-local.sh (see kiosk/install-local.sh for options).
# Usage: scripts/build-bundle.sh [version]   (default: git describe, e.g. v1.1.0 or v1.0.0-3-gabc123)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null || echo dev)}"
NAME="brilliant-ha-kiosk-$VERSION"
OUT="$ROOT/dist"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
B="$STAGE/$NAME"

mkdir -p "$B/ha-kiosk/native/fonts" "$B/systemd" "$OUT"
cp "$ROOT"/native/*.qml "$ROOT"/native/*.js "$ROOT"/native/icons.json "$ROOT"/native/qmldir \
   "$ROOT"/native/layout.example.json "$B/ha-kiosk/native/"
cp "$ROOT"/native/fonts/*.ttf "$B/ha-kiosk/native/fonts/"
cp "$ROOT"/kiosk/kiosk.qml "$ROOT"/kiosk/ha-kiosk-*.sh "$ROOT"/kiosk/ha-kiosk-*.py "$B/ha-kiosk/"
cp "$ROOT"/tools/brilliant_bus.py "$ROOT"/tools/inject_touch.py "$B/ha-kiosk/"
cp "$ROOT"/systemd/* "$B/systemd/"
cp "$ROOT"/kiosk/install-local.sh "$B/install-local.sh"
cp "$ROOT"/config/kiosk.env.example "$B/kiosk.env.example"
cp "$ROOT"/LICENSE "$B/"
echo "$VERSION" > "$B/VERSION"
chmod 755 "$B/install-local.sh" "$B"/ha-kiosk/*.sh "$B"/ha-kiosk/*.py
(cd "$B" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort | xargs sha256sum > MANIFEST.sha256)

tar -C "$STAGE" -czf "$OUT/$NAME.tar.gz" "$NAME"
(cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "$OUT/$NAME.tar.gz"
