pragma Singleton
// Home Assistant websocket client: auth, state cache, live updates, service calls.
import QtQuick 2.15
import QtWebSockets 1.1

Item {
    id: ha

    property string baseUrl: ""          // e.g. http://192.168.1.10:8123
    property string token: ""
    property bool connected: false
    property string status: "connecting"   // connecting | connected | reconnecting | auth_failed | not_configured
    property double downSince: 0           // ms when the connection was lost (for the banner)
    property var _svc: ({})                // ids of call_service requests, to report failures
    property var states: ({})            // entity_id -> state object (mutated in place; use signals)
    property var _pending: ({})
    property int _nextId: 1

    signal entityUpdated(string entityId)
    signal statesLoaded()
    signal lovelaceUpdated(string urlPath)   // a dashboard was saved in HA
    signal callFailed(int id, string message) // a service call was rejected by HA

    function readUrl(url) {
        var x = new XMLHttpRequest();
        try { x.open("GET", url, false); x.send(); return (x.status === 200 || x.status === 0) ? x.responseText : ""; }
        catch (e) { return ""; }
    }
    function readFile(path) { return readUrl("file://" + path); }

    function start() {
        if (sock.active) return;
        var env = readFile("/etc/ha-kiosk/kiosk.env");
        var m = env.match(/^\s*HA_URL\s*=\s*"?(https?:\/\/[^\/\s"]+)/m);
        baseUrl = m ? m[1] : "";
        token = readFile("/etc/ha-kiosk/token").trim().replace(/[^A-Za-z0-9._-]/g, "");
        console.log("ha: base=" + baseUrl + " token=" + (token ? "yes" : "MISSING"));
        if (baseUrl && token) sock.active = true; else status = "not_configured";
    }

    function get(entityId) { return states[entityId] || null; }

    // Some cameras (e.g. CuboAI) only return a still while their live stream is running.
    // For a camera whose still failed, keep HA's HLS stream alive while it is on screen:
    // ask for the stream once, then fetch only the small playlist (never the video), which
    // resets HA's 30 s idle timer. The panel decodes nothing.
    property var _warm: ({})             // entity -> playlist url ("" while asking)
    function stillFailed(entityId) { if (!(entityId in _warm)) { _warm[entityId] = ""; keepWarm(entityId); } }
    function keepWarm(entityId) {
        if (!(entityId in _warm)) return;
        var s = get(entityId);
        if (!s || !((s.attributes.supported_features || 0) & 2)) return;   // no STREAM support
        var url = _warm[entityId];
        if (url) {
            var x = new XMLHttpRequest();
            x.onreadystatechange = function () {
                if (x.readyState === XMLHttpRequest.DONE && x.status !== 200) _warm[entityId] = "";  // expired: re-ask
            };
            x.open("GET", baseUrl + url); x.send();
            return;
        }
        send({ type: "camera/stream", entity_id: entityId, format: "hls" }, function (r) {
            if (r.success && r.result && r.result.url) _warm[entityId] = r.result.url;
            else console.log("ha: no stream for " + entityId + ": " + JSON.stringify(r.error));
        });
    }

    function send(msg, cb) {
        if (sock.status !== WebSocket.Open) return -1;
        msg.id = _nextId++;
        if (cb) _pending[msg.id] = cb;
        sock.sendTextMessage(JSON.stringify(msg));
        return msg.id;
    }

    function callService(domain, service, data, entityId, cb) {
        var msg = { type: "call_service", domain: domain, service: service };
        if (data) msg.service_data = data;
        if (entityId) msg.target = { entity_id: entityId };
        if (cb) msg.return_response = true;
        var id = send(msg, cb);
        if (id < 0) callFailed(-1, "Not connected to Home Assistant");
        else _svc[id] = true;
        return id;
    }

    // Background forecast fetch: no "Couldn't do that" toast on failure. cb(list) gets [] on failure.
    // type "daily" falls back to "twice_daily" (e.g. NWS), merged into one entry per day with
    // temperature = daytime high and templow = night low.
    function getForecast(entityId, type, cb) {
        function req(t, done) {
            var id = send({ type: "call_service", domain: "weather", service: "get_forecasts",
                            service_data: { type: t }, target: { entity_id: entityId }, return_response: true },
                          function (r) {
                              var f = r.success && r.result && r.result.response && r.result.response[entityId];
                              done(f ? f.forecast || [] : null);
                          });
            if (id < 0) done(null);
        }
        req(type, function (list) {
            if (list || type !== "daily") return cb(list || []);
            req("twice_daily", function (tw) {
                var days = [], byDay = {};
                (tw || []).forEach(function (e) {
                    var k = String(e.datetime).slice(0, 10), d = byDay[k];
                    if (!d) { d = byDay[k] = { datetime: e.datetime, condition: e.condition, temperature: e.temperature,
                                                templow: undefined, precipitation_probability: e.precipitation_probability };
                              days.push(d); }
                    if (e.is_daytime === false) d.templow = e.temperature;
                    else { d.temperature = e.temperature; d.condition = e.condition; }
                });
                cb(days);
            });
        });
    }

    function _handle(m) {
        if (m.type === "auth_required") {
            sock.sendTextMessage(JSON.stringify({ type: "auth", access_token: token }));
        } else if (m.type === "auth_ok") {
            connected = true; status = "connected"; downSince = 0;
            console.log("ha: connected, HA " + m.ha_version);
            send({ type: "get_states" }, function (r) {
                var s = {};
                for (var i = 0; i < r.result.length; i++) s[r.result[i].entity_id] = r.result[i];
                states = s;
                statesLoaded();
            });
            send({ type: "subscribe_events", event_type: "state_changed" });
            send({ type: "subscribe_events", event_type: "lovelace_updated" }, function (r) {
                if (!r.success) console.log("ha: no live layout updates (token user must be an admin)");
            });
        } else if (m.type === "auth_invalid") {
            console.log("ha: auth invalid: " + m.message);
            status = "auth_failed";
        } else if (m.type === "event" && m.event && m.event.event_type === "state_changed") {
            var d = m.event.data;
            if (d.new_state) states[d.entity_id] = d.new_state; else delete states[d.entity_id];
            entityUpdated(d.entity_id);
        } else if (m.type === "event" && m.event && m.event.event_type === "lovelace_updated") {
            lovelaceUpdated((m.event.data && m.event.data.url_path) || "");
        } else if (m.type === "result") {
            var cb = _pending[m.id];
            var svc = _svc[m.id]; delete _svc[m.id];
            if (cb) { delete _pending[m.id]; cb(m); }
            else if (!m.success) console.log("ha: call failed: " + JSON.stringify(m.error));
            if (svc && !m.success) callFailed(m.id, (m.error && m.error.message) || "Request failed");
        }
    }

    WebSocket {
        id: sock
        active: false
        url: ha.baseUrl.replace(/^http/, "ws") + "/api/websocket"
        onTextMessageReceived: function (message) { ha._handle(JSON.parse(message)); }
        onStatusChanged: {
            if (status === WebSocket.Error || status === WebSocket.Closed) {
                if (ha.connected || status === WebSocket.Error)
                    console.log("ha: socket " + (status === WebSocket.Error ? "error: " + errorString : "closed"));
                ha.connected = false;
                ha._pending = {}; ha._svc = {};
                if (ha.status !== "auth_failed") ha.status = ha.downSince ? "reconnecting" : "connecting";
                if (!ha.downSince) ha.downSince = Date.now();
                reconnect.restart();
            }
        }
    }
    Timer { id: reconnect; interval: 5000; onTriggered: { sock.active = false; sock.active = true; } }
}
