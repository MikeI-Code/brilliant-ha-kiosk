// Doorbell and camera pop-up: full-screen still images from a Home Assistant camera.
//
// Opens when a doorbell event entity fires (device_class doorbell, e.g. Ring's event.front_door_ding):
// wakes the screen (also at night), shows that door's camera refreshed every few seconds, and closes
// itself after DOORBELL_SECS. Camera tiles open the same view through Panel.showCamera().
// Live video: H.264 over RTSP plays on the i.MX6 hardware decoder (the same parts Brilliant's own
// UI used). The panel cannot decode H.265, so a camera needs an H.264 stream, e.g. CuboAI with its
// "H.264 transcode" option on (found automatically), or a pair in CAMERA_LIVE. Others show stills.
// Pinch or double-tap to zoom, drag to pan, double-tap again to reset.
//
// kiosk.env:
//   DOORBELL=1                 0 = no pop-ups (camera tiles still work)
//   DOORBELL_SECS=45           how long the pop-up stays open
//   DOORBELL_MOTION=0          1 = also pop up on camera motion events (device_class motion)
//   DOORBELL_CAMERAS=          explicit pairs, e.g. event.front_door_ding=camera.front_door_live_view,...
//                              (default: found automatically from the entity names)
//   CAMERA_LIVE=               camera.x=rtsp://host:port/path,... H.264 RTSP streams for live video
//   CAMERA_LIVE_ON=1           0 = stills only
import QtQuick 2.15
import QtMultimedia 5.15
import "."

Rectangle {
    id: db
    color: "#000000"
    visible: active

    property bool active: false
    property string camera: ""
    property string title: ""
    property string subtitle: ""
    property bool enabled_: true
    property bool motion: false
    property int secs: 45
    property var manual: ({})          // DOORBELL_CAMERAS pairs
    property var lastSeen: ({})         // event entity -> last event timestamp handled
    property int tick: 0
    property bool frontIsA: true
    property var liveMap: ({})          // CAMERA_LIVE pairs
    property bool liveOn: true
    property string live: ""            // RTSP url while a live stream is open
    // hasVideo stays false for a gst-pipeline source: count it live once frames arrive.
    readonly property bool playing: live !== "" && player.playbackState === MediaPlayer.PlayingState && vout.sourceRect.width > 0

    function env(key, dflt) {
        var m = new RegExp("^\\s*" + key + "\\s*=\\s*\"?([^\"\\n]*)", "m").exec(Ha.readFile("/etc/ha-kiosk/kiosk.env"));
        return m ? m[1].trim() : dflt;
    }
    Component.onCompleted: {
        enabled_ = env("DOORBELL", "1") !== "0";
        motion = env("DOORBELL_MOTION", "0") === "1";
        secs = Math.max(10, parseInt(env("DOORBELL_SECS", "45")) || 45);
        var m = {};
        env("DOORBELL_CAMERAS", "").split(",").forEach(function (p) {
            var kv = p.split("="); if (kv.length === 2 && kv[0].trim() && kv[1].trim()) m[kv[0].trim()] = kv[1].trim();
        });
        manual = m;
        liveOn = env("CAMERA_LIVE_ON", "1") !== "0";
        var l = {};
        env("CAMERA_LIVE", "").split(",").forEach(function (p) {
            var i = p.indexOf("="); if (i > 0) l[p.slice(0, i).trim()] = p.slice(i + 1).trim();
        });
        liveMap = l;
    }

    // An H.264 RTSP stream for this camera, or "" (stills).
    function liveUrl(cam) {
        if (!liveOn || !cam) return "";
        if (liveMap[cam]) return liveMap[cam];
        var a = (Ha.get(cam) || {}).attributes || {};
        var host = (/^https?:\/\/([^:\/]+)/.exec(Ha.baseUrl) || [])[1];
        if (a.h264_transcode === true && a.rtsp_port && a.uid && host)          // CuboAI
            return "rtsp://" + host + ":" + a.rtsp_port + "/cuboai_h264_" + a.uid;
        return "";
    }
    function pipeline(url) {
        return "gst-pipeline: rtspsrc location=" + url + " protocols=tcp latency=200 name=s "
             + "s. ! application/x-rtp,media=video,encoding-name=H264 ! rtph264depay ! h264parse "
             + "! imxvpudec_h264 ! qtvideosink sync=false";
    }

    // Which camera belongs to an event entity: explicit pair, else the same name prefix
    // (event.front_door_ding -> camera.front_door_live_view / camera.front_door).
    function cameraFor(ev) {
        if (manual[ev]) return manual[ev];
        var base = ev.replace(/^event\./, "").replace(/_(ding|doorbell|press|ring|motion|person)$/, "");
        var tries = ["camera." + base + "_live_view", "camera." + base, "camera." + base + "_snapshot"];
        for (var i = 0; i < tries.length; i++) if (Ha.get(tries[i])) return tries[i];
        return "";
    }
    function kindOf(ev) {
        var s = Ha.get(ev); var dc = s && s.attributes.device_class;
        if (manual[ev]) return dc === "motion" ? "motion" : "doorbell";
        return dc === "doorbell" ? "doorbell" : (dc === "motion" && motion ? "motion" : "");
    }
    function niceName(e) {
        var s = Ha.get(e); var n = (s && s.attributes.friendly_name) || e;
        return n.replace(/\s+(Ding|Doorbell|Motion|Live view)$/i, "");
    }

    function camUrl() {
        var s = Ha.get(camera); var p = s && s.attributes.entity_picture;   // carries an access token
        return p ? Ha.baseUrl + p + (p.indexOf("?") >= 0 ? "&" : "?") + "_=" + tick : "";
    }
    function load() {
        tick++;
        if (tick % 3 === 0) Ha.keepWarm(camera);    // every ~9 s while open
        var back = frontIsA ? imgB : imgA;
        back.source = camUrl();
    }
    function swapIfReady(img) {
        if (img.status === Image.Error) { console.log("doorbell: image failed for " + camera); Ha.stillFailed(camera); }
        if (img.status === Image.Ready && img !== (frontIsA ? imgA : imgB)) frontIsA = (img === imgA);
    }
    function open(cam, t, sub, wake) {
        camera = cam || ""; title = t; subtitle = sub;
        console.log("doorbell: showing " + cam + " (" + t + ", " + sub + ")");
        imgA.source = ""; imgB.source = ""; frontIsA = false;
        resetZoom();
        live = liveUrl(camera);
        player.stop(); player.source = live ? pipeline(live) : "";
        if (live) { console.log("doorbell: live " + live); player.play(); }
        active = true;
        load();
        closeTimer.interval = secs * 1000; closeTimer.restart();
        if (wake) {                                  // same as a touch: the display manager wakes up
            var x = new XMLHttpRequest();
            x.open("POST", "http://127.0.0.1:8765/wake"); x.send("{}");
        }
    }
    function close() {
        active = false; closeTimer.stop(); imgA.source = ""; imgB.source = "";
        player.stop(); player.source = ""; live = "";
    }
    function resetZoom() { stage.scale = 1; stage.x = 0; stage.y = 0; }
    function clampPan() {
        var mx = (stage.scale - 1) * db.width / 2, my = (stage.scale - 1) * db.height / 2;
        stage.x = Math.max(-mx, Math.min(mx, stage.x)); stage.y = Math.max(-my, Math.min(my, stage.y));
    }

    Connections {
        target: Ha
        function onEntityUpdated(id) {
            if (!db.enabled_ || id.indexOf("event.") !== 0) return;
            var kind = db.kindOf(id); if (!kind) return;
            var s = Ha.get(id); if (!s) return;
            var t = Date.parse(s.state);                       // event entities: state = time of the event
            if (isNaN(t) || Date.now() - t > 60000 || db.lastSeen[id] === s.state) return;
            db.lastSeen[id] = s.state;
            var cam = db.cameraFor(id); if (!cam) { console.log("doorbell: no camera for " + id); return; }
            if (db.active && db.camera === cam) return;
            db.open(cam, db.niceName(id), (kind === "motion" ? "Motion" : "Doorbell") + " · "
                    + Qt.formatTime(new Date(t), "h:mm AP"), true);
        }
    }
    // Brilliant's own Ring integration (read through the bridge): doorbell presses and alarm mode.
    // The bridge has already woken the screen when a press arrives.
    property int ringSeq: -1
    function cameraForName(n) {
        var base = n.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
        var tries = ["camera." + base + "_live_view", "camera." + base, "camera." + base + "_snapshot"];
        for (var i = 0; i < tries.length; i++) if (Ha.get(tries[i])) return tries[i];
        return "";
    }
    function pollRing() {
        var x = new XMLHttpRequest();
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE || x.status !== 200) return;
            var r; try { r = JSON.parse(x.responseText); } catch (e) { return; }
            Panel.ringAlarm = r.alarm;
            if (JSON.stringify(r.security || null) !== JSON.stringify(Panel.security)) Panel.security = r.security || null;
            if (JSON.stringify(r.locks || []) !== JSON.stringify(Panel.locks)) Panel.locks = r.locks || [];
            if (JSON.stringify(r.devices || []) !== JSON.stringify(Panel.ringDevices)) Panel.ringDevices = r.devices || [];
            if (db.ringSeq >= 0 && r.seq !== db.ringSeq && r.doorbell && db.enabled_) {
                var cam = Panel.cameraForName(r.doorbell.name);
                if (!(db.active && db.camera === cam))          // the HA event may have opened it already
                    db.open(cam, r.doorbell.name, "Doorbell · " + Qt.formatTime(new Date(r.doorbell.ts * 1000), "h:mm AP"), false);
                if (!cam) console.log("doorbell: no HA camera found for '" + r.doorbell.name + "'");
            }
            db.ringSeq = r.seq;
        };
        x.open("GET", "http://127.0.0.1:8765/ring"); x.send();
    }
    Timer { interval: 1500; repeat: true; running: true; triggeredOnStart: true; onTriggered: db.pollRing() }

    Connections {
        target: Panel
        function onShowCamera(entity, t) { db.open(entity, t || db.niceName(entity), "Camera", false); }
    }

    Timer { id: closeTimer; onTriggered: db.close() }
    // Stills keep coming until live video is actually on screen (and again if it stops).
    Timer { interval: 3000; repeat: true; running: db.active && !db.playing; onTriggered: db.load() }

    MediaPlayer {
        id: player
        autoLoad: false
        onError: console.log("doorbell: live video error: " + errorString)
        onPlaybackStateChanged: console.log("doorbell: live state " + playbackState)
    }

    // Everything that zooms and pans lives in the stage.
    Item {
        id: stage
        width: db.width; height: db.height
        transformOrigin: Item.Center
    Image {
        id: imgA
        anchors.centerIn: parent; width: parent.width; height: parent.height
        fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
        sourceSize.width: db.width
        visible: db.frontIsA && status === Image.Ready
        onStatusChanged: db.swapIfReady(imgA)
    }
    Image {
        id: imgB
        anchors.centerIn: parent; width: parent.width; height: parent.height
        fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
        sourceSize.width: db.width
        visible: !db.frontIsA && status === Image.Ready
        onStatusChanged: db.swapIfReady(imgB)
    }
    VideoOutput {
        id: vout
        anchors.fill: parent
        source: player
        fillMode: VideoOutput.PreserveAspectFit
        onSourceRectChanged: console.log("doorbell: live video " + sourceRect.width + "x" + sourceRect.height)
        visible: db.playing                 // a reused pipeline can show stale frames while starting
    }
    }
    Label {
        anchors.centerIn: parent; visible: !db.playing && imgA.status !== Image.Ready && imgB.status !== Image.Ready
        size: 16; color: Theme.textDim; text: db.camera ? "Loading camera…" : "No camera linked to this doorbell"
    }

    // Title bar (solid, no translucent overlay over the moving picture)
    Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 76 * Theme.u; color: Theme.card
        Column {
            anchors { left: parent.left; leftMargin: 18 * Theme.u; verticalCenter: parent.verticalCenter }
            Label { size: 20; font.weight: Font.DemiBold; text: db.title }
            Label { size: 14; color: Theme.amber; text: db.subtitle }
        }
        Rectangle {
            anchors { right: parent.right; rightMargin: 12 * Theme.u; verticalCenter: parent.verticalCenter }
            width: 56 * Theme.u; height: width; radius: width / 2
            color: closeArea.pressed ? Theme.cardPressed : Theme.track
            Icon { anchors.centerIn: parent; size: 28; name: "close"; color: Theme.text }
            MouseArea { id: closeArea; anchors.fill: parent; onClicked: db.close() }
        }
    }
    // Live badge (solid)
    Rectangle {
        visible: db.playing
        anchors { left: parent.left; bottom: parent.bottom; margins: 14 * Theme.u }
        width: liveLbl.width + 20 * Theme.u; height: 30 * Theme.u; radius: height / 2; color: "#c62828"
        Label { id: liveLbl; anchors.centerIn: parent; size: 13; font.weight: Font.DemiBold; text: "LIVE"; color: "#ffffff" }
    }
    // Pinch or double-tap to zoom, drag to pan; any touch keeps the view open longer.
    PinchArea {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; top: parent.top; topMargin: 76 * Theme.u }
        property real startScale: 1
        onPinchStarted: { startScale = stage.scale; closeTimer.restart() }
        onPinchUpdated: { stage.scale = Math.max(1, Math.min(4, startScale * pinch.scale)); db.clampPan() }
        onPinchFinished: if (stage.scale < 1.05) db.resetZoom()
        MouseArea {
            anchors.fill: parent
            property real px: 0; property real py: 0
            onPressed: { px = mouse.x; py = mouse.y; closeTimer.restart() }
            onPositionChanged: if (stage.scale > 1) {
                stage.x += mouse.x - px; stage.y += mouse.y - py; px = mouse.x; py = mouse.y; db.clampPan()
            }
            onDoubleClicked: {
                if (stage.scale > 1) { db.resetZoom(); return; }
                stage.scale = 2.5;                                   // zoom in on the tapped point
                stage.x = (db.width / 2 - mouse.x) * 1.5; stage.y = (db.height / 2 - mouse.y - 76 * Theme.u) * 1.5;
                db.clampPan();
            }
        }
    }
}
