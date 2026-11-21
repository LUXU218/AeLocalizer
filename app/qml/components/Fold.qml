import QtQuick
import AeLocalizer

Rectangle {
    id: fold
    default property alias content: contentCol.data
    property string title: ""
    property string hint: ""
    property bool expanded: false

    color: "transparent"
    border.width: 0
    clip: true

    implicitHeight: head.height + (expanded ? body.height : 0)

    Column {
        anchors.fill: parent
        spacing: 0

        Item {
            id: head
            width: parent.width
            height: 26

            Row {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                spacing: 10

                Text {
                    text: fold.title
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                }
                Text {
                    text: fold.hint
                    visible: fold.hint.length > 0
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.textTertiary
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                }
            }

            Text {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: fold.expanded ? "收起 ▴" : "展开 ▾"
                color: area.containsMouse ? Theme.textSecondary : Theme.textTertiary
                font.family: Theme.fontFamily
                font.pixelSize: 11

                Behavior on color { ColorAnimation { duration: Theme.durFast } }
            }

            MouseArea {
                id: area
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: fold.expanded = !fold.expanded
            }
        }

        Item {
            id: body
            width: parent.width
            height: fold.expanded ? contentCol.height : 0
            clip: true
            opacity: fold.expanded ? 1 : 0

            Behavior on height { NumberAnimation { duration: Theme.durSlow; easing.type: Easing.InOutQuad } }
            Behavior on opacity { NumberAnimation { duration: Theme.durNormal } }

            Column {
                id: contentCol
                anchors.left: parent.left
                anchors.right: parent.right
                topPadding: Theme.spaceMd
                spacing: Theme.spaceSm
            }
        }
    }
}
