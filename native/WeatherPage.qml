// BETA full-screen weather, opened by tapping the Home sky. Shows what Home doesn't:
// conditions detail (wind/direction, humidity, pressure, visibility), sun and moon, a 24-hour
// temperature + rain-chance chart (drag along it to preview each hour's sky), and the 7-day
// forecast with day highs / night lows. The live sky stays behind; cards are opaque.
// Tap ✕ or leave it 2 min (idle) to close.
import QtQuick 2.15
import "."
import "WeatherNote.js" as WeatherNote

Rectangle {
    id: wp
    property string weatherEntity: ""
    property var w: Ha.get(weatherEntity)
    property var sun: Ha.get("sun.sun")
    property var hourly: []
    property var days: []                 // [{date, day, night}] from twice_daily (or daily)
    property int sel: -1                  // hour previewed in the sky; -1 = now
    signal closed()
    color: Theme.bg

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
    function a(k) { return w && w.attributes ? w.attributes[k] : undefined; }
    function compass(deg) {
        if (deg === undefined || deg === null) return "";
        return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round((((deg % 360) + 360) % 360) / 45) % 8];
    }
    function clock(iso) { return iso ? Qt.formatTime(new Date(iso), "h:mm AP") : "--"; }

    // Moon phase, computed locally (synodic month from a known new moon).
    readonly property var moon: {
        var d = (Date.now() - Date.UTC(2000, 0, 6, 18, 14)) / 86400000;
        var p = ((d % 29.530588) + 29.530588) % 29.530588 / 29.530588;   // 0 new .. 0.5 full
        var names = [["moon-new", "New moon"], ["moon-waxing-crescent", "Waxing crescent"],
                     ["moon-first-quarter", "First quarter"], ["moon-waxing-gibbous", "Waxing gibbous"],
                     ["moon-full", "Full moon"], ["moon-waning-gibbous", "Waning gibbous"],
                     ["moon-last-quarter", "Last quarter"], ["moon-waning-crescent", "Waning crescent"]];
        var n = names[Math.round(p * 8) % 8];
        return { icon: n[0], name: n[1], lit: Math.round((1 - Math.cos(2 * Math.PI * p)) / 2 * 100) };
    }
    readonly property string daylight: {
        if (!sun || !sun.attributes) return "";
        var ms = new Date(sun.attributes.next_setting) - new Date(sun.attributes.next_rising);
        if (ms < 0) ms += 86400000; if (ms > 86400000) ms -= 86400000;
        var m = Math.round(ms / 60000);
        return Math.floor(m / 60) + " h " + (m % 60) + " min";
    }

    readonly property var h: sel >= 0 && sel < hourly.length ? hourly[sel] : null
    readonly property string note: WeatherNote.note(hourly, w ? w.state : "", 12)

    Connections { target: Ha
        function onEntityUpdated(id) { if (id === wp.weatherEntity) wp.w = Ha.get(id); else if (id === "sun.sun") wp.sun = Ha.get(id); } }
    function load() {
        Ha.getForecast(weatherEntity, "hourly", function (l) { wp.hourly = l.slice(0, 24); });
        Ha.getForecast(weatherEntity, "twice_daily", function (l) {
            if (!l.length) {                         // integrations without twice_daily: use daily
                Ha.getForecast(weatherEntity, "daily", function (d) {
                    wp.days = d.slice(0, 7).map(function (e) { return { date: e.datetime, day: e,
                        night: e.templow !== undefined ? { temperature: e.templow } : null }; });
                });
                return;
            }
            var out = [], by = {};
            l.forEach(function (e) {
                var k = String(e.datetime).slice(0, 10), d = by[k];
                if (!d) { d = by[k] = { date: e.datetime, day: null, night: null }; out.push(d); }
                if (e.is_daytime === false) d.night = e; else d.day = e;
            });
            wp.days = out.slice(0, 7);
        });
    }
    Component.onCompleted: load()

    Sky {
        anchors.fill: parent
        radius: 0
        fxSize: 1.8
        touchable: false
        condition: wp.h ? wp.h.condition : (wp.w ? wp.w.state : "")
        phaseOverride: wp.h ? (wp.h.is_daytime === false ? "night" : (wp.h.is_daytime === true ? "day" : "")) : ""
        running: wp.visible
    }

    component Stat: Item {
        id: st
        property string icon: ""
        property string title: ""
        property string value: ""
        property string sub: ""
        property real arrow: -1            // wind bearing (degrees it comes FROM)
        width: (body.width - 2 * Theme.gap) / 3; height: 74 * Theme.u
        Rectangle { anchors.fill: parent; radius: Theme.radius; color: Theme.card; border.color: Theme.border; border.width: Math.max(1, Theme.u * 0.5) }
        Column {
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 * Theme.u }
            spacing: 2 * Theme.u
            Row { spacing: 4 * Theme.u
                Icon { size: 14; name: st.icon; color: Theme.textDim; anchors.verticalCenter: parent.verticalCenter }
                Label { size: 11; color: Theme.textDim; text: st.title.toUpperCase() } }
            Row { spacing: 4 * Theme.u
                Label { size: 18; text: st.value }
                Icon { visible: st.arrow >= 0; size: 16; name: "navigation"; color: Theme.blue
                       rotation: st.arrow + 180; anchors.verticalCenter: parent.verticalCenter } }
            Label { size: 11; color: Theme.textDim; text: st.sub; visible: text !== "" }
        }
    }

    Flickable {
        anchors.fill: parent
        contentHeight: body.height + 40 * Theme.u
        boundsBehavior: Flickable.StopAtBounds
        flickDeceleration: 4000
        Column {
            id: body
            x: 16 * Theme.u; y: 24 * Theme.u
            width: wp.width - 32 * Theme.u
            spacing: Theme.gap

            // Now / previewed hour
            Item {
                width: body.width; height: nowCol.height + 8 * Theme.u
                Column {
                    id: nowCol
                    spacing: 2 * Theme.u
                    Label { size: 15; color: Theme.textDim
                            text: wp.h ? WeatherNote.hourText(new Date(wp.h.datetime)) + " forecast" : "Now" }
                    Row {
                        spacing: 10 * Theme.u
                        Icon { size: 46; color: Theme.amber; anchors.verticalCenter: parent.verticalCenter
                               name: wp.condIcon[wp.h ? wp.h.condition : (wp.w ? wp.w.state : "")] || "weather-cloudy" }
                        Label { size: 64; font.weight: Font.Light
                                text: { var t = wp.h ? wp.h.temperature : wp.a("temperature"); return t !== undefined ? Math.round(t) + "°" : "--"; } }
                    }
                    Label { size: 20; text: wp.cond(wp.h ? wp.h.condition : (wp.w ? wp.w.state : "")) }
                    Label { size: 15; color: Theme.text; visible: wp.note !== "" && !wp.h; text: wp.note }
                }
            }

            // Conditions detail (not on Home)
            Flow {
                width: body.width; spacing: Theme.gap
                Stat { icon: "weather-windy"; title: "Wind"
                       value: wp.a("wind_speed") !== undefined ? Math.round(wp.a("wind_speed")) + " " + (wp.a("wind_speed_unit") || "") : "--"
                       sub: wp.compass(wp.a("wind_bearing")) ? "from " + wp.compass(wp.a("wind_bearing")) : ""
                       arrow: wp.a("wind_bearing") !== undefined ? wp.a("wind_bearing") : -1 }
                Stat { icon: "water-percent"; title: "Humidity"
                       value: wp.a("humidity") !== undefined ? Math.round(wp.a("humidity")) + "%" : "--"
                       sub: wp.a("dew_point") !== undefined ? "Dew point " + Math.round(wp.a("dew_point")) + "°" : "" }
                Stat { icon: "gauge"; title: "Pressure"
                       value: wp.a("pressure") !== undefined ? Number(wp.a("pressure")).toFixed(2) : "--"
                       sub: wp.a("pressure_unit") || "" }
                Stat { icon: "eye-outline"; title: "Visibility"
                       value: wp.a("visibility") !== undefined ? Math.round(wp.a("visibility")) + " " + (wp.a("visibility_unit") || "") : "--" }
                Stat { icon: "weather-sunset-up"; title: "Sunrise"
                       value: wp.sun ? wp.clock(wp.sun.attributes.next_rising) : "--"
                       sub: wp.sun ? "Sunset " + wp.clock(wp.sun.attributes.next_setting) : "" }
                Stat { icon: wp.moon.icon; title: "Moon"; value: wp.moon.lit + "%"; sub: wp.moon.name }
            }
            Label { visible: wp.daylight !== ""; size: 12; color: Theme.textDim; leftPadding: 4 * Theme.u
                    text: "Daylight " + wp.daylight }

            // 24-hour chart: temperature line + rain-chance bars. Drag along it to preview the sky.
            Rectangle {
                width: body.width; height: 150 * Theme.u
                radius: Theme.radius; color: Theme.card; border.color: Theme.border; border.width: Math.max(1, Theme.u * 0.5)
                visible: wp.hourly.length > 1
                Label { x: 12 * Theme.u; y: 8 * Theme.u; size: 12; color: Theme.textDim; text: "NEXT 24 HOURS · drag to preview the sky" }
                Canvas {
                    id: chart
                    anchors { fill: parent; topMargin: 30 * Theme.u; bottomMargin: 22 * Theme.u; leftMargin: 14 * Theme.u; rightMargin: 14 * Theme.u }
                    property var pts: wp.hourly
                    property int mark: wp.sel
                    onPtsChanged: requestPaint()
                    onMarkChanged: requestPaint()
                    onWidthChanged: requestPaint()
                    function xAt(i) { return pts.length > 1 ? i * width / (pts.length - 1) : 0; }
                    onPaint: {
                        var c = getContext("2d"); c.reset();
                        var d = pts, n = d.length; if (n < 2) return;
                        var lo = 1e9, hi = -1e9, i;
                        for (i = 0; i < n; i++) { lo = Math.min(lo, d[i].temperature); hi = Math.max(hi, d[i].temperature); }
                        if (hi - lo < 4) { hi += 2; lo -= 2; }
                        var top = 16 * Theme.u, bot = height - 4 * Theme.u;
                        var yAt = function (t) { return bot - (t - lo) / (hi - lo) * (bot - top); };
                        c.fillStyle = "#3a5e8a";                         // rain-chance bars
                        var bw = Math.max(2, width / n * 0.55);
                        for (i = 0; i < n; i++) {
                            var pp = d[i].precipitation_probability || 0;
                            if (pp > 0) c.fillRect(xAt(i) - bw / 2, height - pp / 100 * height * 0.6, bw, pp / 100 * height * 0.6);
                        }
                        c.strokeStyle = "#f5b54a"; c.lineWidth = 2 * Theme.u; c.beginPath();   // temperature
                        for (i = 0; i < n; i++) { if (i) c.lineTo(xAt(i), yAt(d[i].temperature)); else c.moveTo(xAt(i), yAt(d[i].temperature)); }
                        c.stroke();
                        c.fillStyle = "#eef0f3"; c.font = (11 * Theme.u) + "px 'Open Sans'"; c.textAlign = "center";
                        for (i = 0; i < n; i += 6) c.fillText(Math.round(d[i].temperature) + "°", xAt(i), yAt(d[i].temperature) - 6 * Theme.u);
                        if (mark >= 0 && mark < n) {
                            c.strokeStyle = "#eef0f3"; c.lineWidth = Math.max(1, Theme.u); c.beginPath();
                            c.moveTo(xAt(mark), 0); c.lineTo(xAt(mark), height); c.stroke();
                            c.fillStyle = "#f5b54a"; c.beginPath(); c.arc(xAt(mark), yAt(d[mark].temperature), 4 * Theme.u, 0, 2 * Math.PI); c.fill();
                            c.fillStyle = "#eef0f3";
                            c.fillText((d[mark].precipitation_probability || 0) + "% rain", Math.max(30 * Theme.u, Math.min(width - 30 * Theme.u, xAt(mark))), 10 * Theme.u);
                        }
                    }
                }
                Repeater {
                    model: wp.hourly.length > 1 ? Math.ceil(wp.hourly.length / 6) : 0
                    Label {
                        readonly property int i: index * 6
                        x: chart.x + chart.xAt(i) - width / 2; y: parent.height - 19 * Theme.u
                        size: 11; color: Theme.textDim
                        text: i === 0 ? "Now" : WeatherNote.hourText(new Date(wp.hourly[i].datetime))
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    preventStealing: true
                    function pick(mx) { var n = wp.hourly.length; if (n < 2) return;
                        wp.sel = Math.max(0, Math.min(n - 1, Math.round((mx - chart.x) / chart.width * (n - 1)))); }
                    onPressed: { backToNow.stop(); pick(mouse.x); }
                    onPositionChanged: pick(mouse.x)
                    onReleased: backToNow.restart()
                    onCanceled: backToNow.restart()
                }
                Timer { id: backToNow; interval: 6000; onTriggered: wp.sel = -1 }
            }

            // 7-day forecast: day high / night low on a shared range bar, rain chance.
            Rectangle {
                id: week
                width: body.width; height: weekCol.height + 20 * Theme.u
                radius: Theme.radius; color: Theme.card; border.color: Theme.border; border.width: Math.max(1, Theme.u * 0.5)
                visible: wp.days.length > 0
                readonly property real wlo: { var v = 1e9; wp.days.forEach(function (d) { if (d.night) v = Math.min(v, d.night.temperature); if (d.day) v = Math.min(v, d.day.temperature); }); return v; }
                readonly property real whi: { var v = -1e9; wp.days.forEach(function (d) { if (d.day) v = Math.max(v, d.day.temperature); if (d.night) v = Math.max(v, d.night.temperature); }); return v; }
                readonly property real span: Math.max(1, whi - wlo)
                Column {
                    id: weekCol
                    x: 12 * Theme.u; y: 10 * Theme.u; width: parent.width - 24 * Theme.u
                    spacing: 2 * Theme.u
                    Label { size: 12; color: Theme.textDim; text: wp.days.length + "-DAY FORECAST"; bottomPadding: 4 * Theme.u }
                    Repeater {
                        model: wp.days
                        Item {
                            id: dayRow
                            readonly property var dy: modelData.day
                            readonly property var nt: modelData.night
                            readonly property real hiT: dy ? dy.temperature : (nt ? nt.temperature : 0)
                            readonly property real loT: nt ? nt.temperature : hiT
                            readonly property int pop: Math.max((dy && dy.precipitation_probability) || 0, (nt && nt.precipitation_probability) || 0)
                            width: weekCol.width; height: 38 * Theme.u
                            Label { id: dn; width: 56 * Theme.u; anchors.verticalCenter: parent.verticalCenter; size: 15
                                    text: index === 0 ? "Today" : Qt.formatDate(new Date(modelData.date), "ddd") }
                            Icon { id: di; x: dn.width; anchors.verticalCenter: parent.verticalCenter; size: 22; color: Theme.text
                                   name: wp.condIcon[(dayRow.dy || dayRow.nt || {}).condition] || "weather-cloudy" }
                            Label { id: dp; x: di.x + 30 * Theme.u; width: 38 * Theme.u; anchors.verticalCenter: parent.verticalCenter
                                    size: 12; color: Theme.blue; text: dayRow.pop >= 10 ? dayRow.pop + "%" : "" }
                            Label { id: lo; x: dp.x + dp.width; width: 34 * Theme.u; horizontalAlignment: Text.AlignRight
                                    anchors.verticalCenter: parent.verticalCenter; size: 15; color: Theme.textDim
                                    text: dayRow.nt ? Math.round(dayRow.loT) + "°" : "" }
                            Rectangle {
                                id: track
                                x: lo.x + lo.width + 8 * Theme.u; width: dayRow.width - x - 44 * Theme.u
                                height: 5 * Theme.u; radius: height / 2; color: Theme.track
                                anchors.verticalCenter: parent.verticalCenter
                                Rectangle {
                                    height: parent.height; radius: height / 2; color: Theme.amber
                                    x: (dayRow.loT - week.wlo) / week.span * track.width
                                    width: Math.max(height, (dayRow.hiT - dayRow.loT) / week.span * track.width)
                                }
                            }
                            Label { x: dayRow.width - 36 * Theme.u; width: 36 * Theme.u; horizontalAlignment: Text.AlignRight
                                    anchors.verticalCenter: parent.verticalCenter; size: 15
                                    text: dayRow.dy ? Math.round(dayRow.hiT) + "°" : "" }
                        }
                    }
                }
            }
            Label { size: 11; color: Theme.textFaint; text: wp.a("attribution") || ""; leftPadding: 4 * Theme.u }
        }
    }

    // Close (fixed, above the scrolling content)
    Rectangle {
        anchors { top: parent.top; right: parent.right; margins: 16 * Theme.u }
        width: 44 * Theme.u; height: width; radius: width / 2
        color: Theme.card; border.color: Theme.border
        Icon { anchors.centerIn: parent; size: 22; name: "close"; color: Theme.text }
        MouseArea { anchors { fill: parent; margins: -10 * Theme.u } onClicked: wp.closed() }
    }
}
