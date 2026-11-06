// Number tile (number.* / input_number.*): name, value + unit, and a drag bar over min..max
// (mode slider/auto) or debounced -/+ buttons (mode "box").
// Neither domain has supported_features bits; behaviour comes from attributes min/max/step/mode.
import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property string domain: entity.split(".")[0] === "input_number" ? "input_number" : "number"
    readonly property real minV: attrs.min !== undefined ? Number(attrs.min) : 0
    readonly property real maxV: attrs.max !== undefined ? Number(attrs.max) : 100
    readonly property real step: attrs.step ? Number(attrs.step) : 1
    readonly property bool box: attrs.mode === "box"
    readonly property string unit: attrs.unit_of_measurement || ""
    readonly property real cur: Number(stateText)
    readonly property bool valid: !unavailable && !isNaN(cur)
    property real pendingDelta: 0
    height: 136 * Theme.u

    function decimals() { var s = step.toString(); var i = s.indexOf("."); return i < 0 ? 0 : s.length - i - 1; }
    function snap(v) {
        v = minV + Math.round((v - minV) / step) * step;
        v = Math.max(minV, Math.min(maxV, v));
        return Number(v.toFixed(decimals()));
    }
    function fmt(v) { return v.toFixed(decimals()) + (unit ? " " + unit : ""); }
    function setValue(v) { sent(Ha.callService(domain, "set_value", { value: snap(v) }, entity)); }
    function bump(d) {
        if (!valid) return;
        pendingDelta = snap(cur + pendingDelta + d * step) - cur;
        commit.restart();
    }
    Timer {
        id: commit; interval: 900
        onTriggered: { if (t.pendingDelta !== 0) t.setValue(t.cur + t.pendingDelta); t.pendingDelta = 0; }
    }

    Rectangle {
        id: badge
        x: Theme.pad; y: Theme.pad
        width: 38 * Theme.u; height: width; radius: width / 2
        color: t.valid ? Theme.amberSoft : Theme.track
        Icon { anchors.centerIn: parent; size: 22; name: t.icon || "numeric"; color: t.valid ? Theme.amber : Theme.textDim }
    }
    Column {
        anchors { left: badge.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad; verticalCenter: badge.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
        Label {
            width: parent.width; size: 14; color: t.valid ? Theme.amber : Theme.textDim
            text: !t.valid ? (t.unavailable ? "Unavailable" : t.stateText)
                  : bar.dragging || bar.holding ? t.fmt(t.snap(t.minV + bar._live * (t.maxV - t.minV)))
                  : t.fmt(t.cur + t.pendingDelta)
        }
    }

    Bar {
        id: bar
        visible: !t.box
        enabled: t.valid
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        height: 44 * Theme.u
        value: t.valid && t.maxV > t.minV ? Math.max(0, Math.min(1, (t.cur - t.minV) / (t.maxV - t.minV))) : 0
        onCommitted: function (v) { t.setValue(t.minV + v * (t.maxV - t.minV)); }
    }

    Row {
        visible: t.box
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        spacing: 8 * Theme.u
        Repeater {
            model: [-1, 1]
            Rectangle {
                width: (parent.width - 8 * Theme.u) / 2; height: 44 * Theme.u; radius: 12 * Theme.u
                color: pm.pressed ? Theme.cardPressed : Theme.track
                Icon { anchors.centerIn: parent; size: 20; name: modelData < 0 ? "minus" : "plus"; color: t.valid ? Theme.text : Theme.textFaint }
                MouseArea { id: pm; anchors.fill: parent; enabled: t.box; onClicked: if (!Panel.multi) t.bump(modelData) }
            }
        }
    }
}
