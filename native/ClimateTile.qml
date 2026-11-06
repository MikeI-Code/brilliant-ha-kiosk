// Thermostat / room tile: current temp, target, and -/+ (debounced).
import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property string mode: stateText
    readonly property bool range: attrs.temperature === undefined || attrs.temperature === null
    property real pendingDelta: 0
    readonly property real step: attrs.target_temp_step || 1
    readonly property var cur: attrs.current_temperature
    readonly property string action: attrs.hvac_action || ""
    readonly property color accent: action === "heating" ? Theme.orange : action === "cooling" ? Theme.blue
                                    : mode === "heat" ? Theme.orange : mode === "cool" ? Theme.blue : Theme.textDim
    height: 136 * Theme.u
    readonly property var modeNames: ({ heat_cool: "Heat/Cool", heat: "Heat", cool: "Cool", auto: "Auto", dry: "Dry",
                                        fan_only: "Fan", off: "Off", heating: "Heating", cooling: "Cooling",
                                        idle: "Idle", drying: "Drying", fan: "Fan", preheating: "Preheating" })
    function nice(s) { return modeNames[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ") : ""); }

    function fmt(v) { return v === undefined || v === null ? "--" : (Math.round(v * 2) / 2).toString(); }
    function targetText() {
        if (unavailable) return "Unavailable";
        if (mode === "off") return "Off";
        if (range) return fmt(attrs.target_temp_low + pendingDelta) + "–" + fmt(attrs.target_temp_high + pendingDelta) + "°";
        return fmt(attrs.temperature + pendingDelta) + "°";
    }
    function bump(d) {
        if (unavailable || mode === "off") return;
        pendingDelta += d * step;
        commit.restart();
    }
    Timer {
        id: commit; interval: 900
        onTriggered: {
            if (t.range)
                Ha.callService("climate", "set_temperature",
                               { target_temp_low: t.attrs.target_temp_low + t.pendingDelta,
                                 target_temp_high: t.attrs.target_temp_high + t.pendingDelta }, t.entity);
            else
                Ha.callService("climate", "set_temperature", { temperature: t.attrs.temperature + t.pendingDelta }, t.entity);
            t.pendingDelta = 0;
        }
    }

    Label { id: nm; x: Theme.pad; y: Theme.pad; width: parent.width - 2 * Theme.pad; text: t.name; size: 15; font.weight: Font.DemiBold }
    Row {
        anchors { left: parent.left; leftMargin: Theme.pad; top: nm.bottom; topMargin: 2 * Theme.u }
        spacing: 6 * Theme.u
        Label { text: t.fmt(t.cur) + "°"; size: 30; font.weight: Font.Light }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            Label { size: 13; color: t.accent; text: t.nice(t.action ? t.action : t.mode) }
            Label { size: 13; color: Theme.textDim; text: "→ " + t.targetText() }
        }
    }
    Row {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        spacing: 8 * Theme.u
        Repeater {
            model: [-1, 1]
            Rectangle {
                width: (parent.width - 8 * Theme.u) / 2; height: 44 * Theme.u; radius: 12 * Theme.u
                color: pm.pressed ? Theme.cardPressed : Theme.track
                Icon { anchors.centerIn: parent; size: 20; name: modelData < 0 ? "minus" : "plus"; color: Theme.text }
                MouseArea { id: pm; anchors.fill: parent; onClicked: t.bump(modelData) }
            }
        }
    }
}
