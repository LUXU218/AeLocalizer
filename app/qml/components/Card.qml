import QtQuick
import QtQuick.Layouts
import AeLocalizer

Rectangle {
    id: card
    default property alias content: inner.data
    property string title: ""
    property string subtitle: ""

    color: "transparent"
    border.width: 0

    GlassBackdrop { anchors.fill: parent }

    implicitHeight: col.implicitHeight + (title.length > 0 ? Theme.spaceMd : 0)

    ColumnLayout {
        id: col
        anchors.fill: parent
        spacing: Theme.spaceMd

        ColumnLayout {
            visible: card.title.length > 0
            spacing: 3
            Text {
                text: card.title
                color: Theme.textOnBg
                font.family: Theme.fontFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
            }
            Text {
                text: card.subtitle
                visible: card.subtitle.length > 0
                color: Theme.textOnBgTertiary
                font.family: Theme.fontFamily
                font.pixelSize: 11
                lineHeight: 1.4
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        ColumnLayout {
            id: inner
            Layout.fillWidth: true
            spacing: Theme.spaceSm
        }
    }
}
