// Read-only sensor (type "sensor") or binary sensor (type "binary").
import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property bool binary: cfg.type === "binary"
    readonly property bool active: binary && stateText === "on"
    height: 72 * Theme.u
    tapArea.enabled: false

    function valueText() {
        if (unavailable) return "Unavailable";
        if (binary) {
            var dc = attrs.device_class || "";
            if (dc === "door" || dc === "window" || dc === "opening" || dc === "garage_door") return active ? "Open" : "Closed";
            return active ? "Detected" : "Clear";
        }
        var v = parseFloat(stateText);
        var s = isNaN(v) ? stateText : (Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10).toString();
        return s + (attrs.unit_of_measurement ? (attrs.unit_of_measurement.charAt(0) === "°" || attrs.unit_of_measurement === "%" ? "" : " ") + attrs.unit_of_measurement : "");
    }

    Icon {
        id: ic
        x: Theme.pad; anchors.verticalCenter: parent.verticalCenter
        size: 22; name: t.icon || "eye"
        color: t.active ? Theme.amber : Theme.textDim
    }
    Column {
        anchors { left: ic.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad / 2; verticalCenter: parent.verticalCenter }
        Label { width: parent.width; text: t.name; size: 13; color: Theme.textDim; elide: Text.ElideRight }
        Label { width: parent.width; text: t.valueText(); size: t.unavailable ? 14 : 19; elide: Text.ElideRight; font.weight: Font.DemiBold; color: t.active ? Theme.amber : Theme.text }
    }
}
