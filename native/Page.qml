// A scrolling page of titled sections, each a flow of tiles built from layout.json.
import QtQuick 2.15
import "."

Flickable {
    id: page
    property var spec: ({})
    property var weatherEntity: ""
    property var insideEntity: ""
    contentWidth: width
    contentHeight: body.height + 24 * Theme.u
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: !Panel.multi            // two-finger gestures must not scroll the page
    flickDeceleration: 4000

    Connections { target: Panel; function onGoHome() { page.contentY = 0; } }

    // Scroll hint: a soft fade at the bottom while there is more below.
    Rectangle {
        parent: page
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 28 * Theme.u
        visible: page.contentHeight > page.height + 4 && !page.atYEnd
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#000e1013" }
            GradientStop { position: 1.0; color: "#e60e1013" }
        }
    }


    readonly property var files: ({ light: "LightTile.qml", "switch": "SwitchTile.qml", scene: "SceneTile.qml",
                                    alloff: "SceneTile.qml", climate: "ClimateTile.qml", media: "MediaTile.qml",
                                    sensor: "SensorTile.qml", binary: "SensorTile.qml",
                                    cover: "CoverTile.qml", lock: "LockTile.qml", fan: "FanTile.qml",
                                    number: "NumberTile.qml", select: "SelectTile.qml",
                                    camera: "CameraTile.qml" })
    readonly property var cols: ({ full: 1, half: 2, third: 3, quarter: 4 })

    Column {
        id: body
        x: Theme.gap; y: Theme.gap
        width: parent.width - 2 * Theme.gap
        spacing: 18 * Theme.u

        Repeater {
            model: page.spec.sections || []
            Column {
                id: sec
                property var s: modelData
                width: body.width
                spacing: 8 * Theme.u
                visible: !(s.hideWhenEmpty && flow.visibleCount === 0)

                Loader {
                    width: parent.width
                    active: sec.s.type === "hero"
                    visible: active
                    sourceComponent: Hero { weatherEntity: page.weatherEntity; insideEntity: page.insideEntity }
                    onLoaded: item.width = Qt.binding(function () { return width; })
                }
                Label { visible: !!sec.s.title; text: (sec.s.title || "").toUpperCase(); size: 15; color: Theme.textDim; font.weight: Font.DemiBold; leftPadding: 4 * Theme.u }
                Flow {
                    id: flow
                    width: parent.width
                    spacing: Theme.gap
                    property int visibleCount: 0
                    function recount() { var n = 0; for (var i = 0; i < children.length; i++) if (children[i].item && children[i].item.shown) n++; visibleCount = n; }
                    Repeater {
                        model: sec.s.items || []
                        Loader {
                            readonly property int n: page.cols[modelData.size || "half"] || 2
                            readonly property bool shown: item ? item.shown : false
                            width: (flow.width - (n - 1) * Theme.gap) / n
                            height: item ? item.height : 0
                            visible: shown
                            onShownChanged: flow.recount()
                            Component.onCompleted: setSource(page.files[modelData.type] || "SensorTile.qml",
                                { entity: modelData.entity || "", name: modelData.name || "", icon: modelData.icon || "", cfg: modelData, width: width })
                            onWidthChanged: if (item) item.width = width
                            onLoaded: flow.recount()
                        }
                    }
                }
            }
        }
    }
}
