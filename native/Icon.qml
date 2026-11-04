import QtQuick 2.15
import "."

Text {
    property string name: ""
    property real size: 24
    font.family: Theme.iconFont.name
    font.pixelSize: size * Theme.u
    text: Icons.glyph(name)
    color: Theme.textDim
    renderType: Text.QtRendering
}
