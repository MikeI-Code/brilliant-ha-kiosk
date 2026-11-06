import QtQuick 2.15
import "."

// Fan / humidifier tile. Tap toggles; a drag bar sets speed (fan) or target humidity (humidifier).
// HA supported_features bits used:
//   fan:        SET_SPEED = 1  -> show the speed bar (fan.set_percentage; 0 % = fan.turn_off)
//               PRESET_MODE = 8 is ignored here on purpose: preset_modes go in the detail sheet later.
//   humidifier: no bit needed; the bar is always shown and maps 0..1 onto min_humidity..max_humidity
//               (humidifier.set_humidity). MODES = 1 is likewise left for the detail sheet.
// Without a bar the tile is the compact 70u switch layout.
Tile {
    id: t
    readonly property bool humid: entity.indexOf("humidifier.") === 0
    readonly property bool on: stateText === "on"
    readonly property int feats: attrs.supported_features || 0
    readonly property bool hasBar: !unavailable && (humid || (feats & 1) !== 0)
    readonly property real minH: attrs.min_humidity !== undefined ? attrs.min_humidity : 0
    readonly property real maxH: attrs.max_humidity !== undefined ? attrs.max_humidity : 100
    readonly property real pct: humid
        ? (attrs.humidity !== undefined && attrs.humidity !== null ? attrs.humidity : 0)
        : (on && attrs.percentage !== undefined && attrs.percentage !== null ? attrs.percentage : 0)
    readonly property real level: humid
        ? (maxH > minH ? Math.max(0, Math.min(1, (pct - minH) / (maxH - minH))) : 0)
        : pct / 100
    height: (hasBar ? 118 : 70) * Theme.u
    color: pressed ? Theme.cardPressed : (on && !unavailable ? Theme.onTint : Theme.card)
    onTapped: {
        if (unavailable) return;
        if (humid) sent(Ha.callService("humidifier", on ? "turn_off" : "turn_on", null, entity));
        else sent(Ha.callService("fan", "toggle", null, entity));
    }

    Rectangle {
        id: badge
        x: Theme.pad
        y: t.hasBar ? Theme.pad : (t.height - height) / 2
        width: 38 * Theme.u; height: width; radius: width / 2
        color: t.on ? Theme.amberSoft : Theme.track
        Icon { anchors.centerIn: parent; size: 22; color: t.on ? Theme.amber : Theme.textDim
               name: t.icon || (t.humid ? (t.on ? "air-humidifier" : "air-humidifier-off") : (t.on ? "fan" : "fan-off")) }
    }
    Column {
        anchors { left: badge.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad; verticalCenter: badge.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
        Label { width: parent.width; size: 14; color: t.on ? Theme.amber : Theme.textDim
                text: t.unavailable ? "Unavailable"
                      : (t.on ? ("On" + ((t.humid || (t.feats & 1)) ? " · " + Math.round(t.pct) + "%" : "")) : "Off") }
    }
    Bar {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        value: t.level
        visible: t.hasBar
        onCommitted: function (v) {
            if (t.humid) {
                var h = Math.round(t.minH + v * (t.maxH - t.minH));
                t.sent(Ha.callService("humidifier", "set_humidity", { humidity: h }, t.entity));
            } else {
                var p = Math.round(v * 100);
                if (p < 2) t.sent(Ha.callService("fan", "turn_off", null, t.entity));
                else t.sent(Ha.callService("fan", "set_percentage", { percentage: p }, t.entity));
            }
        }
    }
}
