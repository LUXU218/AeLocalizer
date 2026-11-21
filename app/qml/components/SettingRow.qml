import QtQuick
import QtQuick.Layouts
import AeLocalizer

RowLayout {
    id: row
    default property alias content: ctrl.data
    property string title: ""
    property string hint: ""

    readonly property int labelWidth: Theme.roomy ? 164 : 148

    spacing: Theme.spaceMd
    Layout.fillWidth: true
    Layout.minimumHeight: 36

    ColumnLayout {
        Layout.preferredWidth: row.labelWidth
        Layout.minimumWidth: row.labelWidth
        Layout.maximumWidth: row.labelWidth
        Layout.alignment: Qt.AlignVCenter
        spacing: 2

        Text {
            text: row.title
            Layout.fillWidth: true
            color: Theme.textOnBg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            wrapMode: Text.WordWrap
        }
        Text {
            text: row.hint
            visible: row.hint.length > 0
            Layout.fillWidth: true
            color: Theme.textOnBgSecondary
            font.family: Theme.fontFamily
            font.pixelSize: 11
            lineHeight: 1.3
            wrapMode: Text.WordWrap
        }
    }

    RowLayout {
        id: ctrl
        Layout.fillWidth: true
        spacing: Theme.spaceSm
    }
}
