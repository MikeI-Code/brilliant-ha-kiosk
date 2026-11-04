// Scene/script button, or an action button (cfg.action). type "alloff" turns off every light (tap twice).
import QtQuick 2.15
import "."

Tile {
    id: t
    property bool flash: false
    property bool armed: false
    readonly property bool allOff: cfg.type === "alloff"
    height: 86 * Theme.u
    opacity: 1
    color: flash ? Theme.amberSoft : (armed ? "#3a1f1f" : (pressed ? Theme.cardPressed : Theme.card))

    onTapped: {
        if (allOff) {
            if (!armed) { armed = true; disarm.restart(); return; }
            armed = false;
            Ha.callService("light", "turn_off", null, "all");
        } else if (cfg.action) {          // button card with a perform-action (from an HA dashboard)
            Ha.callService(cfg.action.domain, cfg.action.service, cfg.action.data, cfg.action.target);
        } else {
            Ha.callService(entity.split(".")[0] === "script" ? "script" : "scene", "turn_on", null, entity);
        }
        flash = true; unflash.restart();
    }
    Timer { id: unflash; interval: 600; onTriggered: t.flash = false }
    Timer { id: disarm; interval: 3000; onTriggered: t.armed = false }

    Column {
        anchors.centerIn: parent
        spacing: 6 * Theme.u
        Icon { anchors.horizontalCenter: parent.horizontalCenter; size: 26; name: t.icon || "palette"
               color: t.armed ? Theme.red : (t.flash ? Theme.amber : Theme.blue) }
        Label { anchors.horizontalCenter: parent.horizontalCenter; size: 13; font.weight: Font.DemiBold
                text: t.armed ? "Tap again" : t.name }
    }
}
