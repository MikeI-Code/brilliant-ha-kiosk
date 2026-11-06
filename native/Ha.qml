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
