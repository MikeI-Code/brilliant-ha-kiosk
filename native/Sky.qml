// BETA animated sky behind the home Hero (idea and palettes adapted from
// alexsto1an/ha-ani-weather-card, MIT). Opaque gradient chosen from the weather
// condition and the sun phase; one 3 s cross-fade when it changes. Particles
// (rain/snow/lightning) live in WeatherFx.qml behind a Loader so a missing
// QtQuick.Particles module only drops the motion, never the sky.
// kiosk.env: WEATHER_FX=off | sky | full (default sky; full is opt-in, ~1 CPU core in bad weather).
import QtQuick 2.15
import "."

Item {
    id: sky
    property string condition: ""
    property bool running: true           // pause motion when off-screen
    property real radius: Theme.radius
    property string phaseOverride: ""     // weather page preview of another hour: day | night | dawn | dusk
    property real fxSize: 1               // particle/sprite scale (bigger on the weather page)
    property bool touchable: true         // finger blows particles; a tap emits tapped()
    signal tapped()
    readonly property string mode: {
        var m = /^\s*WEATHER_FX\s*=\s*"?(off|sky|full)/mi.exec(Ha.readFile("/etc/ha-kiosk/kiosk.env"));
        return m ? m[1].toLowerCase() : "sky";
    }
    visible: mode !== "off"

    // Sun phase from sun.sun: night below -6°, dawn/dusk within ±6°, else day.
    property var sun: Ha.get("sun.sun")
    Connections { target: Ha; function onEntityUpdated(id) { if (id === "sun.sun") sky.sun = Ha.get("sun.sun"); }
                             function onStatesLoaded() { sky.sun = Ha.get("sun.sun"); } }
    readonly property string phase: phaseOverride || livePhase
    readonly property string livePhase: {
        if (!sun || !sun.attributes) return "day";
        var e = sun.attributes.elevation;
        if (e === undefined) return sun.state === "below_horizon" ? "night" : "day";
        if (e < -6) return "night";
        if (e < 6) return sun.attributes.rising ? "dawn" : "dusk";
        return "day";
    }

    // Condition group -> [top, bottom] per phase. Kept dark enough for Theme.text (AA).
    readonly property var palettes: ({
        clear:  { day: ["#164a7d", "#2a6aa3"], dawn: ["#3a3f73", "#b0645a"], dusk: ["#2b2f63", "#a3504a"], night: ["#060b1f", "#14204a"] },
        cloud:  { day: ["#344859", "#4f6577"], dawn: ["#3c3f58", "#7d6366"], dusk: ["#33364f", "#6e5257"], night: ["#0d1119", "#232b38"] },
        rain:   { day: ["#2a3a4a", "#465a6c"], dawn: ["#2b2f43", "#5a4f58"], dusk: ["#262a3d", "#4f4450"], night: ["#0a0e14", "#1b2430"] },
        storm:  { day: ["#1e2432", "#3a4152"], dawn: ["#1f2030", "#46394a"], dusk: ["#1b1c2b", "#3f3242"], night: ["#07080d", "#161a26"] },
        snow:   { day: ["#3e5266", "#5c7387"], dawn: ["#4a4d66", "#8a7a86"], dusk: ["#40435c", "#7a6a78"], night: ["#141a26", "#2c3646"] },
        fog:    { day: ["#47515c", "#5f6a75"], dawn: ["#4e4e5c", "#867a7c"], dusk: ["#46465a", "#786a70"], night: ["#15181d", "#2b3038"] }
    })
    function group(c) {
        if (c === "sunny" || c === "clear-night") return "clear";
        if (c === "rainy" || c === "pouring") return "rain";
        if (c === "lightning" || c === "lightning-rainy" || c === "exceptional") return "storm";
        if (c === "snowy" || c === "snowy-rainy" || c === "hail") return "snow";
        if (c === "fog") return "fog";
        return "cloud";                   // cloudy, partlycloudy, windy, unknown
    }
    readonly property var colors: (palettes[group(condition)] || palettes.cloud)[phase === "day" && condition === "clear-night" ? "night" : phase]

    // 'base' shows the current sky; 'fade' brings the next one in over 3 s, then hands it over.
    onColorsChanged: { fade.topColor = colors[0]; fade.bottomColor = colors[1]; fadeIn.restart(); }
    Component.onCompleted: { base.topColor = colors[0]; base.bottomColor = colors[1]; }

    component SkyLayer: Rectangle {
        property color topColor: "#0e1013"
        property color bottomColor: "#0e1013"
        anchors.fill: parent
        radius: sky.radius
        gradient: Gradient { GradientStop { position: 0; color: topColor } GradientStop { position: 1; color: bottomColor } }
    }
    SkyLayer { id: base }
    SkyLayer { id: fade; opacity: 0; visible: opacity > 0 }
    SequentialAnimation {
        id: fadeIn
        NumberAnimation { target: fade; property: "opacity"; from: 0; to: 1; duration: 3000 }
        ScriptAction { script: { base.topColor = fade.topColor; base.bottomColor = fade.bottomColor; fade.opacity = 0; } }
    }

    Connections { target: Panel; function onWoke() { if (fxLoader.item && sky.running) fxLoader.item.burst(); } }
    MouseArea {
        anchors.fill: parent
        z: 3
        enabled: sky.touchable && sky.visible
        onPressed: if (fxLoader.item) fxLoader.item.pushAt(mouse.x - fxLoader.x, mouse.y - fxLoader.y)
        onPositionChanged: if (fxLoader.item) fxLoader.item.pushAt(mouse.x - fxLoader.x, mouse.y - fxLoader.y)
        onReleased: if (fxLoader.item) fxLoader.item.release()
        onCanceled: if (fxLoader.item) fxLoader.item.release()
        onClicked: sky.tapped()
    }
    Loader {
        id: fxLoader
        anchors { fill: parent; leftMargin: sky.radius / 2; rightMargin: sky.radius / 2 }
        z: 2
        active: sky.mode === "full"
        source: "WeatherFx.qml"
        onLoaded: {
            item.condition = Qt.binding(function () { return sky.condition; });
            item.running = Qt.binding(function () { return sky.running; });
            item.night = Qt.binding(function () { return sky.phase === "night"; });
            item.size = Qt.binding(function () { return sky.fxSize; });
        }
    }
}
