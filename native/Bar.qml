// Horizontal drag bar (brightness / volume). Emits committed(value 0..1) on release.
import QtQuick 2.15
import "."

Rectangle {
    id: bar
    property real value: 0              // 0..1 from HA
    property color fill: Theme.amber
    property bool dragging: drag.pressed
    property real _live: 0
    signal committed(real v)

    height: 30 * Theme.u
    radius: 10 * Theme.u
    color: Theme.track
    clip: true

    Rectangle {
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        width: Math.max(parent.radius * 2, parent.width * (bar.dragging ? bar._live : bar.value))
        radius: parent.radius
        color: bar.fill
        opacity: (bar.dragging ? bar._live : bar.value) > 0 ? 0.9 : 0
    }
    Label {
        anchors { right: parent.right; rightMargin: 10 * Theme.u; verticalCenter: parent.verticalCenter }
        size: 12
        color: Theme.text
        text: Math.round((bar.dragging ? bar._live : bar.value) * 100) + "%"
    }
    MouseArea {
        id: drag
        anchors.fill: parent
        preventStealing: true
        function at(x) { return Math.max(0, Math.min(1, x / bar.width)); }
        onPressed: bar._live = at(mouse.x)
        onPositionChanged: bar._live = at(mouse.x)
        onReleased: if (!Panel.multi) bar.committed(bar._live)
    }
}
