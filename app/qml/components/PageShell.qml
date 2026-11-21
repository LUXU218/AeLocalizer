import QtQuick
import AeLocalizer

Rectangle {
    id: shell
    default property alias content: contentData.data
    property string title: ""

    readonly property int gutter: Math.round(Theme.spaceMd
                              + (Theme.spaceLg - Theme.spaceMd) * Math.max(0, Math.min(1, (width - 700) / 200)))

    color: Theme.hasAnyBg ? "transparent" : Theme.workspaceFill
    clip: true
    opacity: visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutQuad } }

    Item {
        id: head
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: shell.gutter
        anchors.rightMargin: shell.gutter
        height: Math.round(Math.max(52, Math.min(64, 52 + (shell.height - 460) / 15)))

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: shell.title
            color: Theme.textOnBg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontMetric
            font.weight: Font.DemiBold
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
