import QtQuick 2.15
import "."

Text {
    property real size: 14
    font.family: Theme.font
    font.pixelSize: size * Theme.u
    color: Theme.text
    elide: Text.ElideRight
    renderType: Text.QtRendering
}
