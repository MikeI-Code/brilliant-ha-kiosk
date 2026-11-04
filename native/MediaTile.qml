// Speaker: artwork, title/artist, play/pause/skip, volume bar.
import QtQuick 2.15
import "."

Tile {
    id: t
    readonly property bool playing: stateText === "playing"
    readonly property bool onlyPlaying: cfg.onlyPlaying === true
    readonly property string art: attrs.entity_picture ? Ha.baseUrl + attrs.entity_picture : ""
    shown: !onlyPlaying || playing
    height: 128 * Theme.u

    Rectangle {
        id: artBox
        x: Theme.pad; y: Theme.pad
        width: 56 * Theme.u; height: width; radius: 10 * Theme.u
        color: Theme.track; clip: true
        Image {
            anchors.fill: parent
            source: t.art; asynchronous: true; cache: true
            sourceSize.width: parent.width; sourceSize.height: parent.height
            fillMode: Image.PreserveAspectCrop
            visible: status === Image.Ready
        }
        Icon { anchors.centerIn: parent; size: 26; name: "music"; visible: !t.art; color: t.playing ? Theme.blue : Theme.textDim }
    }
    Column {
        anchors { left: artBox.right; leftMargin: 10 * Theme.u; right: controls.left; rightMargin: 6 * Theme.u; verticalCenter: artBox.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold }
        Label { width: parent.width; size: 12; color: t.playing ? Theme.text : Theme.textDim
                text: t.unavailable ? "Unavailable" : (t.attrs.media_title || (t.stateText.charAt(0).toUpperCase() + t.stateText.slice(1))) }
        Label { width: parent.width; size: 12; color: Theme.textDim; text: t.attrs.media_artist || ""; visible: text !== "" }
    }
    Row {
        id: controls
        anchors { right: parent.right; rightMargin: Theme.pad / 2; verticalCenter: artBox.verticalCenter }
        Repeater {
            model: [ { i: "skip-previous", s: "media_previous_track" },
                     { i: t.playing ? "pause" : "play", s: "media_play_pause" },
                     { i: "skip-next", s: "media_next_track" } ]
            Item {
                width: 40 * Theme.u; height: 44 * Theme.u
                Icon { anchors.centerIn: parent; size: modelData.s === "media_play_pause" ? 32 : 24; name: modelData.i
                       color: mb.pressed ? Theme.blue : Theme.text }
                MouseArea { id: mb; anchors.fill: parent; enabled: !t.unavailable
                            onClicked: Ha.callService("media_player", modelData.s, null, t.entity) }
            }
        }
    }
    Bar {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Theme.pad }
        fill: Theme.blue
        value: t.attrs.volume_level || 0
        visible: !t.unavailable
        onCommitted: function (v) { Ha.callService("media_player", "volume_set", { volume_level: Math.round(v * 100) / 100 }, t.entity); }
    }
    tapArea.enabled: false
}
