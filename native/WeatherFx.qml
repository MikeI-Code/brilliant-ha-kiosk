// BETA weather motion for Sky.qml: rain streaks, snow, lightning flashes.
// Small particle counts and solid (non-gradient) particles to respect the i.MX6
// fill-rate budget; everything stops when 'running' is false.
import QtQuick 2.15
import QtQuick.Particles 2.15
import "."

Item {
    id: fx
    property string condition: ""
    property bool running: true
    property bool night: false
    clip: true

    readonly property bool rain: ["rainy", "pouring", "lightning-rainy", "snowy-rainy"].indexOf(condition) >= 0
    readonly property bool snow: ["snowy", "snowy-rainy", "hail"].indexOf(condition) >= 0
    readonly property bool bolt: condition === "lightning" || condition === "lightning-rainy"
    readonly property bool stars: night && (condition === "clear-night" || condition === "sunny")

    ParticleSystem {
        id: ps
        anchors.fill: parent
        running: fx.running && (fx.rain || fx.snow || fx.stars)
        paused: !fx.running

        ItemParticle {
            groups: ["rain"]
            delegate: Rectangle { width: Math.max(1, Theme.u * 0.75); height: 9 * Theme.u; color: "#7fc4dcf0" }
        }
        ItemParticle {
            groups: ["snow"]
            delegate: Rectangle { width: 4 * Theme.u; height: width; radius: width / 2; color: "#d8eef4fa" }
        }
        ItemParticle {
            groups: ["star"]
            fade: true
            delegate: Rectangle { width: 2 * Theme.u; height: width; color: "#b0ffffff" }
        }

        Emitter {
            group: "rain"; enabled: fx.rain
            width: parent.width; height: 1; y: -10 * Theme.u
            emitRate: fx.condition === "pouring" ? 45 : 25
            maximumEmitted: 40
            lifeSpan: 900
            velocity: PointDirection { x: -40; y: 420; yVariation: 60 }
        }
        Emitter {
            group: "snow"; enabled: fx.snow
            width: parent.width; height: 1; y: -6 * Theme.u
            emitRate: 8; maximumEmitted: 30
            lifeSpan: 4500
            size: 4; sizeVariation: 2
            velocity: PointDirection { x: -10; xVariation: 20; y: 45; yVariation: 15 }
        }
        Emitter {
            group: "star"; enabled: fx.stars
            anchors.fill: parent
            emitRate: 2; maximumEmitted: 14
            lifeSpan: 6000; lifeSpanVariation: 2000
        }
    }

    // Lightning: a brief white flash every 6–14 s.
    Rectangle {
        id: flash
        anchors.fill: parent
        color: "#ffffff"; opacity: 0
        visible: opacity > 0
        SequentialAnimation {
            id: strike
            NumberAnimation { target: flash; property: "opacity"; to: 0.35; duration: 60 }
            NumberAnimation { target: flash; property: "opacity"; to: 0.05; duration: 90 }
            NumberAnimation { target: flash; property: "opacity"; to: 0.25; duration: 50 }
            NumberAnimation { target: flash; property: "opacity"; to: 0; duration: 400 }
        }
        Timer {
            running: fx.bolt && fx.running; repeat: true
            interval: 6000 + Math.random() * 8000
            onTriggered: { interval = 6000 + Math.random() * 8000; strike.restart(); }
        }
    }
}
