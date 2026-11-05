// Hold-to-talk intercom button (bottom right, above the tabs). Shown only when this panel runs
// ha-kiosk-intercom with INTERCOM_MIC=1 and another intercom panel exists. Talks to the service on
// 127.0.0.1 (INTERCOM_PORT + 1, default 10711). Tap the small label to change the target room.
import QtQuick 2.15
import "."

Item {
    id: ic
    property string api: "http://127.0.0.1:10711"
    property bool mic: false
    property bool talking: false
    property bool pressedDown: false
    property string target: ""
    property var targets: []
    property string icState: "idle"
    visible: mic && targets.length > 0
    width: 76 * Theme.u; height: 96 * Theme.u

    function call(path, cb) {
        var x = new XMLHttpRequest();
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return;
            if (x.status !== 200) { ic.mic = false; return; }
            try {
                var s = JSON.parse(x.responseText);
                ic.mic = s.mic; ic.talking = s.talking; ic.target = s.target;
                ic.targets = s.targets || []; ic.icState = s.state;
            } catch (e) {}
            if (cb) cb();
        };
        x.open("GET", api + path);
        x.send();
    }

    Timer { interval: ic.pressedDown || ic.talking ? 1000 : (ic.mic ? 5000 : 30000); running: true; repeat: true; triggeredOnStart: true
            onTriggered: ic.call("/status") }

    Rectangle {
        id: btn
        anchors { horizontalCenter: parent.horizontalCenter; top: parent.top }
        width: 64 * Theme.u; height: width; radius: width / 2
        color: ic.talking || ic.pressedDown ? Theme.red : "#e6181b21"
        border.color: ic.icState.indexOf("listening") === 0 ? Theme.blue : Theme.border
        border.width: ic.icState.indexOf("listening") === 0 ? 3 : 1
        Icon { anchors.centerIn: parent; size: 30; name: "microphone"
               color: ic.talking || ic.pressedDown ? "white" : Theme.textDim }
        MouseArea {
            anchors.fill: parent
            onPressed: { ic.pressedDown = true; ic.call("/talk?on=1"); }
            onReleased: { ic.pressedDown = false; ic.call("/talk?on=0"); }
            onCanceled: { ic.pressedDown = false; ic.call("/talk?on=0"); }
        }
    }
    Label {
        anchors { horizontalCenter: parent.horizontalCenter; top: btn.bottom; topMargin: 4 * Theme.u }
        size: 10; color: Theme.textDim; elide: Text.ElideRight; width: parent.width
        horizontalAlignment: Text.AlignHCenter
        text: ic.target
        MouseArea {
            anchors.fill: parent
            onClicked: {   // cycle through the rooms
                var i = ic.targets.indexOf(ic.target);
                var n = ic.targets[(i + 1) % ic.targets.length];
                ic.call("/target?name=" + encodeURIComponent(n));
            }
        }
    }
}
