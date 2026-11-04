pragma Singleton
import QtQuick 2.15

QtObject {
    // 1 "u" = one CSS-ish pixel on a 360-wide layout; set from the screen width at startup.
    property real u: 2

    readonly property color bg: "#0e1013"
    readonly property color card: "#181b21"
    readonly property color cardPressed: "#22262e"
    readonly property color border: "#232730"
    readonly property color track: "#2a2f38"
    readonly property color text: "#eef0f3"
    readonly property color textDim: "#8b919c"
    readonly property color textFaint: "#555b66"
    readonly property color amber: "#f5b54a"
    readonly property color amberSoft: "#3a2f1b"
    readonly property color blue: "#5ec2e8"
    readonly property color blueSoft: "#1b3038"
    readonly property color orange: "#ff8a4c"
    readonly property color green: "#7bd88f"
    readonly property color red: "#ff6b6b"

    readonly property real radius: 18 * u
    readonly property real gap: 10 * u
    readonly property real pad: 14 * u

    property FontLoader iconFont: FontLoader { source: "fonts/materialdesignicons-webfont.ttf" }
    readonly property string font: "Open Sans"
}
