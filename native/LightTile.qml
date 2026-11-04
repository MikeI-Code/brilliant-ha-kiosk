import QtQuick 2.15
import "."

// Light tile. If the entity is this panel's own wired load (LOAD_ENTITY), it is controlled
// locally through the bridge (instant, works without HA) and shows a "This switch" marker.
Tile {
    id: t
    readonly property bool local: Panel.available && entity !== "" && entity === Panel.loadEntity
    readonly property bool on: local ? Panel.on : stateText === "on"
    readonly property real level: local ? Panel.level
        : (on && attrs.brightness !== undefined && attrs.brightness !== null ? attrs.brightness / 255 : (on ? 1 : 0))
    unavailable: local ? false : (!st || st.state === "unavailable" || st.state === "unknown")
    height: 104 * Theme.u
    onTapped: {
        if (local) Panel.toggle();
        else if (!unavailable) Ha.callService("light", "toggle", null, entity);
    }

    Rectangle {
        id: badge
        x: Theme.pad; y: Theme.pad
        width: 38 * Theme.u; height: width; radius: width / 2
        color: t.on ? Theme.amberSoft : Theme.track
        Icon { anchors.centerIn: parent; size: 22; name: t.icon || (t.on ? "lightbulb" : "lightbulb-outline"); color: t.on ? Theme.amber : Theme.textDim }
    }
    Column {
        anchors { left: badge.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad; verticalCenter: badge.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold }
        Label { width: parent.width; size: 12; color: Theme.textDim
                text: (t.unavailable ? "Unavailable" : (t.on ? "On · " + Math.round(t.level * 100) + "%" : "Off"))
                      + (t.local ? " · this switch" : "") }
    }
    Bar {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        value: t.level
        visible: !t.unavailable
        onCommitted: function (v) {
            if (t.local) Panel.setLevel(v);
            else if (v < 0.02) Ha.callService("light", "turn_off", null, t.entity);
            else Ha.callService("light", "turn_on", { brightness_pct: Math.round(v * 100) }, t.entity);
        }
    }
}
