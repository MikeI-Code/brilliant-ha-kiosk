pragma Singleton
// This panel itself: its wired load (via the local bridge, no HA in the path), multi-touch
// gesture settings, and the "multi-finger gesture in progress" flag that suppresses taps.
//
// kiosk.env:
//   LOAD_ENTITY=light.kitchen_table   HA entity of this panel's load; tiles for it use local control
//   ROOM_SPEAKERS=media_player.kitchen  this room's speakers; three fingers up/down sets their volume
//   GESTURE_2_TAP=load.toggle         two-finger tap
//   GESTURE_3_TAP=…                   three-finger tap
//   GESTURE_<2|3>_SWIPE_<LEFT|RIGHT|UP|DOWN>=…   (two-finger up/down = dim the load unless set)
// Actions: load.toggle | load.on | load.off | page:<n> | <domain>.<service>[:<entity_id>]
import QtQuick 2.15
import "."

Item {
    id: panel
    readonly property string bridge: "http://127.0.0.1:8765/load"
    property bool available: false
    property bool on: false
    property real level: 0               // 0..1
    property string name: ""
    property string loadEntity: ""
    property var gestures: ({})
    property bool multi: false           // a gesture with 2+ fingers is in progress
    property var speakers: []            // ROOM_SPEAKERS: this room's Sonos speakers (3-finger volume)
    property var _volStart: ({})
    signal changed()

    // Room speaker volume (0..1): the loudest of the room's speakers.
    function speakerVolume() {
        var v = 0;
        for (var i = 0; i < speakers.length; i++) {
            var s = Ha.get(speakers[i]);
            if (s && s.attributes.volume_level !== undefined) v = Math.max(v, s.attributes.volume_level);
        }
        return v;
    }
    function captureVolumes() {
        var m = {};
        for (var i = 0; i < speakers.length; i++) {
            var s = Ha.get(speakers[i]);
            m[speakers[i]] = s && s.attributes.volume_level !== undefined ? s.attributes.volume_level : 0;
        }
        _volStart = m;
    }
    // Move every room speaker by the same amount from where it started, keeping their balance.
    function setVolumeDelta(delta) {
        for (var e in _volStart)
            Ha.callService("media_player", "volume_set",
                           { volume_level: Math.round(Math.max(0, Math.min(1, _volStart[e] + delta)) * 100) / 100 }, e);
    }

    function _req(method, body, cb) {
        var x = new XMLHttpRequest();
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return;
            if (x.status === 200) {
                var s = JSON.parse(x.responseText);
                available = true; on = s.on; level = s.on ? s.brightness / 100 : 0; name = s.name || name;
                changed();
            } else if (x.status === 0 || x.status >= 500) {
                available = false;
            }
            if (cb) cb();
        };
        x.open(method, bridge);
        x.send(body ? JSON.stringify(body) : null);
    }
    function refresh() { _req("GET"); }
    // Tell the bridge about a gesture ("GESTURE_3_TAP" -> "3_tap") so HA automations can use it.
    function report(key) {
        var x = new XMLHttpRequest();
        x.open("POST", "http://127.0.0.1:8765/gesture");
        x.send(JSON.stringify({ name: key.replace(/^GESTURE_/, "").toLowerCase() }));
    }
    function setLoad(body) { _req("POST", body); }
    function toggle() { setLoad({ toggle: true }); }
    function setLevel(v) { setLoad(v < 0.02 ? { on: false } : { brightness: Math.round(v * 100) }); }

    // Run a configured action string.
    function run(action, win) {
        if (!action) return false;
        if (action === "load.toggle") toggle();
        else if (action === "load.on") setLoad({ on: true });
        else if (action === "load.off") setLoad({ on: false });
        else if (action.indexOf("page:") === 0) win.current = parseInt(action.substr(5)) || 0;
        else {
            var m = /^([a-z_]+)\.([a-z_]+)(?::(.+))?$/.exec(action);
            if (!m) { console.log("gesture: bad action " + action); return false; }
            Ha.callService(m[1], m[2], null, m[3] || null);
        }
        console.log("gesture: " + action);
        return true;
    }

    Component.onCompleted: {
        var env = Ha.readFile("/etc/ha-kiosk/kiosk.env"), g = {}, re = /^\s*(GESTURE_[A-Z0-9_]+)\s*=\s*"?([^"\n#]*)"?/gm, m;
        while ((m = re.exec(env)) !== null) g[m[1]] = m[2].trim();
        if (g.GESTURE_2_TAP === undefined) g.GESTURE_2_TAP = "load.toggle";
        gestures = g;
        var le = /^\s*LOAD_ENTITY\s*=\s*"?([a-z_]+\.[A-Za-z0-9_]+)/m.exec(env);
        loadEntity = le ? le[1] : "";
        var sp = /^\s*ROOM_SPEAKERS\s*=\s*"?([^"\n#]*)/m.exec(env);
        speakers = sp ? sp[1].split(",").map(function (x) { return x.trim(); }).filter(function (x) { return x; }) : [];
        refresh();
    }
    // Keep in step with changes made elsewhere (HA, the Brilliant app).
    Timer { interval: 3000; running: true; repeat: true; onTriggered: panel.refresh() }
}
