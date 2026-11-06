import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property bool on: stateText === "on"
    height: 70 * Theme.u
    color: pressed ? Theme.cardPressed : (on && !unavailable ? Theme.onTint : Theme.card)
    onTapped: if (!unavailable) sent(Ha.callService("homeassistant", "toggle", null, entity))

    Rectangle {
        id: badge
        x: Theme.pad; anchors.verticalCenter: parent.verticalCenter
        width: 38 * Theme.u; height: width; radius: width / 2
        color: t.on ? Theme.amberSoft : Theme.track
        Icon { anchors.centerIn: parent; size: 22; name: t.icon || "power"; color: t.on ? Theme.amber : Theme.textDim }
    }
    Column {
        anchors { left: badge.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad; verticalCenter: parent.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
        Label { width: parent.width; size: 14; color: t.on ? Theme.amber : Theme.textDim; text: t.unavailable ? "Unavailable" : (t.on ? "On" : "Off") }
    }
}
