// BETA full-screen weather, opened by tapping the Home sky. The sky fills the screen; swiping the
// hourly strip previews each hour's sky (condition + day/night). Tap ✕ or wait 2 min to close.
import QtQuick 2.15
import "."
import "WeatherNote.js" as WeatherNote

Rectangle {
    id: wp
    property string weatherEntity: ""
    property var w: Ha.get(weatherEntity)
    property var hourly: []
    property int sel: -1                  // -1 = now
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
    function hourLabel(i) { return i < 0 ? "Now" : WeatherNote.hourText(new Date(hourly[i].datetime)); }

    readonly property var h: sel >= 0 ? hourly[sel] : null
    readonly property string shownCond: h ? h.condition : (w ? w.state : "")
    readonly property var shownTemp: h ? h.temperature : (w ? w.attributes.temperature : undefined)
    readonly property string note: WeatherNote.note(hourly, w ? w.state : "", 12)
    readonly property string windUnit: w && w.attributes.wind_speed_unit ? w.attributes.wind_speed_unit : ""

    Connections { target: Ha; function onEntityUpdated(id) { if (id === wp.weatherEntity) wp.w = Ha.get(id); } }
    function load() {
        Ha.getForecast(weatherEntity, "hourly", function (l) { wp.hourly = l.slice(0, 24); });
    }
    Component.onCompleted: load()

    Sky {
        id: sky
        anchors.fill: parent
        radius: 0
        fxSize: 1.8
        condition: wp.shownCond
        phaseOverride: wp.h ? (wp.h.is_daytime === false ? "night" : (wp.h.is_daytime === true ? "day" : "")) : ""
        running: wp.visible
    }

    // Close
    Rectangle {
        anchors { top: parent.top; right: parent.right; margins: 16 * Theme.u }
        width: 44 * Theme.u; height: width; radius: width / 2
        color: Theme.card; border.color: Theme.border; z: 5
        Icon { anchors.centerIn: parent; size: 22; name: "close"; color: Theme.text }
        MouseArea { anchors { fill: parent; margins: -10 * Theme.u } onClicked: wp.closed() }
    }

    // Reading for "now" or the selected hour.
    Column {
        anchors { left: parent.left; top: parent.top; margins: 24 * Theme.u; topMargin: 36 * Theme.u }
        spacing: 4 * Theme.u
        z: 4
        Label { size: 16; color: Theme.textDim; text: wp.hourLabel(wp.sel) }
        Row {
            spacing: 10 * Theme.u
            Icon { size: 54; color: Theme.amber; name: wp.condIcon[wp.shownCond] || "weather-cloudy"; anchors.verticalCenter: parent.verticalCenter }
            Label { size: 76; font.weight: Font.Light; text: wp.shownTemp !== undefined ? Math.round(wp.shownTemp) + "°" : "--" }
        }
        Label { size: 22; text: wp.cond(wp.shownCond) }
        Row {
            spacing: 18 * Theme.u
            Label { size: 14; color: Theme.textDim; visible: !!wp.h && wp.h.precipitation_probability !== undefined
                    text: wp.h ? "Rain " + Math.round(wp.h.precipitation_probability || 0) + "%" : "" }
            Label { size: 14; color: Theme.textDim
                    text: { var s = wp.h ? wp.h.wind_speed : (wp.w ? wp.w.attributes.wind_speed : undefined);
                            return s !== undefined ? "Wind " + Math.round(s) + " " + wp.windUnit : ""; } }
            Label { size: 14; color: Theme.textDim
                    text: { var v = wp.h ? wp.h.humidity : (wp.w ? wp.w.attributes.humidity : undefined);
                            return v !== undefined ? "Humidity " + Math.round(v) + "%" : ""; } }
        }
        Label { size: 16; color: Theme.text; visible: wp.note !== "" && wp.sel < 0; text: wp.note; topPadding: 6 * Theme.u }
    }

    // Hourly strip: tap or swipe; the sky follows the selected hour.
    ListView {
        id: strip
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 16 * Theme.u; bottomMargin: 28 * Theme.u }
        height: 118 * Theme.u
        z: 4
        orientation: ListView.Horizontal
        spacing: 8 * Theme.u
        model: wp.hourly.length ? wp.hourly.length + 1 : 0      // 0 = now
        boundsBehavior: Flickable.StopAtBounds
        delegate: Rectangle {
            readonly property int hi: index - 1
            readonly property var d: hi >= 0 ? wp.hourly[hi] : null
            width: 72 * Theme.u; height: strip.height; radius: Theme.radius
            color: wp.sel === hi ? Theme.cardPressed : Theme.card
            border.color: wp.sel === hi ? Theme.amber : Theme.border
            border.width: Math.max(1, Theme.u * (wp.sel === hi ? 1 : 0.5))
            Column {
                anchors.centerIn: parent; spacing: 4 * Theme.u
                Label { anchors.horizontalCenter: parent.horizontalCenter; size: 12; color: Theme.textDim; text: wp.hourLabel(hi) }
                Icon { anchors.horizontalCenter: parent.horizontalCenter; size: 26; color: Theme.text
                       name: wp.condIcon[d ? d.condition : (wp.w ? wp.w.state : "")] || "weather-cloudy" }
                Label { anchors.horizontalCenter: parent.horizontalCenter; size: 15
                        text: d ? Math.round(d.temperature) + "°" : (wp.w ? Math.round(wp.w.attributes.temperature) + "°" : "") }
                Label { anchors.horizontalCenter: parent.horizontalCenter; size: 11; color: Theme.blue
                        text: d && d.precipitation_probability ? Math.round(d.precipitation_probability) + "%" : " " }
            }
            MouseArea { anchors.fill: parent; onClicked: wp.sel = hi }
        }
        // Swiping previews the hour under the strip's left edge.
        onContentXChanged: if (moving) { var i = indexAt(contentX + 36 * Theme.u, 10); if (i >= 0) wp.sel = i - 1; }
    }
}
