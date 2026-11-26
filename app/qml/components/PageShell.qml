import QtQuick
import QtQuick.Layouts
import AeLocalizer

Rectangle {
    id: shell
    default property alias content: contentData.data
    property string title: ""
    property string description: ""
    property Component actions

    readonly property int gutter: Math.round(Theme.spaceMd + (Theme.spaceXl - Theme.spaceMd)
                                             * Math.max(0, Math.min(1, (width - 650) / 300)))
    color: Theme.hasAnyBg ? "transparent" : Theme.workspaceFill
    clip: true

    RowLayout {
        id: head
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: shell.gutter
        anchors.rightMargin: shell.gutter
        height: shell.height < 525 ? 54 : Math.max(76, Theme.fontMetric + Theme.fontSmall + 40)
        spacing: Theme.spaceMd

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3
            Text {
                Layout.fillWidth: true
                text: shell.title
                color: Theme.textOnBg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontMetric
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            Text {
                Layout.fillWidth: true
                visible: shell.height >= 525 && text !== ""
                text: shell.description
                color: Theme.textOnBgSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                elide: Text.ElideRight
            }
        }
        Loader {
            active: !!shell.actions
            sourceComponent: shell.actions
        }
    }
    Item {
        id: contentData
        anchors.top: head.bottom
        anchors.left: head.left
        anchors.right: head.right
        anchors.bottom: parent.bottom
        anchors.bottomMargin: shell.gutter
    }
}
