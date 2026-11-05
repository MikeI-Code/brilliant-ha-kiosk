// Photo screensaver: crossfading slideshow of the panel's photos with a clock and current weather.
// Weather comes from the layout's weather entity; SCREENSAVER_WEATHER=0 in kiosk.env hides it.
// Playlist: /run/ha-kiosk-display/photos.txt (written by ha-kiosk-display.sh).
// Shown while the display state is "photos"; the waking tap is swallowed by main.qml.
import QtQuick 2.15
import "."

Rectangle {
    id: ss
    color: "black"
    property bool active: false
    property int photoSecs: 15
    property var photos: []
    property int pos: 0
    property bool frontIsA: true
    property date now: new Date()
    property string weatherEntity: ""
    property bool showWeather: true
    property var w: null
    property var today: null
    readonly property var condIcon: ({
        "clear-night": "weather-night", "cloudy": "weather-cloudy", "exceptional": "alert-circle-outline",
        "fog": "weather-fog", "hail": "weather-hail", "lightning": "weather-lightning",
        "lightning-rainy": "weather-lightning-rainy", "partlycloudy": "weather-partly-cloudy",
        "pouring": "weather-pouring", "rainy": "weather-rainy", "snowy": "weather-snowy",
        "snowy-rainy": "weather-snowy-rainy", "sunny": "weather-sunny", "windy": "weather-windy",
        "windy-variant": "weather-windy-variant" })
    readonly property var condText: ({
        "clear-night": "Clear", "partlycloudy": "Partly cloudy", "lightning-rainy": "Storms",
        "snowy-rainy": "Sleet", "windy-variant": "Windy", "exceptional": "Alert" })
    function cond(s) { return condText[s] || (s ? s.charAt(0).toUpperCase() + s.slice(1) : ""); }
    function loadWeather() {
        w = weatherEntity ? Ha.get(weatherEntity) : null;
        if (!weatherEntity || !showWeather) return;
        Ha.callService("weather", "get_forecasts", { type: "daily" }, weatherEntity, function (r) {
            var f = r.success && r.result && r.result.response && r.result.response[ss.weatherEntity];
            ss.today = f && f.forecast && f.forecast.length ? f.forecast[0] : null;
        });
    }
    Connections {
        target: Ha
        function onEntityUpdated(id) { if (ss.active && id === ss.weatherEntity) ss.w = Ha.get(id); }
    }

    function shuffle(a) {
        for (var i = a.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t;
        }
        return a;
    }
    function load() {
        var env = Ha.readFile("/etc/ha-kiosk/kiosk.env");
        var m = /^PHOTO_SECS=(\d+)/m.exec(env); if (m) photoSecs = Math.max(5, parseInt(m[1]));
        showWeather = !/^SCREENSAVER_WEATHER=["']?(0|off|false|no)\b/mi.test(env);
        var list = Ha.readFile("/run/ha-kiosk-display/photos.txt").split("\n")
                     .map(function (s) { return s.trim(); }).filter(function (s) { return s !== ""; });
        photos = shuffle(list); pos = 0;
        console.log("screensaver: " + photos.length + " photos, " + photoSecs + "s each");
    }
    // Load the next photo into the hidden image; it fades in once decoded.
    function advance() {
        if (photos.length === 0) return;
        var back = frontIsA ? imgB : imgA;
        back.source = "file://" + photos[pos % photos.length];
        pos = (pos + 1) % photos.length;
        if (pos === 0) photos = shuffle(photos);
    }
    function swapIfReady(img) {
        if (img.status !== Image.Ready || img === (frontIsA ? imgA : imgB)) return;
        frontIsA = (img === imgA);
    }

    onActiveChanged: {
        if (active) { load(); loadWeather(); imgA.source = ""; imgB.source = ""; frontIsA = false; advance(); }
        else { imgA.source = ""; imgB.source = ""; }   // free the decoded images
    }

    Image {
        id: imgA
        anchors.fill: parent
        fillMode: Image.PreserveAspectCrop
        asynchronous: true; cache: false
        sourceSize: Qt.size(ss.width, ss.height)
        opacity: ss.frontIsA ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 1200; easing.type: Easing.InOutQuad } }
        onStatusChanged: ss.swapIfReady(imgA)
    }
    Image {
        id: imgB
        anchors.fill: parent
        fillMode: Image.PreserveAspectCrop
        asynchronous: true; cache: false
        sourceSize: Qt.size(ss.width, ss.height)
        opacity: ss.frontIsA ? 0 : 1
        Behavior on opacity { NumberAnimation { duration: 1200; easing.type: Easing.InOutQuad } }
        onStatusChanged: ss.swapIfReady(imgB)
    }

    // Clock over a soft gradient so it reads on bright photos.
    Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 220 * Theme.u
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#00000000" }
            GradientStop { position: 1.0; color: "#99000000" }
        }
    }
    Column {
        anchors { left: parent.left; bottom: parent.bottom; leftMargin: 24 * Theme.u; bottomMargin: 28 * Theme.u }
        Label { size: 64; font.weight: Font.Light; color: "white"
                text: Qt.formatTime(ss.now, "h:mm AP").replace(/\s*[AP]M$/i, "") }
        Label { size: 16; color: "#e0e0e0"; text: Qt.formatDate(ss.now, "dddd, MMMM d") }
    }
    Column {
        visible: ss.showWeather && ss.w !== null && ss.w.state !== "unavailable"
        anchors { right: parent.right; bottom: parent.bottom; rightMargin: 24 * Theme.u; bottomMargin: 28 * Theme.u }
        spacing: 2 * Theme.u
        Row {
            anchors.right: parent.right; spacing: 8 * Theme.u
            Icon { size: 44; color: "white"; anchors.verticalCenter: parent.verticalCenter
                   name: ss.w ? (ss.condIcon[ss.w.state] || "weather-cloudy") : "weather-cloudy" }
            Label { size: 48; font.weight: Font.Light; color: "white"
                    text: ss.w && ss.w.attributes.temperature !== undefined ? Math.round(ss.w.attributes.temperature) + "°" : "" }
        }
        Label { anchors.right: parent.right; size: 16; color: "#e0e0e0"
                text: ss.w ? ss.cond(ss.w.state) + (ss.today && ss.today.templow !== undefined
                      ? "  ·  " + Math.round(ss.today.temperature) + "° / " + Math.round(ss.today.templow) + "°" : "") : "" }
    }

    Timer { interval: ss.photoSecs * 1000; running: ss.active; repeat: true; onTriggered: ss.advance() }
    Timer { interval: 1000; running: ss.active; repeat: true; triggeredOnStart: true; onTriggered: ss.now = new Date() }
    Timer { interval: 30 * 60 * 1000; running: ss.active; repeat: true; onTriggered: ss.loadWeather() }
}
