// Full-screen Home Assistant kiosk for the Brilliant panel.
// Settings: HA_URL / HA_ZOOM in /etc/ha-kiosk/kiosk.env (args may override). Run with qmlscene.
// Files:  /etc/ha-kiosk/token           long-lived HA token (optional; enables auto-login)
//         /run/ha-kiosk-display/state   "awake" | "dim" | "asleep", written by ha-kiosk-display.sh
// Needs env QML_XHR_ALLOW_FILE_READ=1 to read those files.
import QtQuick 2.15
import QtQuick.Window 2.15
import QtWebEngine 1.10

Window {
    id: win
    visible: true
    visibility: Window.FullScreen
    color: "#0e1013"

    // Settings come from /etc/ha-kiosk/kiosk.env (HA_URL, HA_ZOOM); command-line args override.
    property string homeUrl: "about:blank"
    property real zoom: 2.0

    function loadSettings() {
        var lines = readFile("/etc/ha-kiosk/kiosk.env").split("\n");
        for (var i = 0; i < lines.length; i++) {
            var m = lines[i].match(/^\s*(HA_URL|HA_ZOOM)\s*=\s*"?([^"#\s]+)"?/);
            if (!m) continue;
            if (m[1] === "HA_URL") homeUrl = m[2];
            else zoom = parseFloat(m[2]) || 2.0;
        }
        var a = Qt.application.arguments;
        for (var j = 1; j < a.length; j++) {
            if (a[j].indexOf("http") === 0) homeUrl = a[j];
            else if (/^[0-9.]+$/.test(a[j])) zoom = parseFloat(a[j]);
        }
    }
    property string token: ""
    property bool ready: false
    property string displayState: "awake"

    function readFile(path) {
        var x = new XMLHttpRequest();
        try {
            x.open("GET", "file://" + path, false);
            x.send();
            return (x.status === 200 || x.status === 0) ? x.responseText : "";
        } catch (e) {
            return "";
        }
    }

    // Seeds HA's frontend storage before its JS runs: token login, dark theme, hidden sidebar.
    property string bootstrapJs: token === "" ? "" :
        "(function(){try{" +
        "var o=location.origin,t='" + token + "';" +
        "var cur=JSON.parse(localStorage.getItem('hassTokens')||'null');" +
        "if(!cur||cur.access_token!==t){localStorage.setItem('hassTokens',JSON.stringify(" +
        "{access_token:t,token_type:'Bearer',expires_in:1800,hassUrl:o,clientId:o+'/',expires:9999999999999,refresh_token:''}));}" +
        "localStorage.setItem('selectedTheme',JSON.stringify({theme:'Panel Dark',dark:true}));" +
        "localStorage.setItem('dockedSidebar','\"always_hidden\"');" +
        "}catch(e){}})();"

    Component.onCompleted: {
        loadSettings();
        console.log("kiosk: url=" + homeUrl + " zoom=" + zoom);
        token =readFile("/etc/ha-kiosk/token").trim().replace(/[^A-Za-z0-9._-]/g, "");
        ready = true;
    }

    WebEngineProfile {
        id: haProfile
        storageName: "ha-kiosk"
        offTheRecord: false
        persistentStoragePath: "/var/lib/ha-kiosk/profile"
        cachePath: "/var/lib/ha-kiosk/cache"
        persistentCookiesPolicy: WebEngineProfile.ForcePersistentCookies
        httpCacheType: WebEngineProfile.DiskHttpCache
        httpCacheMaximumSize: 64 * 1024 * 1024
    }

    Loader {
        id: viewLoader
        anchors.fill: parent
        active: win.ready
        sourceComponent: WebEngineView {
            id: view
            profile: haProfile
            zoomFactor: win.zoom
            backgroundColor: "#0e1013"
            settings.touchIconsEnabled: false
            settings.showScrollBars: false
            settings.playbackRequiresUserGesture: false
            userScripts: win.bootstrapJs === "" ? [] : [bootstrap]
            WebEngineScript {
                id: bootstrap
                name: "ha-bootstrap"
                injectionPoint: WebEngineScript.DocumentCreation
                worldId: WebEngineScript.MainWorld
                sourceCode: win.bootstrapJs
            }
            Component.onCompleted: url = win.homeUrl
            onLoadingChanged: function(req) {
                if (req.status === WebEngineView.LoadFailedStatus) {
                    console.log("kiosk: load failed: " + req.errorString + " - retrying in 10s");
                    retry.restart();
                } else if (req.status === WebEngineView.LoadSucceededStatus) {
                    zoomFactor = win.zoom;
                    console.log("kiosk: loaded " + req.url);
                }
            }
            onRenderProcessTerminated: function(status, code) {
                console.log("kiosk: renderer died (" + status + "," + code + ") - reloading");
                retry.restart();
            }
            onFeaturePermissionRequested: function(origin, feature) {
                grantFeaturePermission(origin, feature, false);
            }
            onNewViewRequested: function(req) { url = req.requestedUrl; }
            Timer { id: retry; interval: 10000; onTriggered: parent.url = win.homeUrl }
        }
    }

    // While the screen is dark or dimmed, swallow touches so the waking tap doesn't press a button.
    Timer {
        interval: 300; running: true; repeat: true
        onTriggered: {
            var s = win.readFile("/run/ha-kiosk-display/state").trim();
            if (s !== "") win.displayState = s;
        }
    }
    Item {
        anchors.fill: parent
        visible: win.displayState !== "awake"
        z: 100
        MultiPointTouchArea { anchors.fill: parent; mouseEnabled: true }
        MouseArea { anchors.fill: parent }
    }
}
