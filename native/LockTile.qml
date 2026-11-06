// Lock tile (domain lock). Tap locks in one tap; unlocking needs a second tap within 3 s
// (the SceneTile "armed" pattern). Locks with attributes.code_format need a code: no keypad
// in this milestone, so unlock is refused with a hint. Locking still works without a code.
// supported_features: bit 1 (OPEN) is not used - this tile only calls lock.lock / lock.unlock.
import QtQuick 2.15
import "."

Tile {
    id: t
    property bool armed: false
    readonly property bool locked: stateText === "locked"
    readonly property bool jammed: stateText === "jammed"
    readonly property bool busy: stateText === "locking" || stateText === "unlocking" || stateText === "opening"
    readonly property bool unlockedish: stateText === "unlocked" || stateText === "open" || stateText === "opening" || stateText === "unlocking"
    readonly property bool needsCode: !!(attrs && attrs.code_format)
    property bool codeHint: false
    readonly property color accent: jammed ? Theme.red : (locked || stateText === "locking" ? Theme.green : Theme.amber)

    height: 70 * Theme.u
    color: armed ? "#3a1f1f" : (pressed ? Theme.cardPressed : (unlockedish && !unavailable ? Theme.onTint : Theme.card))

    onTapped: {
        if (unavailable) return;
        if (locked || stateText === "locking") {
            // Unlock path: code locks are refused, others need the arm-confirm tap.
            if (needsCode) { codeHint = true; hintTimer.restart(); return; }
            if (!armed) { armed = true; disarm.restart(); return; }
            armed = false;
            sent(Ha.callService("lock", "unlock", null, entity));
        } else {
            // unlocked / open / jammed / unlocking / opening: lock in one tap.
            armed = false;
            sent(Ha.callService("lock", "lock", null, entity));
        }
    }
    onStateTextChanged: { armed = false; codeHint = false; }
    Timer { id: disarm; interval: 3000; onTriggered: t.armed = false }
    Timer { id: hintTimer; interval: 3000; onTriggered: t.codeHint = false }

    function statusText() {
        if (unavailable) return "Unavailable";
        if (armed) return "Tap again to unlock";
        if (codeHint) return "Code needed — use the HA app";
        switch (stateText) {
        case "locked": return "Locked";
        case "unlocked": return "Unlocked";
        case "locking": return "Locking…";
        case "unlocking": return "Unlocking…";
        case "jammed": return "Jammed";
        case "open": return "Open";
        case "opening": return "Opening…";
        }
        return stateText;
    }

    Rectangle {
        id: badge
        x: Theme.pad; anchors.verticalCenter: parent.verticalCenter
        width: 38 * Theme.u; height: width; radius: width / 2
        color: t.unlockedish && !t.jammed ? Theme.amberSoft : Theme.track
        Icon {
            anchors.centerIn: parent; size: 22
            name: t.jammed ? "lock-alert" : (t.locked || t.stateText === "locking" ? (t.icon || "lock") : "lock-open-variant")
            color: t.unavailable ? Theme.textDim : t.accent
        }
    }
    Column {
        anchors { left: badge.right; leftMargin: 10 * Theme.u; right: parent.right; rightMargin: Theme.pad; verticalCenter: parent.verticalCenter }
        Label { width: parent.width; text: t.name; size: 15; font.weight: Font.DemiBold; elide: Text.ElideRight }
        Label {
            width: parent.width; size: 14; elide: Text.ElideRight
            color: t.unavailable ? Theme.textDim : (t.armed ? Theme.red : (t.codeHint ? Theme.textDim : t.accent))
            text: t.statusText()
        }
    }
}
