// Native Home Assistant panel UI for the Brilliant control (Qt Quick, no browser).
// Run: QML_XHR_ALLOW_FILE_READ=1 qmlscene /etc/ha-kiosk/native/main.qml
// Reads: /etc/ha-kiosk/kiosk.env (HA_URL), /etc/ha-kiosk/token, ./layout.json,
//        /run/ha-kiosk-display/state (from ha-kiosk-display.sh).
import QtQuick 2.15
import QtQuick.Window 2.15
import "."
import "LovelaceLayout.js" as LovelaceLayout

Window {
    id: win
    visible: true
    visibility: Window.FullScreen
    color: Theme.bg

    property var layout: ({ pages: [] })
    property int current: 0
    property string displayState: "awake"

    // Layout source: the HA dashboard named by LAYOUT_DASHBOARD in kiosk.env (edited in HA's UI),
    // cached in /var/lib/ha-kiosk for offline boots; otherwise ./layout.json.
    property string layoutDashboard: ""
    property string layoutJson: ""
    readonly property string cachePath: "/var/lib/ha-kiosk/layout-cache.json"

    function setLayout(txt, from) {
        if (!txt || txt === layoutJson) return false;
        try { var l = JSON.parse(txt); } catch (e) { console.log("native: bad layout from " + from + ": " + e); return false; }
        if (!l.pages || l.pages.length === 0) return false;
        layoutJson = txt; layout = l;
        if (current >= l.pages.length) current = 0;
        console.log("native: layout from " + from + ", " + l.pages.length + " pages");
        return true;
    }
    function fetchLayout() {
        if (!layoutDashboard) return;
        Ha.send({ type: "lovelace/config", url_path: layoutDashboard }, function (r) {
            if (!r.success) { console.log("native: dashboard '" + layoutDashboard + "': " + JSON.stringify(r.error)); return; }
            var l = LovelaceLayout.convert(r.result, function (e) {
                var s = Ha.get(e); return (s && s.attributes.friendly_name) || e; });
            if (setLayout(JSON.stringify(l), "HA dashboard '" + layoutDashboard + "'")) {
                var x = new XMLHttpRequest();   // cache it (needs QML_XHR_ALLOW_FILE_WRITE=1)
                try { x.open("PUT", "file://" + cachePath); x.send(layoutJson); } catch (e) { console.log("native: cache write failed: " + e); }
            }
        });
    }
    Connections {
        target: Ha
        function onStatesLoaded() { win.fetchLayout(); }
        function onLovelaceUpdated(p) { if (p === win.layoutDashboard) win.fetchLayout(); }
    }

    Component.onCompleted: {
        Theme.u = Math.max(1, Math.min(width, height) / 360);
        var m = /^\s*LAYOUT_DASHBOARD\s*=\s*"?([A-Za-z0-9_-]+)/m.exec(Ha.readFile("/etc/ha-kiosk/kiosk.env"));
        layoutDashboard = m ? m[1] : "";
        if (!(layoutDashboard && setLayout(Ha.readFile(cachePath), "cache")))
            setLayout(Ha.readUrl(Qt.resolvedUrl("layout.json")), "layout.json");
        console.log("native: u=" + Theme.u + (layoutDashboard ? ", layout dashboard: " + layoutDashboard : ""));
        Ha.start();
    }

    // Pages side by side: swipe left/right (or tap a tab) to switch. All pages stay loaded
    // (large cacheBuffer), so switching is instant. Vertical drags still scroll the page.
    ListView {
        id: pages
        anchors { left: parent.left; right: parent.right; top: parent.top; bottom: tabs.top }
        orientation: ListView.Horizontal
        snapMode: ListView.SnapOneItem
        highlightRangeMode: ListView.StrictlyEnforceRange
        preferredHighlightBegin: 0; preferredHighlightEnd: width
        highlightMoveDuration: 220
        boundsBehavior: Flickable.StopAtBounds
        flickDeceleration: 6000
        cacheBuffer: width * 12
        clip: true
        interactive: win.displayState === "awake" && !Panel.multi
        model: win.layout.pages || []
        onCurrentIndexChanged: if (currentIndex >= 0 && currentIndex !== win.current) win.current = currentIndex
        delegate: Loader {
            width: pages.width; height: pages.height
            asynchronous: index !== 0
            sourceComponent: modelData.type === "sonos" ? sonosPage : gridPage
            property var spec: modelData
        }
        Component { id: gridPage
            Page { spec: parent.spec; weatherEntity: win.layout.weather || ""; insideEntity: win.layout.insideTemp || "" } }
        Component { id: sonosPage
            SonosPage { speakers: parent.spec.speakers || [] } }
    }

    // Connection banner
    Rectangle {
        anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 8 * Theme.u }
        visible: !Ha.connected
        height: 30 * Theme.u; width: bannerText.width + 24 * Theme.u; radius: height / 2
        color: "#3a1f1f"
        Label { id: bannerText; anchors.centerIn: parent; size: 12; color: Theme.red; text: "Connecting to Home Assistant…" }
    }

    // Bottom tab bar
    Rectangle {
        id: tabs
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 62 * Theme.u
        color: "#121418"
        Rectangle { anchors { left: parent.left; right: parent.right; top: parent.top } height: 1; color: Theme.border }
        Row {
            anchors.fill: parent
            Repeater {
                model: win.layout.pages || []
                Item {
                    width: tabs.width / Math.max(1, (win.layout.pages || []).length); height: tabs.height
                    readonly property bool sel: index === win.current
                    Column {
                        anchors.centerIn: parent; spacing: 2 * Theme.u
                        Icon { anchors.horizontalCenter: parent.horizontalCenter; size: 26; name: modelData.icon
                               color: parent.parent.sel ? Theme.amber : Theme.textDim }
                        Label { anchors.horizontalCenter: parent.horizontalCenter; size: 11; text: modelData.title
                                color: parent.parent.sel ? Theme.amber : Theme.textDim }
                    }
                    MouseArea { anchors.fill: parent; onClicked: win.current = index }
                }
            }
        }
    }

    // Swallow touches while the screen is dark or dimmed (the waking tap shouldn't press anything).
    Timer {
        interval: 300; running: true; repeat: true
        onTriggered: {
            var s = Ha.readFile("/run/ha-kiosk-display/state").trim(); if (s !== "") win.displayState = s;
            // Remote tab switch for testing/automation: echo N > /run/ha-kiosk-display/page
            var p = Ha.readFile("/run/ha-kiosk-display/page").trim(); if (p !== "" && !isNaN(p)) win.current = parseInt(p);
        }
    }
    // Multi-touch gestures (see Panel.qml for the settings). Five passive PointHandlers each follow
    // one finger without taking the touch away from the tiles underneath; while 2+ fingers are down,
    // Panel.multi is set so tiles/bars/scrolling ignore the touch.
    //   2 fingers up/down: dim this panel's load (live), unless GESTURE_2_SWIPE_UP/DOWN are set
    //   3 fingers up/down: volume of this room's speakers (ROOM_SPEAKERS), unless GESTURE_3_SWIPE_UP/DOWN are set
    //   N-finger tap / swipe: GESTURE_<N>_TAP, GESTURE_<N>_SWIPE_<LEFT|RIGHT|UP|DOWN>
    Item {
        id: gest
        anchors.fill: parent; z: 50
        property int fingers: 0
        property int maxFingers: 0
        property point start
        property point cur
        property point curBefore
        property point end
        property double t0: 0
        property double tCount: 0            // when the finger count last changed
        property bool ended: false
        property string sliding: ""          // "", "dim" (2 fingers) or "volume" (3 fingers)
        property real slideStart: 0
        property real slideValue: 0
        property real sent: -1
        property string toast: ""
        readonly property var handlers: [p1, p2, p3, p4, p5]
        readonly property bool dimGesture: Panel.available
            && !Panel.gestures.GESTURE_2_SWIPE_UP && !Panel.gestures.GESTURE_2_SWIPE_DOWN
        readonly property bool volumeGesture: Panel.speakers.length > 0
            && !Panel.gestures.GESTURE_3_SWIPE_UP && !Panel.gestures.GESTURE_3_SWIPE_DOWN

        function update() {
            var n = 0, x = 0, y = 0;
            for (var i = 0; i < handlers.length; i++)
                if (handlers[i].active) { n++; x += handlers[i].point.position.x; y += handlers[i].point.position.y; }
            if (n > 0) cur = Qt.point(x / n, y / n);
            if (fingers === 0 && n > 0) { maxFingers = 0; ended = false; t0 = Date.now(); }
            if (n > maxFingers) { maxFingers = n; start = cur; end = cur; tCount = Date.now(); }   // new baseline per finger count
            if (n < maxFingers && !ended) { ended = true; end = curBefore; }   // first finger lifted: gesture is over
            if (!ended && n === maxFingers) end = cur;
            curBefore = cur;
            if (maxFingers >= 2) { Panel.multi = true; reset.stop(); }

            // Live sliders: 2 fingers = dim this switch's load, 3 fingers = room speaker volume.
            var mode = maxFingers === 2 && dimGesture ? "dim" : (maxFingers === 3 && volumeGesture ? "volume" : "");
            if (sliding && sliding !== mode) { sliding = ""; send.stop(); }   // a finger joined: drop the 2-finger slide
            if (mode && n === maxFingers && !ended) {
                var dy = start.y - cur.y, dx = cur.x - start.x;
                if (!sliding && Date.now() - tCount > 150
                        && Math.abs(dy) > 28 * Theme.u && Math.abs(dy) > 1.5 * Math.abs(dx)) {
                    sliding = mode; start = cur; sent = -1;
                    if (mode === "dim") slideStart = Panel.on ? Panel.level : 0;
                    else { Panel.captureVolumes(); slideStart = Panel.speakerVolume(); }
                }
                if (sliding) slideValue = Math.max(0, Math.min(1, slideStart + (start.y - cur.y) / (gest.height * 0.55)));
            }
            var was = fingers;
            fingers = n;
            if (n === 0 && was > 0) finish();
        }

        function push() {
            if (Math.abs(slideValue - sent) < 0.01) return;
            sent = slideValue;
            if (sliding === "dim") Panel.setLevel(slideValue);
            else if (sliding === "volume") Panel.setVolumeDelta(slideValue - slideStart);
        }

        function finish() {
            if (sliding) {
                send.stop(); push(); sliding = "";
            } else if (maxFingers >= 2) {
                var dx = end.x - start.x, dy = end.y - start.y, dist = Math.sqrt(dx * dx + dy * dy);
                var key = "";
                if (dist < 24 * Theme.u && Date.now() - t0 < 700) key = "GESTURE_" + maxFingers + "_TAP";
                else if (dist > 60 * Theme.u)
                    key = "GESTURE_" + maxFingers + "_SWIPE_" + (Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "RIGHT" : "LEFT") : (dy > 0 ? "DOWN" : "UP"));
                if (key) Panel.report(key);   // to HA as an MQTT event (automation trigger)
                var act = key ? Panel.gestures[key] : "";
                if (act && Panel.run(act, win)) { toast = act === "load.toggle" ? (Panel.name || "Light") : act.replace(/^.*[.:]/, "").replace(/_/g, " "); toastTimer.restart(); }
                else if (key) console.log("gesture: " + key + " (not set)");
            }
            reset.restart();
        }
        Timer { id: reset; interval: 250; onTriggered: Panel.multi = false }
        // Light: every 150 ms. Speakers go through HA and Sonos, so a little slower.
        Timer { id: send; interval: gest.sliding === "volume" ? 300 : 150; repeat: true; running: gest.sliding !== ""
                onTriggered: gest.push() }
        Timer { id: toastTimer; interval: 1200; onTriggered: gest.toast = "" }

        PointHandler { id: p1; acceptedDevices: PointerDevice.TouchScreen; onActiveChanged: gest.update(); onPointChanged: gest.update() }
        PointHandler { id: p2; acceptedDevices: PointerDevice.TouchScreen; onActiveChanged: gest.update(); onPointChanged: gest.update() }
        PointHandler { id: p3; acceptedDevices: PointerDevice.TouchScreen; onActiveChanged: gest.update(); onPointChanged: gest.update() }
        PointHandler { id: p4; acceptedDevices: PointerDevice.TouchScreen; onActiveChanged: gest.update(); onPointChanged: gest.update() }
        PointHandler { id: p5; acceptedDevices: PointerDevice.TouchScreen; onActiveChanged: gest.update(); onPointChanged: gest.update() }

        // Live readout while sliding (like the stock Brilliant swipe), or a short toast.
        Rectangle {
            anchors.centerIn: parent
            visible: gest.sliding !== "" || gest.toast !== ""
            width: 220 * Theme.u; height: gest.sliding ? 220 * Theme.u : 64 * Theme.u; radius: 28 * Theme.u
            color: "#e6181b21"; border.color: Theme.border
            Column {
                anchors.centerIn: parent; spacing: 6 * Theme.u
                Icon { anchors.horizontalCenter: parent.horizontalCenter; visible: gest.sliding !== ""; size: 44
                       name: gest.sliding === "volume" ? (gest.slideValue > 0 ? "volume-high" : "volume-off")
                                                       : (gest.slideValue > 0 ? "lightbulb-on" : "lightbulb-outline")
                       color: gest.slideValue > 0 ? (gest.sliding === "volume" ? Theme.blue : Theme.amber) : Theme.textDim }
                Label { anchors.horizontalCenter: parent.horizontalCenter; size: gest.sliding ? 44 : 16; font.weight: Font.Light
                        text: gest.sliding ? (gest.slideValue > 0.01 ? Math.round(gest.slideValue * 100) + "%" : (gest.sliding === "volume" ? "0%" : "Off"))
                                           : gest.toast }
                Label { anchors.horizontalCenter: parent.horizontalCenter; visible: gest.sliding !== ""; size: 13; color: Theme.textDim
                        text: gest.sliding === "volume" ? "Speakers" : Panel.name }
            }
        }
    }

    // Daytime idle: photo slideshow (display state "photos", see ha-kiosk-display.sh).
    Screensaver {
        anchors.fill: parent; z: 90
        active: win.displayState === "photos"
        visible: active
    }
    Item {
        anchors.fill: parent; z: 100
        visible: win.displayState !== "awake"
        MultiPointTouchArea { anchors.fill: parent; mouseEnabled: true }
        MouseArea { anchors.fill: parent }
    }
    // After waking from night/dim, go back to Home.
    onCurrentChanged: if (pages.currentIndex !== current) pages.currentIndex = current
    onDisplayStateChanged: if (displayState === "asleep" || displayState === "photos") current = 0
}
