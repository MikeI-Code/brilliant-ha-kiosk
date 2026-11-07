// BETA weather motion for Sky.qml: clouds, fog, sun glow, moon, stars, rain, snow, wind-blown
// leaves and lightning. Small counts and pre-rendered sprites (native/sky/*.png) to respect the
// i.MX6 fill-rate budget; everything stops when 'running' is false.
// Touch: pushAt(x, y) / release() blows particles away from a finger; burst() is the wake flourish.
import QtQuick 2.15
import QtQuick.Particles 2.15
import "."

Item {
    id: fx
    property string condition: ""
    property bool running: true
    property bool night: false
    property real size: 1                  // 1 in the Hero, larger on the weather page
    clip: true

    readonly property bool rain: ["rainy", "pouring", "lightning-rainy", "snowy-rainy"].indexOf(condition) >= 0
    readonly property bool snow: ["snowy", "snowy-rainy", "hail"].indexOf(condition) >= 0
    readonly property bool bolt: condition === "lightning" || condition === "lightning-rainy"
    readonly property bool clear: condition === "sunny" || condition === "clear-night"
    readonly property bool stars: night && (clear || condition === "partlycloudy")
    readonly property bool windy: condition === "windy" || condition === "windy-variant"
    readonly property bool foggy: condition === "fog"
    readonly property int cloudCount: clear ? 0 : condition === "partlycloudy" ? 2
                                      : (foggy ? 1 : 3)

    // Touch push
    property bool pushing: false
    property real pushX: 0
    property real pushY: 0
    function pushAt(x, y) { pushX = x; pushY = y; pushing = true; }
    function release() { pushing = false; }
    // Wake flourish: a quick extra flurry of whatever is falling, and the sun/moon glow swells.
    function burst() {
        if (rainEm.enabled) rainEm.burst(30);
        if (snowEm.enabled) snowEm.burst(20);
        if (leafEm.enabled) leafEm.burst(6);
        if (starEm.enabled) starEm.burst(8);
        swell.restart();
    }

    // Sun (day, clear/partly) or moon (night, clear/partly), top right behind the reading.
    Image {
        id: orb
        source: fx.night ? "sky/moon.png" : "sky/sun.png"
        visible: fx.clear || fx.condition === "partlycloudy"
        width: (fx.night ? 72 : 150) * Theme.u * fx.size; height: width
        sourceSize.width: fx.night ? 96 : 256
        x: parent.width - width * (fx.night ? 1.3 : 0.75); y: -height * (fx.night ? -0.15 : 0.3)
        smooth: true
        SequentialAnimation on scale {
            running: fx.running && orb.visible && !fx.night; loops: Animation.Infinite
            NumberAnimation { to: 1.06; duration: 4000; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 4000; easing.type: Easing.InOutSine }
        }
        SequentialAnimation {
            id: swell
            NumberAnimation { target: orb; property: "opacity"; to: 1; duration: 1 }
            NumberAnimation { target: orb; property: "scale"; to: 1.18; duration: 500; easing.type: Easing.OutQuad }
            NumberAnimation { target: orb; property: "scale"; to: 1.0; duration: 1400; easing.type: Easing.InOutSine }
        }
    }

    // Drifting clouds: each crosses slowly, wraps, and starts at a different place.
    Repeater {
        model: fx.cloudCount
        Image {
            id: cl
            source: index % 2 ? "sky/cloud2.png" : "sky/cloud1.png"
            sourceSize.width: 256
            width: (150 + 40 * index) * Theme.u * fx.size; height: width / 2
            y: (index * 0.22 - 0.12) * fx.height
            opacity: fx.night ? 0.45 : (fx.condition === "partlycloudy" ? 0.8 : 1)
            readonly property real span: fx.width + width
            readonly property real offset: (index * 0.37 + 0.1) % 1
            property real t: 0
            x: -width + ((t + offset) % 1) * span
            NumberAnimation on t {
                running: fx.running; loops: Animation.Infinite
                from: 0; to: 1; duration: (fx.windy ? 30000 : 70000) + index * 17000
            }
        }
    }

    // Fog: two wide bands sliding past each other.
    Repeater {
        model: fx.foggy ? 2 : 0
        Image {
            source: "sky/fog.png"
            width: fx.width * 2; height: fx.height * (index ? 0.5 : 0.65)
            y: fx.height * (index ? 0.45 : 0.15)
            fillMode: Image.Tile
            NumberAnimation on x {
                running: fx.running; loops: Animation.Infinite
                from: index ? -fx.width : 0; to: index ? 0 : -fx.width
                duration: 60000 + index * 25000
            }
        }
    }

    ParticleSystem {
        id: ps
        anchors.fill: parent
        running: fx.running
        paused: !fx.running

        ItemParticle {
            groups: ["rain"]
            delegate: Rectangle { width: Math.max(1, Theme.u * 0.75); height: 9 * Theme.u * fx.size; color: "#7fc4dcf0" }
        }
        ItemParticle {
            groups: ["snow"]
            delegate: Rectangle { width: 4 * Theme.u * fx.size; height: width; radius: width / 2; color: "#d8eef4fa" }
        }
        ItemParticle {
            groups: ["star"]
            fade: true
            delegate: Rectangle { width: 2 * Theme.u; height: width; color: "#b0ffffff" }
        }
        ItemParticle {
            groups: ["leaf"]
            delegate: Rectangle { width: 7 * Theme.u; height: 4 * Theme.u; radius: height / 2
                                  color: ["#c98a3b", "#a5652a", "#d9a441"][Math.floor(Math.random() * 3)] }
        }

        Emitter {
            id: rainEm
            group: "rain"; enabled: fx.rain
            width: parent.width; height: 1; y: -10 * Theme.u
            emitRate: (fx.condition === "pouring" ? 45 : 25) * fx.size
            maximumEmitted: 40 * fx.size
            lifeSpan: 900 * fx.size
            velocity: PointDirection { x: -40; y: 420; yVariation: 60 }
        }
        Emitter {
            id: snowEm
            group: "snow"; enabled: fx.snow
            width: parent.width; height: 1; y: -6 * Theme.u
            emitRate: 8 * fx.size; maximumEmitted: 30 * fx.size
            lifeSpan: 4500 * fx.size
            velocity: PointDirection { x: -10; xVariation: 20; y: 45; yVariation: 15 }
        }
        Emitter {
            id: starEm
            group: "star"; enabled: fx.stars
            anchors.fill: parent
            emitRate: 2; maximumEmitted: 14
            lifeSpan: 6000; lifeSpanVariation: 2000
        }
        Emitter {
            id: leafEm
            group: "leaf"; enabled: fx.windy
            x: -10 * Theme.u; width: 1; height: parent.height
            emitRate: 1.2; maximumEmitted: 8
            lifeSpan: 5000
            velocity: PointDirection { x: 160; xVariation: 60; y: 10; yVariation: 30 }
        }
        Wander { groups: ["leaf", "snow"]; xVariance: 40; pace: 60; affectedParameter: Wander.Velocity }
        // Rotate leaves as they tumble.
        Affector {
            groups: ["leaf"]
            onAffectParticles: function (particles, dt) {
                for (var i = 0; i < particles.length; i++) { particles[i].rotation += 4 * dt; particles[i].update = true; }
            }
        }
        // Finger push: particles near the touch are blown outward.
        Attractor {
            anchors.fill: parent
            enabled: fx.pushing
            pointX: fx.pushX; pointY: fx.pushY
            strength: -90000
            affectedParameter: Attractor.Velocity
            proportionalToDistance: Attractor.InverseLinear
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
