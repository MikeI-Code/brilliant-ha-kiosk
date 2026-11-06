// Security tab: everything Brilliant's own integrations provide that Home Assistant doesn't.
// Sections (each shown only when Brilliant reports it; more can be added the same way, e.g. a
// building-entry intercom):
//   Alarm      Ring Alarm status, Arm home / Arm away, Disarm with the code (AlarmPad.qml)
//   Locks      Schlage locks: lock with one tap, unlock with two
//   Doorbells & cameras   Ring devices, shown with Home Assistant's camera stills; tap = full screen
// Control goes through the bridge to Brilliant's integrations; Home Assistant only gets status.
import QtQuick 2.15
import "."

Flickable {
    id: page
    contentWidth: width
    contentHeight: body.height + 24 * Theme.u
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: !Panel.multi
    flickDeceleration: 4000
    Connections { target: Panel; function onGoHome() { page.contentY = 0; } }

    readonly property var alarm: Panel.ringAlarm
    readonly property string st: alarm ? alarm.state : "unknown"
    readonly property bool armed: st.indexOf("armed") === 0 || st.indexOf("alarm") >= 0
    readonly property var labels: ({ disarmed: "Disarmed", armed_home: "Armed home", armed_away: "Armed away",
                                     armed_night: "Armed night", disarming: "Disarming…", disarmed_not_ready: "Not ready",
                                     intrusion_alarm: "INTRUSION ALARM", fire_alarm: "FIRE ALARM",
                                     carbon_monoxide_alarm: "CO ALARM", medical_alarm: "MEDICAL ALARM",
                                     police_alarm: "POLICE ALARM", armed_away_bypass: "Armed away (bypass)",
                                     armed_home_bypass: "Armed home (bypass)", disarmed_bypass: "Disarmed (bypass)",
                                     disabled: "Off", unknown: "Unknown" })
    property string alarmMsg: ""
    property bool alarmBusy: false
    readonly property var sec: Panel.security
    readonly property bool viewOnly: !!sec && !!sec.access && sec.access !== "control"   // SECURITY_ACCESS=view
    readonly property bool canArm: !sec || sec.caps.arm
    readonly property bool canDisarm: !sec || (sec.caps.disarm && !sec.lockout_until)
    readonly property bool canLock: !sec || sec.caps.lock
    readonly property string disarmNote: !sec ? ""
        : sec.lockout_until ? "Panel disarm paused after 3 failed attempts. Try again in a few minutes, or use the Ring app or keypad."
        : !sec.caps.disarm ? "Disarm from the panel is unavailable: " + (sec.reasons.disarm || "not supported") + ". Use the Ring app or keypad."
        : sec.last_disarm === "not_accepted" ? "The last disarm wasn't accepted (wrong code, or a Brilliant update changed something)."
        : sec.disarm_unverified ? "Panel disarm hasn't been confirmed on this Brilliant firmware yet." : ""
    function arm(mode, label) {
        alarmBusy = true; alarmMsg = label + "…";
        Panel.bridgePost("/alarm", { mode: mode }, function (r) {
            page.alarmBusy = false;
            page.alarmMsg = r.ok ? "" : "Couldn't do that: " + (r.error || "error");
        });
    }

    Column {
        id: body
        x: Theme.gap; y: Theme.gap
        width: parent.width - 2 * Theme.gap
        spacing: 18 * Theme.u

        // ---------------------------------------------------------------- Alarm
        Column {
            width: parent.width; spacing: 8 * Theme.u
            visible: !!page.alarm
            Label { text: "ALARM"; size: 15; color: Theme.textDim; font.weight: Font.DemiBold; leftPadding: 4 * Theme.u }
            Rectangle {
                width: parent.width; height: alarmCol.height + 2 * Theme.pad; radius: Theme.radius
                color: page.armed ? Theme.onTint : Theme.card; border.color: Theme.border
                Column {
                    id: alarmCol
                    x: Theme.pad; y: Theme.pad; width: parent.width - 2 * Theme.pad
                    spacing: 12 * Theme.u
                    Row {
                        spacing: 12 * Theme.u
                        Icon { size: 40; anchors.verticalCenter: parent.verticalCenter
                               name: page.st.indexOf("alarm") >= 0 ? "shield-alert" : (page.armed ? "shield-lock" : "shield-home-outline")
                               color: page.st.indexOf("alarm") >= 0 ? Theme.red : (page.armed ? Theme.amber : Theme.green) }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            Label { size: 22; font.weight: Font.DemiBold; text: page.labels[page.st] || page.st
                                    color: page.st.indexOf("alarm") >= 0 ? Theme.red : (page.armed ? Theme.amber : Theme.green) }
                            Label { size: 14; color: Theme.textDim; text: page.alarm ? page.alarm.name : "" }
                        }
                    }
                    Row {
                        width: parent.width; spacing: 10 * Theme.u
                        visible: !page.armed && !page.viewOnly
                        Repeater {
                            model: [{ m: "armed_home", t: "Arm home", i: "shield-home" }, { m: "armed_away", t: "Arm away", i: "shield-lock" }]
                            Rectangle {
                                width: (parent.width - 10 * Theme.u) / 2; height: 64 * Theme.u; radius: 14 * Theme.u
                                color: ab.pressed ? Theme.cardPressed : Theme.track; opacity: page.alarmBusy || !page.canArm ? 0.4 : 1
                                Row {
                                    anchors.centerIn: parent; spacing: 8 * Theme.u
                                    Icon { size: 24; name: modelData.i; color: Theme.amber; anchors.verticalCenter: parent.verticalCenter }
                                    Label { size: 17; font.weight: Font.DemiBold; text: modelData.t; anchors.verticalCenter: parent.verticalCenter }
                                }
                                MouseArea { id: ab; anchors.fill: parent; enabled: !page.alarmBusy && page.canArm; onClicked: page.arm(modelData.m, modelData.t) }
                            }
                        }
                    }
                    Rectangle {
                        width: parent.width; height: 64 * Theme.u; radius: 14 * Theme.u
                        visible: page.armed && !page.viewOnly
                        opacity: page.canDisarm ? 1 : 0.4
                        color: db.pressed ? Theme.cardPressed : Theme.track
                        Row {
                            anchors.centerIn: parent; spacing: 8 * Theme.u
                            Icon { size: 24; name: "dialpad"; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                            Label { size: 17; font.weight: Font.DemiBold; text: "Disarm"; anchors.verticalCenter: parent.verticalCenter }
                        }
                        MouseArea { id: db; anchors.fill: parent; enabled: page.canDisarm; onClicked: Panel.showAlarm() }
                    }
                    Label { width: parent.width; wrapMode: Text.WordWrap; size: 14; visible: page.viewOnly
                            color: Theme.textDim; text: "View only on this panel" }
                    Label { width: parent.width; wrapMode: Text.WordWrap; size: 14; visible: page.disarmNote !== "" && !page.viewOnly
                            color: page.canDisarm ? Theme.textDim : Theme.amber; text: page.disarmNote }
                    Label { width: parent.width; wrapMode: Text.WordWrap; size: 14; color: Theme.amber
                            visible: !!page.sec && !page.canArm && !page.viewOnly
                            text: "Arming from the panel is unavailable: " + (page.sec && page.sec.reasons.arm || "") + ". Use the Ring app." }
                    Label { width: parent.width; wrapMode: Text.WordWrap; size: 14; visible: page.alarmMsg !== ""
                            color: page.alarmMsg.indexOf("Couldn't") === 0 ? Theme.red : Theme.textDim; text: page.alarmMsg }
                }
            }
        }

        // ---------------------------------------------------------------- Locks
        Column {
            width: parent.width; spacing: 8 * Theme.u
            visible: Panel.locks.length > 0
            Label { text: "LOCKS"; size: 15; color: Theme.textDim; font.weight: Font.DemiBold; leftPadding: 4 * Theme.u }
            Label { text: "View only on this panel"; size: 14; color: Theme.textDim; leftPadding: 4 * Theme.u; visible: page.viewOnly }
            Repeater {
                model: Panel.locks
                Rectangle {
                    id: lk
                    width: body.width; height: 88 * Theme.u; radius: Theme.radius
                    property bool confirm: false
                    property bool pending: false
                    readonly property bool locked: modelData.state === "locked"
                    readonly property bool jammed: modelData.state === "jammed"
                    onLockedChanged: pending = false
                    color: lk.confirm ? "#3a1f1f" : (la.pressed ? Theme.cardPressed : (lk.locked ? Theme.card : Theme.onTint))
                    border.color: lk.pending ? Theme.amber : Theme.border
                    border.width: lk.pending ? Math.max(2, Theme.u * 1.5) : 1
                    Timer { id: lkConfirm; interval: 3000; onTriggered: lk.confirm = false }
                    Timer { id: lkPending; interval: 20000; onTriggered: lk.pending = false }
                    Row {
                        anchors { left: parent.left; leftMargin: Theme.pad; verticalCenter: parent.verticalCenter }
                        spacing: 12 * Theme.u
                        Rectangle {
                            width: 52 * Theme.u; height: width; radius: width / 2
                            color: lk.locked ? "#1d3324" : (lk.jammed ? "#3a1f1f" : Theme.amberSoft)
                            Icon { anchors.centerIn: parent; size: 28
                                   name: lk.locked ? "lock" : (lk.jammed ? "lock-alert" : "lock-open-variant")
                                   color: lk.locked ? Theme.green : (lk.jammed ? Theme.red : Theme.amber) }
                        }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            Label { size: 18; font.weight: Font.DemiBold
                                    text: modelData.name.charAt(0).toUpperCase() + modelData.name.slice(1) }
                            Label { size: 15
                                    color: lk.confirm ? Theme.red : (lk.locked ? Theme.green : (lk.jammed ? Theme.red : Theme.amber))
                                    text: lk.confirm ? "Tap again to unlock"
                                          : lk.pending ? (lk.locked ? "Unlocking…" : "Locking…")
                                          : (lk.locked ? "Locked" : lk.jammed ? "Jammed" : "Unlocked")
                                            + (modelData.battery_low ? " · battery low" : "") }
                        }
                    }
                    Label { anchors { right: parent.right; rightMargin: Theme.pad; verticalCenter: parent.verticalCenter }
                            size: 14; color: page.canLock ? Theme.textDim : Theme.amber
                            text: !page.canLock ? "Control unavailable" : (lk.locked ? "Tap to unlock" : "Tap to lock")
                            visible: !lk.pending && !lk.confirm && !page.viewOnly }
                    opacity: page.canLock || page.viewOnly ? 1 : 0.5
                    MouseArea {
                        id: la; anchors.fill: parent; enabled: !lk.pending && page.canLock && !page.viewOnly
                        onClicked: {
                            if (lk.locked && !lk.confirm) { lk.confirm = true; lkConfirm.restart(); return; }
                            lk.confirm = false; lk.pending = true; lkPending.restart();
                            Panel.bridgePost("/lock", { id: modelData.id, locked: !lk.locked }, function (r) {
                                if (!r.ok) { lk.pending = false; console.log("lock: " + r.error); }
                            });
                        }
                    }
                }
            }
        }

        // ---------------------------------------------------------------- Doorbells & cameras
        Column {
            width: parent.width; spacing: 8 * Theme.u
            visible: Panel.ringDevices.length > 0
            Label { text: "DOORBELLS & CAMERAS"; size: 15; color: Theme.textDim; font.weight: Font.DemiBold; leftPadding: 4 * Theme.u }
            Flow {
                width: parent.width; spacing: Theme.gap
                Repeater {
                    model: Panel.ringDevices
                    CameraTile {
                        width: (body.width - Theme.gap) / 2
                        entity: Panel.cameraForName(modelData.name)
                        name: modelData.name + (modelData.kind === "doorbell" && modelData.last_press
                              ? " · rang " + Qt.formatTime(new Date(modelData.last_press * 1000), "h:mm AP") : "")
                        icon: modelData.kind === "doorbell" ? "doorbell-video" : "cctv"
                    }
                }
            }
        }
    }
}
