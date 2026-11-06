// Cover / valve tile: badge + name + status, a row of open / stop / close buttons, and a
// position bar when the entity supports it. Garage doors and gates need two taps to open/close.
//
// supported_features bits (HA CoverEntityFeature / ValveEntityFeature; same low bits for both):
//   OPEN = 1, CLOSE = 2, SET_POSITION = 4, STOP = 8
// (cover also has tilt bits 16..128, unused here). If supported_features is missing we assume
// open + close + stop so the tile stays usable.
import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property string domain: entity.split(".")[0] === "valve" ? "valve" : "cover"
    readonly property string devClass: attrs.device_class || (domain === "valve" ? "valve" : "")
    readonly property bool guarded: devClass === "garage" || devClass === "gate"
    readonly property int features: attrs.supported_features !== undefined ? attrs.supported_features : 11
    readonly property bool canOpen: (features & 1) !== 0
    readonly property bool canClose: (features & 2) !== 0
    readonly property bool canSetPos: (features & 4) !== 0
    readonly property bool canStop: (features & 8) !== 0
    readonly property var pos: attrs.current_position
    readonly property bool hasPos: typeof pos === "number" && !isNaN(pos)
    readonly property bool showBar: !guarded && (canSetPos && hasPos)   // no drag-to-move for garage doors/gates
    readonly property bool on: !unavailable && stateText !== "closed"
    readonly property bool moving: stateText === "opening" || stateText === "closing"

    property string armed: ""            // "open" / "close" while waiting for the confirming tap
    Timer { id: disarm; interval: 3000; onTriggered: t.armed = "" }

    height: (showBar ? 170 : 120) * Theme.u
    color: pressed ? Theme.cardPressed : (on && !unavailable ? Theme.onTint : Theme.card)

    function iconFor() {
        if (t.icon) return t.icon;
        var open = stateText !== "closed";
        if (devClass === "garage" || devClass === "gate") return open ? "garage-open" : "garage";
        if (devClass === "door") return open ? "door-open" : "door";
        if (devClass === "blind" || devClass === "shade" || devClass === "curtain") return open ? "blinds-open" : "blinds";
        if (devClass === "valve" || domain === "valve") return open ? "valve-open" : "valve-closed";
        return open ? "window-shutter-open" : "window-shutter";
    }
    function statusText() {
        if (unavailable) return "Unavailable";
        if (armed === "open") return "Tap again to open";
        if (armed === "close") return "Tap again to close";
        var s = { open: "Open", closed: "Closed", opening: "Opening…", closing: "Closing…" }[stateText]
                || (stateText.charAt(0).toUpperCase() + stateText.slice(1));
        return hasPos ? s + " · " + Math.round(pos) + "%" : s;
    }
    function act(a) {                    // a: "open" | "stop" | "close"
        if (unavailable) return;
        if (guarded && a !== "stop") {
            if (armed !== a) { armed = a; disarm.restart(); return; }
        }
        armed = "";
        sent(Ha.callService(domain, a + "_" + domain, null, entity));
    }

    Rectangle {
        id: badge
        x: Theme.pad; y: Theme.pad
        width: 38 * Theme.u; height: width; radius: width / 2
        color: t.on ? Theme.amberSoft : Theme.track
        Icon { anchors.centerIn: parent; size: 22; name: t.iconFor(); color: t.on ? Theme.amber : Theme.textDim }
    }
    Column {
        anchors { left: badge.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad; verticalCenter: badge.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
        Label { width: parent.width; size: 14; elide: Text.ElideRight
                color: t.armed ? Theme.red : (t.on ? Theme.amber : Theme.textDim); text: t.statusText() }
    }

    Row {
        id: buttons
        anchors { left: parent.left; right: parent.right; top: badge.bottom; margins: Theme.pad; topMargin: 10 * Theme.u }
        spacing: 8 * Theme.u
        readonly property var acts: {
            var l = [];
            if (t.canOpen) l.push("open");
            if (t.canStop) l.push("stop");
            if (t.canClose) l.push("close");
            return l;
        }
        Repeater {
            model: buttons.acts
            Rectangle {
                width: (buttons.width - (buttons.acts.length - 1) * buttons.spacing) / buttons.acts.length
                height: 44 * Theme.u; radius: 12 * Theme.u
                readonly property bool isArmed: t.armed === modelData
                color: isArmed ? "#3a1f1f" : (bm.pressed ? Theme.cardPressed : Theme.track)
                Icon {
                    anchors.centerIn: parent; size: 20
                    name: modelData === "open" ? "arrow-up" : modelData === "close" ? "arrow-down" : "stop"
                    color: parent.isArmed ? Theme.red : (t.unavailable ? Theme.textFaint : Theme.text)
                }
                MouseArea { id: bm; anchors.fill: parent; onClicked: if (!Panel.multi) t.act(modelData) }
            }
        }
    }

    Bar {
        visible: t.showBar
        anchors { left: parent.left; right: parent.right; top: buttons.bottom; margins: Theme.pad; topMargin: 10 * Theme.u }
        value: t.hasPos ? Math.max(0, Math.min(1, t.pos / 100)) : 0
        onCommitted: function (v) {
            if (t.unavailable) return;
            var p = Math.round(v * 100);
            if (t.domain === "valve") t.sent(Ha.callService("valve", "set_valve_position", { position: p }, t.entity));
            else t.sent(Ha.callService("cover", "set_cover_position", { position: p }, t.entity));
        }
    }
}
