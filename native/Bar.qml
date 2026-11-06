// Horizontal drag bar (brightness / volume). Emits committed(value 0..1) on release, but only
// after a real drag: a plain tap does nothing, so a brush against a tile can't change a light.
import QtQuick 2.15
import "."

Rectangle {
    id: bar
    property real value: 0              // 0..1 from HA
    property color fill: Theme.amber
    property bool dragging: drag.pressed
    property real _live: 0
    // After release, keep showing where the finger left the bar until HA reports the new value
    // (or 3 s pass), so the fill doesn't snap back to the old level and then forward again.
    property bool holding: false
    readonly property real shown: (dragging || holding) ? _live : value
    onValueChanged: holding = false
    Timer { id: holdTimer; interval: 3000; onTriggered: bar.holding = false }
    signal committed(real v)

    height: 40 * Theme.u
    radius: 10 * Theme.u
    color: Theme.track

    Rectangle {
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        width: Math.max(parent.radius * 2, parent.width * bar.shown)
        radius: parent.radius
        color: Qt.rgba(bar.fill.r, bar.fill.g, bar.fill.b, 0.9)
        visible: bar.shown > 0
    }
    Label {
        anchors { right: parent.right; rightMargin: 10 * Theme.u; verticalCenter: parent.verticalCenter }
        size: 12
        color: Theme.text
        text: Math.round(bar.shown * 100) + "%"
    }
    MouseArea {
        id: drag
        anchors.fill: parent
        preventStealing: true
        property real x0: 0
        property bool moved: false
        function at(x) { return Math.max(0, Math.min(1, x / bar.width)); }
        onPressed: { x0 = mouse.x; moved = false; bar._live = at(mouse.x); }
        onPositionChanged: {
            bar._live = at(mouse.x);
            if (Math.abs(mouse.x - x0) > 8 * Theme.u) moved = true;
        }
        // Only a real drag changes the level; a plain tap is ignored.
        onReleased: if (moved && !Panel.multi) { bar.holding = true; holdTimer.restart(); bar.committed(bar._live); }
    }
}
