// Home header: big clock, date, current weather, inside temp, 5-day forecast.
import QtQuick 2.15
import "."

Item {
    id: hero
    property string weatherEntity: ""
    property string insideEntity: ""
    property var w: null
    property var inside: null
    property var forecast: []
    property date now: new Date()
    height: col.height

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

    function refresh() {
        w = Ha.get(weatherEntity);
        inside = Ha.get(insideEntity);
    }
    function loadForecast() {
        if (!weatherEntity) return;
        Ha.callService("weather", "get_forecasts", { type: "daily" }, weatherEntity, function (r) {
            if (r.success && r.result && r.result.response && r.result.response[hero.weatherEntity])
                hero.forecast = r.result.response[hero.weatherEntity].forecast.slice(0, 5);
        });
    }
    Connections {
        target: Ha
        function onEntityUpdated(id) { if (id === hero.weatherEntity || id === hero.insideEntity) hero.refresh(); }
        function onStatesLoaded() { hero.refresh(); hero.loadForecast(); }
    }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: hero.now = new Date() }
    Timer { interval: 30 * 60 * 1000; running: true; repeat: true; onTriggered: hero.loadForecast() }
    Component.onCompleted: { refresh(); if (Ha.connected) loadForecast(); }

    Column {
        id: col
        width: parent.width
        spacing: 12 * Theme.u

        Item {
            width: parent.width; height: clock.height + date.height
            Label { id: clock; size: 64; font.weight: Font.Light; text: Qt.formatTime(hero.now, "h:mm AP").replace(/\s*[AP]M$/i, "") }
            Label { id: date; anchors.top: clock.bottom; size: 16; color: Theme.textDim; text: Qt.formatDate(hero.now, "dddd, MMMM d") }
            Column {
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                spacing: 2 * Theme.u
                Row {
                    anchors.right: parent.right; spacing: 6 * Theme.u
                    Icon { size: 34; color: Theme.amber; name: hero.w ? (hero.condIcon[hero.w.state] || "weather-cloudy") : "weather-cloudy"; anchors.verticalCenter: parent.verticalCenter }
                    Label { size: 30; font.weight: Font.Light; text: hero.w ? Math.round(hero.w.attributes.temperature) + "°" : "--" }
                }
                Label { anchors.right: parent.right; size: 12; color: Theme.textDim; text: hero.w ? hero.cond(hero.w.state) : "" }
                Label { anchors.right: parent.right; size: 12; color: Theme.textDim
                        text: hero.inside ? "Inside " + Math.round(parseFloat(hero.inside.state)) + "°" : "" }
            }
        }

        Rectangle {
            width: parent.width; height: 92 * Theme.u; radius: Theme.radius
            color: Theme.card; border.color: Theme.border; border.width: Math.max(1, Theme.u * 0.5)
            visible: hero.forecast.length > 0
            Row {
                anchors.fill: parent; anchors.margins: 8 * Theme.u
                Repeater {
                    model: hero.forecast
                    Column {
                        width: parent.width / Math.max(1, hero.forecast.length)
                        spacing: 2 * Theme.u
                        Label { anchors.horizontalCenter: parent.horizontalCenter; size: 12; color: Theme.textDim
                                text: Qt.formatDate(new Date(modelData.datetime), "ddd") }
                        Icon { anchors.horizontalCenter: parent.horizontalCenter; size: 24; color: Theme.text
                               name: hero.condIcon[modelData.condition] || "weather-cloudy" }
                        Label { anchors.horizontalCenter: parent.horizontalCenter; size: 13
                                text: Math.round(modelData.temperature) + "° " }
                        Label { anchors.horizontalCenter: parent.horizontalCenter; size: 11; color: Theme.textDim
                                text: modelData.templow !== undefined ? Math.round(modelData.templow) + "°" : "" }
                    }
                }
            }
        }
    }
}
