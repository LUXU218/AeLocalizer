import QtQuick
import QtQuick.Layouts
import AeLocalizer

ColumnLayout {
    id: block
    default property alias content: inner.data
    property string title: ""
    property string hint: ""

    Layout.fillWidth: true
    spacing: 4

    Text {
        text: block.title
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
    }
    Text {
        text: block.hint
        visible: block.hint.length > 0
        Layout.fillWidth: true
        color: Theme.textTertiary
        font.family: Theme.fontFamily
        font.pixelSize: 11
        lineHeight: 1.3
        wrapMode: Text.WordWrap
    }

    ColumnLayout {
        id: inner
        Layout.fillWidth: true
        Layout.topMargin: 2
        spacing: Theme.spaceSm
    }
}
