// Select tile (select.* / input_select.*): name, current option, and either up to 4 option
// chips in a row (current one amber) or one chip that cycles to the next option on tap.
// Neither domain has supported_features bits; options come from attributes.options.
import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property string domain: entity.split(".")[0] === "input_select" ? "input_select" : "select"
    readonly property var options: attrs.options || []
    readonly property bool chips: options.length > 0 && options.length <= 4
    property string chosen: ""            // option tapped, shown until HA reports the new state
    readonly property string current: pending && chosen !== "" ? chosen : stateText
    onPendingChanged: if (!pending) chosen = ""
    height: 136 * Theme.u

    function pick(o) {
        if (unavailable || o === stateText) return;
        chosen = o;
        sent(Ha.callService(domain, "select_option", { option: o }, entity));
    }
    function next() {
        if (unavailable || options.length === 0) return;
        var i = options.indexOf(current);
        chosen = options[(i + 1) % options.length];
        sent(Ha.callService(domain, "select_next", { cycle: true }, entity));
    }

    Rectangle {
        id: badge
        x: Theme.pad; y: Theme.pad
        width: 38 * Theme.u; height: width; radius: width / 2
        color: t.unavailable ? Theme.track : Theme.amberSoft
        Icon { anchors.centerIn: parent; size: 22; name: t.icon || "form-select"; color: t.unavailable ? Theme.textDim : Theme.amber }
    }
    Column {
        anchors { left: badge.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad; verticalCenter: badge.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
        Label { width: parent.width; size: 14; color: t.unavailable ? Theme.textDim : Theme.amber
                text: t.unavailable ? "Unavailable" : t.current }
    }

    Row {
        id: chipRow
        visible: t.chips
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        spacing: 6 * Theme.u
        Repeater {
            model: t.chips ? t.options : []
            Rectangle {
                readonly property bool sel: modelData === t.current
                width: (chipRow.width - (t.options.length - 1) * chipRow.spacing) / t.options.length
                height: 44 * Theme.u; radius: 12 * Theme.u
                color: cm.pressed ? Theme.cardPressed : (sel ? Theme.amberSoft : Theme.track)
                Label { anchors.centerIn: parent; width: parent.width - 8 * Theme.u; horizontalAlignment: Text.AlignHCenter
                        size: 13; font.weight: sel ? Font.DemiBold : Font.Normal
                        color: sel ? Theme.amber : Theme.text; text: modelData }
                MouseArea { id: cm; anchors.fill: parent; onClicked: if (!Panel.multi) t.pick(modelData) }
            }
        }
    }

    Rectangle {
        visible: !t.chips
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        height: 44 * Theme.u; radius: 12 * Theme.u
        color: nm.pressed ? Theme.cardPressed : Theme.amberSoft
        Label { anchors { left: parent.left; right: nextIcon.left; margins: 12 * Theme.u; verticalCenter: parent.verticalCenter }
                size: 14; font.weight: Font.DemiBold; color: t.unavailable ? Theme.textDim : Theme.amber
                text: t.unavailable ? "Unavailable" : t.current }
        Icon { id: nextIcon; anchors { right: parent.right; rightMargin: 12 * Theme.u; verticalCenter: parent.verticalCenter }
               size: 20; name: "chevron-right"; color: Theme.text }
        MouseArea { id: nm; anchors.fill: parent; enabled: !t.chips; onClicked: if (!Panel.multi) t.next() }
    }
}
