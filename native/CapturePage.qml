// Capture: opt-in voice / camera capture (KIOSK_CAPTURE=1, video with CAPTURE_VIDEO=1).
// Talks to ha-kiosk-capture on 127.0.0.1 (CAPTURE_PORT + 1, default 10721). The mic and camera are
// opened only while a recording runs; each clip is published to Home Assistant over MQTT
// (<base>/capture/event) for your own automations. Polls only while this page is on screen.
import QtQuick 2.15
import "."

Item {
    id: page
    property string api: "http://127.0.0.1:10721"
    property var st: ({})                    // last /status reply
    property bool reachable: false
    property bool busyCall: false
    property string replyText: ""
    property double replyTs: 0
    property double nowSecs: Date.now() / 1000

    readonly property string state_: st.state || "idle"
    readonly property bool recording: state_ === "recording"
    readonly property bool processing: state_ === "processing"
    readonly property bool video: !!st.video
    readonly property real level: st.level || 0
    readonly property var last: st.last || null

    function call(path) {
        var x = new XMLHttpRequest();
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return;
            if (path !== "/status") page.busyCall = false;
            if (x.status !== 200) { page.reachable = false; return; }
            try {
                var s = JSON.parse(x.responseText);
                if (s.state !== undefined) { page.st = s; page.reachable = true; page.takeReply(s.reply); }
            } catch (e) {}
            if (path !== "/status") poll.restart();
        };
        if (path !== "/status") busyCall = true;
        x.open("GET", api + path);
        x.send();
    }
    function takeReply(r) {
        if (!r || !r.text) { replyText = ""; return; }
        if (r.ts !== replyTs) {
            replyTs = r.ts; replyText = r.text;
            var ca = r.clear_after === undefined ? 30 : r.clear_after;   // 0 = keep until replaced
            if (ca > 0) { replyClear.interval = Math.max(1, ca) * 1000; replyClear.restart(); } else replyClear.stop();
        }
    }
    function fmt(s) {
        s = Math.max(0, Math.round(s || 0));
        return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
    }
    function kindName(k) { return k === "snapshot" ? "Snapshot" : (k === "video" ? "Video clip" : "Voice clip"); }

    Timer { id: replyClear; onTriggered: page.replyText = "" }
    // 250 ms while recording/processing, 2 s while idle, never while off screen.
    Timer {
        id: poll
        interval: page.recording || page.processing ? 250 : 2000
        running: page.visible; repeat: true; triggeredOnStart: true
        onTriggered: { page.nowSecs = Date.now() / 1000; page.call("/status"); }
    }

    Column {
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme.pad }
        spacing: Theme.gap

        // Header: title + red Recording pill
        Item {
            width: parent.width; height: 40 * Theme.u
            Label { anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                    size: 22; font.weight: Font.DemiBold; text: "Capture" }
            Rectangle {
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                visible: page.recording
                height: 30 * Theme.u; width: pillText.implicitWidth + 30 * Theme.u; radius: height / 2
                color: Theme.red
                Rectangle { x: 10 * Theme.u; anchors.verticalCenter: parent.verticalCenter
                            width: 8 * Theme.u; height: width; radius: width / 2; color: "white" }
                Label { id: pillText; x: 22 * Theme.u; anchors.verticalCenter: parent.verticalCenter
                        size: 12; font.weight: Font.DemiBold; color: "white"
                        text: page.st.source === "ha" ? "Recording · Requested by Home Assistant" : "Recording" }
            }
        }

        // Big record / stop button
        Rectangle {
            id: big
            anchors.horizontalCenter: parent.horizontalCenter
            width: 150 * Theme.u; height: width; radius: width / 2
            color: page.recording ? Theme.red : (bigMa.pressed ? Theme.cardPressed : Theme.card)
            border.color: page.recording ? Theme.red : Theme.border; border.width: 2
            opacity: page.reachable && !page.processing ? 1 : 0.5
            Icon { anchors.centerIn: parent; size: 64
                   name: page.recording ? "stop" : "microphone"
                   color: page.recording ? "white" : Theme.amber }
            MouseArea {
                id: bigMa; anchors.fill: parent
                enabled: page.reachable && !page.processing && !page.busyCall
                onClicked: page.call(page.recording ? "/stop" : "/start?kind=audio")
            }
        }

        // Elapsed / max
        Label {
            anchors.horizontalCenter: parent.horizontalCenter; size: 28; font.weight: Font.Light
            text: page.fmt(page.st.elapsed) + " / " + page.fmt(page.st.max)
            color: page.recording ? Theme.text : Theme.textDim
        }

        // Level meter: solid bars only (weak GPU)
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 4 * Theme.u
            height: 26 * Theme.u
            Repeater {
                model: 20
                Rectangle {
                    width: 8 * Theme.u; height: parent.height; radius: 2 * Theme.u
                    anchors.verticalCenter: parent.verticalCenter
                    readonly property bool lit: page.recording && page.st.kind === "audio" && page.level * 20 > index
                    color: !lit ? Theme.track : (index >= 16 ? Theme.red : (index >= 12 ? Theme.amber : Theme.green))
                }
            }
        }

        // State text
        Label {
            width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; size: 14
            color: page.state_ === "error" ? Theme.red : Theme.textDim
            text: !page.reachable ? "Capture service not answering"
                : page.state_ === "error" ? "Error: " + (page.st.error || "unknown")
                : page.processing ? "Publishing…"
                : page.recording ? "Recording " + page.kindName(page.st.kind).toLowerCase() + " · tap to stop"
                : "Tap to record a voice clip"
        }

        // Video buttons (CAPTURE_VIDEO=1)
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Theme.gap
            visible: page.video
            Repeater {
                model: [ { label: "Snapshot", icon: "camera", path: "/snapshot" },
                         { label: "Video clip", icon: "video", path: "/start?kind=video" } ]
                Rectangle {
                    width: 140 * Theme.u; height: 56 * Theme.u; radius: Theme.radius
                    readonly property bool ok: page.reachable && !page.recording && !page.processing && !page.busyCall
                    color: vma.pressed ? Theme.cardPressed : Theme.card
                    border.color: Theme.border
                    opacity: ok ? 1 : 0.5
                    Row {
                        anchors.centerIn: parent; spacing: 8 * Theme.u
                        Icon { anchors.verticalCenter: parent.verticalCenter; size: 24; name: modelData.icon; color: Theme.blue }
                        Label { anchors.verticalCenter: parent.verticalCenter; size: 15; text: modelData.label }
                    }
                    MouseArea { id: vma; anchors.fill: parent; enabled: parent.ok; onClicked: page.call(modelData.path) }
                }
            }
        }

        // Reply from Home Assistant (<base>/capture/reply)
        Rectangle {
            width: parent.width; visible: page.replyText !== ""
            height: replyCol.height + 2 * Theme.pad; radius: Theme.radius
            color: Theme.card; border.color: Theme.border
            Column {
                id: replyCol
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: Theme.pad }
                spacing: 4 * Theme.u
                Label { size: 12; color: Theme.textDim; text: "Home Assistant" }
                Label { width: parent.width; size: 16; wrapMode: Text.WordWrap; elide: Text.ElideNone; text: page.replyText }
            }
        }

        // Last clip
        Label {
            width: parent.width; horizontalAlignment: Text.AlignHCenter; size: 13; color: Theme.textDim
            visible: !!page.last
            text: {
                if (!page.last) return "";
                var l = page.last, left = (l.expires || 0) - page.nowSecs;
                var len = l.kind === "snapshot" ? "" : " · " + page.fmt(l.duration);
                return "Last: " + page.kindName(l.kind) + len + " · "
                     + (left > 0 ? "available for " + Math.max(1, Math.ceil(left / 60)) + " min" : "expired");
            }
        }
    }
}
