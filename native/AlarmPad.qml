// Ring Alarm pad: arm home / arm away when disarmed, disarm with the alarm code when armed.
// Talks to Brilliant's own Ring integration through the bridge; the code is sent once, encrypted
// by the bridge for the alarm's one-time key, and never stored.
import QtQuick 2.15
import "."

Rectangle {
    id: pad
    color: Theme.bg
    visible: open
    property bool open: false
    property string pin: ""
    property string msg: ""
    property bool busy: false
    readonly property var a: Panel.ringAlarm
    readonly property string st: a ? a.state : "unknown"
    readonly property bool armed: st.indexOf("armed") === 0 || st.indexOf("alarm") >= 0
    readonly property var labels: ({ disarmed: "Disarmed", armed_home: "Armed home", armed_away: "Armed away",
                                     armed_night: "Armed night", disarming: "Disarming…", disarmed_not_ready: "Not ready",
                                     intrusion_alarm: "INTRUSION ALARM", fire_alarm: "FIRE ALARM",
                                     carbon_monoxide_alarm: "CO ALARM", medical_alarm: "MEDICAL ALARM",
                                     police_alarm: "POLICE ALARM", armed_away_bypass: "Armed away (bypass)",
                                     armed_home_bypass: "Armed home (bypass)", disarmed_bypass: "Disarmed (bypass)",
                                     disabled: "Off", unknown: "Unknown" })

    function show() { pin = ""; msg = ""; busy = false; open = true; idle.restart(); }
    function hide() { pin = ""; open = false; }
    function send(body, okMsg) {
        busy = true; msg = "Sending…"; idle.restart();
        Panel.bridgePost("/alarm", body, function (r) {
            pad.busy = false; pad.pin = "";
            pad.msg = r.ok ? okMsg + (body.mode === "disarmed" ? " · checking it was accepted…" : "") : ("Couldn't do that: " + (r.error || "error"));
            if (r.ok) closeSoon.restart();
        });
    }
    onStChanged: if (open && !busy && msg === "Sending…") msg = ""

    Connections { target: Panel; function onShowAlarm() { pad.show(); } }
    Timer { id: idle; interval: 60000; onTriggered: pad.hide() }        // never leave the pad open
    Timer { id: closeSoon; interval: 2500; onTriggered: pad.hide() }

    MouseArea { anchors.fill: parent; onPressed: idle.restart() }       // swallow taps behind the pad

    Column {
        anchors { top: parent.top; topMargin: 24 * Theme.u; horizontalCenter: parent.horizontalCenter }
        width: parent.width - 48 * Theme.u
        spacing: 14 * Theme.u

        Item {
            width: parent.width; height: 56 * Theme.u
            Column {
                anchors.verticalCenter: parent.verticalCenter
                Label { size: 14; color: Theme.textDim; text: pad.a ? pad.a.name : "Alarm" }
                Label { size: 24; font.weight: Font.DemiBold; text: pad.labels[pad.st] || pad.st
                        color: pad.st.indexOf("alarm") >= 0 ? Theme.red : (pad.armed ? Theme.amber : Theme.green) }
            }
            Rectangle {
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                width: 56 * Theme.u; height: width; radius: width / 2
                color: xArea.pressed ? Theme.cardPressed : Theme.track
                Icon { anchors.centerIn: parent; size: 28; name: "close"; color: Theme.text }
                MouseArea { id: xArea; anchors.fill: parent; onClicked: pad.hide() }
            }
        }

        // Disarmed: arm buttons
        Column {
            width: parent.width; spacing: 12 * Theme.u
            visible: !pad.armed
            Repeater {
                model: [{ m: "armed_home", t: "Arm home", i: "shield-home" }, { m: "armed_away", t: "Arm away", i: "shield-lock" }]
                Rectangle {
                    width: parent.width; height: 84 * Theme.u; radius: Theme.radius
                    color: ab.pressed ? Theme.cardPressed : Theme.card; border.color: Theme.border
                    opacity: pad.busy ? 0.5 : 1
                    Row {
                        anchors.centerIn: parent; spacing: 12 * Theme.u
                        Icon { size: 30; name: modelData.i; color: Theme.amber; anchors.verticalCenter: parent.verticalCenter }
                        Label { size: 20; font.weight: Font.DemiBold; text: modelData.t; anchors.verticalCenter: parent.verticalCenter }
                    }
                    MouseArea { id: ab; anchors.fill: parent; enabled: !pad.busy
                                onClicked: pad.send({ mode: modelData.m }, modelData.t + " sent") }
                }
            }
        }

        // Armed: code keypad to disarm
        Column {
            width: parent.width; spacing: 10 * Theme.u
            visible: pad.armed
            Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; size: 15; color: Theme.textDim
                    text: "Enter the alarm code to disarm" }
            Rectangle {
                width: parent.width; height: 56 * Theme.u; radius: 12 * Theme.u; color: Theme.card; border.color: Theme.border
                Label { anchors.centerIn: parent; size: 26; font.letterSpacing: 6 * Theme.u
                        text: pad.pin.length ? pad.pin.replace(/./g, "●") : " " }
            }
            Grid {
                columns: 3; spacing: 10 * Theme.u
                anchors.horizontalCenter: parent.horizontalCenter
                Repeater {
                    model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "OK"]
                    Rectangle {
                        width: (pad.width - 48 * Theme.u - 20 * Theme.u) / 3; height: 64 * Theme.u; radius: 14 * Theme.u
                        color: kb.pressed ? Theme.cardPressed : (modelData === "OK" ? Theme.amberSoft : Theme.card)
                        border.color: Theme.border
                        opacity: pad.busy ? 0.5 : 1
                        Label { anchors.centerIn: parent; size: 24; text: modelData
                                color: modelData === "OK" ? Theme.amber : Theme.text }
                        MouseArea {
                            id: kb; anchors.fill: parent; enabled: !pad.busy
                            onClicked: {
                                idle.restart();
                                if (modelData === "⌫") pad.pin = pad.pin.slice(0, -1);
                                else if (modelData === "OK") { if (pad.pin.length >= 4) pad.send({ mode: "disarmed", pin: pad.pin }, "Disarm sent"); }
                                else if (pad.pin.length < 8) pad.pin += modelData;
                            }
                        }
                    }
                }
            }
        }

        Label { width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; size: 15
                color: pad.msg.indexOf("Couldn't") === 0 ? Theme.red : Theme.textDim; text: pad.msg; visible: pad.msg !== "" }
    }
}
