// Camera tile: a still image from a Home Assistant camera, refreshed while the page is on screen.
// Tap for the full-screen view (Doorbell.qml).
import QtQuick 2.15
import "."

Tile {
    id: t
    height: (width - 2 * Theme.pad) * 9 / 16 + 2 * Theme.pad + 30 * Theme.u
    property int tick: 0
    readonly property string pic: attrs.entity_picture || ""      // carries an access token
    onTapped: Panel.showCamera(entity, name)

    // Refresh every 20 s, only while this tile is actually on screen.
    Timer { interval: 20000; repeat: true; running: t.visible && t.pic !== ""; onTriggered: t.tick++ }

    Rectangle {
        id: frame
        x: Theme.pad; y: Theme.pad
        width: parent.width - 2 * Theme.pad; height: width * 9 / 16
        radius: 10 * Theme.u; color: Theme.track
        Image {
            anchors.fill: parent
            fillMode: Image.PreserveAspectCrop; asynchronous: true; cache: false
            sourceSize.width: width
            source: t.pic && t.visible ? Ha.baseUrl + t.pic + (t.pic.indexOf("?") >= 0 ? "&" : "?") + "_=" + t.tick : ""
            visible: status === Image.Ready
        }
        Icon { anchors.centerIn: parent; size: 34; name: "cctv"; color: Theme.textDim; visible: !t.pic }
    }
    Row {
        anchors { left: parent.left; leftMargin: Theme.pad; top: frame.bottom; topMargin: 6 * Theme.u }
        spacing: 6 * Theme.u
        Icon { size: 18; name: t.icon || "cctv"; color: Theme.textDim; anchors.verticalCenter: parent.verticalCenter }
        Label { size: 14; font.weight: Font.DemiBold; text: t.name.replace(/\s+Live view$/i, ""); elide: Text.ElideRight
                width: t.width - 3 * Theme.pad - 24 * Theme.u }
    }
}
