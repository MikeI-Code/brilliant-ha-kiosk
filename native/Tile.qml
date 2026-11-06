// Base tile: card background, press feedback, and live state for one entity.
import QtQuick 2.15
import "."

Rectangle {
    id: tile
    property string entity: ""
    property string name: ""
    property string icon: ""
    property var cfg: ({})
    property var st: null
    property bool shown: true            // layout visibility (e.g. media tiles only while playing)
    property bool pressed: tapArea.pressed
    property alias tapArea: tapArea
    signal tapped()

    // Pending feedback: set by a tile when it sends a command; cleared when HA reports the new
    // state, when the call fails, or after 4 s. The border pulses meanwhile.
    property bool pending: false
    property int _callId: -1
    function sent(id) { _callId = id; pending = id >= 0; if (pending) pendingTimer.restart(); }
    Timer { id: pendingTimer; interval: 4000; onTriggered: tile.pending = false }

    readonly property var attrs: st ? st.attributes : ({})
    readonly property string stateText: st ? st.state : "unavailable"
    property bool unavailable: !st || st.state === "unavailable" || st.state === "unknown"

    radius: Theme.radius
    color: pressed ? Theme.cardPressed : Theme.card
    border.color: pending ? Theme.amber : Theme.border
    border.width: pending ? Math.max(2, Theme.u * 1.5) : Math.max(1, Theme.u * 0.5)
    opacity: unavailable && entity !== "" ? 0.8 : 1   // the status line says "Unavailable"

    function refresh() {
        st = entity ? Ha.get(entity) : null;
        if (!name && st && st.attributes.friendly_name) name = st.attributes.friendly_name;   // no name in the layout
    }
    Component.onCompleted: refresh()
    Connections {
        target: Ha
        function onEntityUpdated(id) { if (id === tile.entity) { tile.pending = false; tile.refresh(); } }
        function onCallFailed(id, msg) { if (id === tile._callId) tile.pending = false; }
        function onStatesLoaded() { tile.refresh(); }
    }

    MouseArea {
        id: tapArea
        anchors.fill: parent
        onClicked: if (!Panel.multi) tile.tapped()   // ignore the taps inside a multi-finger gesture
    }
}
