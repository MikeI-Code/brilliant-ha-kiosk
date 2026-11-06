// Thermostat / room tile: current temp, target, and -/+ (debounced).
//
// With a house thermostat controller (CLIMATE_CONTROLLER, e.g. a Flair "structure"):
//   thermostat tiles (e.g. ecobee)  current temp and heating/cooling from the thermostat;
//                                    mode and setpoint go to the controller; Fan chip goes to the thermostat
//   the controller's own tile        mode chip + its own setpoint
//   room tiles (e.g. Flair rooms)    their own setpoint, and the house mode
// Without it, every tile controls its own entity, as before.
import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property string ctl: Panel.climateController
    property var ctlSt: null
    function refreshCtl() { ctlSt = ctl ? Ha.get(ctl) : null; }
    Component.onCompleted: refreshCtl()
    Connections {
        target: Ha
        function onEntityUpdated(id) { if (id === t.ctl) t.refreshCtl(); }
        function onStatesLoaded() { t.refreshCtl(); }
    }
    readonly property bool isCtl: ctl !== "" && entity === ctl
    readonly property bool routed: ctl !== "" && !isCtl && Panel.isThermostat(entity, attrs)   // a thermostat driven by the controller
    readonly property bool showModes: routed || isCtl
    readonly property bool showFan: routed && !!attrs.fan_modes && attrs.fan_modes.indexOf("on") >= 0 && attrs.fan_modes.indexOf("auto") >= 0
    readonly property var tA: routed && ctlSt ? ctlSt.attributes : attrs          // where the setpoint lives
    readonly property string tEntity: routed ? ctl : entity
    readonly property string mode: (ctl !== "" && !isCtl && ctlSt) ? ctlSt.state : stateText
    readonly property var modes: ((routed && ctlSt ? ctlSt.attributes.hvac_modes : attrs.hvac_modes) || [])
    readonly property bool range: tA.temperature === undefined || tA.temperature === null
    property real pendingDelta: 0
    property string pendingMode: ""
    readonly property real step: tA.target_temp_step || attrs.target_temp_step || 1
    readonly property var cur: attrs.current_temperature
    readonly property string action: attrs.hvac_action || ""
    readonly property string shownMode: pendingMode || mode
    readonly property color accent: action === "heating" ? Theme.orange : action === "cooling" ? Theme.blue
                                    : shownMode === "heat" ? Theme.orange : shownMode === "cool" ? Theme.blue : Theme.textDim
    height: (showModes ? 188 : 136) * Theme.u
    readonly property var modeNames: ({ heat_cool: "Heat/Cool", heat: "Heat", cool: "Cool", auto: "Auto", dry: "Dry",
                                        fan_only: "Fan", off: "Off", heating: "Heating", cooling: "Cooling",
                                        idle: "Idle", drying: "Drying", fan: "Fan", preheating: "Preheating" })
    function nice(s) { return modeNames[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ") : ""); }

    function fmt(v) { return v === undefined || v === null ? "--" : (Math.round(v * 2) / 2).toString(); }
    function targetText() {
        if (unavailable) return "Unavailable";
        if (shownMode === "off") return "Off";
        if (range) return fmt(tA.target_temp_low + pendingDelta) + "–" + fmt(tA.target_temp_high + pendingDelta) + "°";
        return fmt(tA.temperature + pendingDelta) + "°";
    }
    function bump(d) {
        if (unavailable || shownMode === "off") return;
        pendingDelta += d * step;
        commit.restart();
    }
    function nextMode() {                       // tap the mode chip to step through the modes; sent after a pause
        if (!modes.length) return;
        var i = modes.indexOf(shownMode);
        pendingMode = modes[(i + 1) % modes.length];
        modeCommit.restart();
    }
    Timer {
        id: commit; interval: 900
        onTriggered: {
            if (t.range)
                t.sent(Ha.callService("climate", "set_temperature",
                               { target_temp_low: t.tA.target_temp_low + t.pendingDelta,
                                 target_temp_high: t.tA.target_temp_high + t.pendingDelta }, t.tEntity));
            else
                t.sent(Ha.callService("climate", "set_temperature", { temperature: t.tA.temperature + t.pendingDelta }, t.tEntity));
            t.pendingDelta = 0;
        }
    }
    Timer {
        id: modeCommit; interval: 1200
        onTriggered: {
            if (t.pendingMode && t.pendingMode !== t.mode)
                t.sent(Ha.callService("climate", "set_hvac_mode", { hvac_mode: t.pendingMode }, t.tEntity));
            modeClear.restart();
        }
    }
    Timer { id: modeClear; interval: 4000; onTriggered: t.pendingMode = "" }
    onModeChanged: if (pendingMode === mode) pendingMode = ""

    Label { id: nm; x: Theme.pad; y: Theme.pad; width: parent.width - 2 * Theme.pad; text: t.name; size: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
    Row {
        id: reading
        anchors { left: parent.left; leftMargin: Theme.pad; top: nm.bottom; topMargin: 2 * Theme.u }
        spacing: 6 * Theme.u
        Label { text: t.fmt(t.cur) + "°"; size: 30; font.weight: Font.Light }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            Label { size: 13; color: t.accent; text: t.nice(t.action ? t.action : t.shownMode) }
            Label { size: 13; color: Theme.textDim; text: "→ " + t.targetText() }
        }
    }
    // Mode (to the controller) and Fan (to this thermostat)
    Row {
        visible: t.showModes
        anchors { left: parent.left; right: parent.right; top: reading.bottom; margins: Theme.pad; topMargin: 8 * Theme.u }
        spacing: 8 * Theme.u
        Rectangle {
            width: t.showFan ? (parent.width - 8 * Theme.u) / 2 : parent.width; height: 44 * Theme.u; radius: 12 * Theme.u
            color: mc.pressed ? Theme.cardPressed : (t.shownMode === "off" ? Theme.track : Theme.amberSoft)
            Label { anchors.centerIn: parent; size: 14; color: t.shownMode === "off" ? Theme.text : Theme.amber
                    text: "Mode: " + t.nice(t.shownMode) }
            MouseArea { id: mc; anchors.fill: parent; onClicked: if (!Panel.multi) t.nextMode() }
        }
        Rectangle {
            visible: t.showFan
            width: (parent.width - 8 * Theme.u) / 2; height: 44 * Theme.u; radius: 12 * Theme.u
            readonly property bool on: t.attrs.fan_mode === "on"
            color: fc.pressed ? Theme.cardPressed : (on ? Theme.blueSoft : Theme.track)
            Label { anchors.centerIn: parent; size: 14; color: parent.on ? Theme.blue : Theme.text
                    text: "Fan: " + (parent.on ? "On" : "Auto") }
            MouseArea { id: fc; anchors.fill: parent
                        onClicked: if (!Panel.multi) t.sent(Ha.callService("climate", "set_fan_mode",
                                                                           { fan_mode: parent.on ? "auto" : "on" }, t.entity)) }
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
                opacity: t.shownMode === "off" ? 0.5 : 1
                Icon { anchors.centerIn: parent; size: 20; name: modelData < 0 ? "minus" : "plus"; color: Theme.text }
                MouseArea { id: pm; anchors.fill: parent; onClicked: if (!Panel.multi) t.bump(modelData) }
            }
        }
    }
}
