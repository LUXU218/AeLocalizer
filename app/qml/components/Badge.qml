import QtQuick
import AeLocalizer

Rectangle {
    id: badge
    property string text: ""
    property color tone: Theme.textSecondary
    property bool filled: false
    property bool pulse: false

    implicitWidth: row.implicitWidth
    implicitHeight: 18
    color: "transparent"
    border.width: 0

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6

        Rectangle {
            width: 6; height: 6; radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: badge.tone

            SequentialAnimation on scale {
                running: badge.pulse
                loops: Animation.Infinite
                NumberAnimation { to: 1.35; duration: 900; easing.type: Easing.InOutQuad }
                NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutQuad }
            }
        }

        Text {
            text: badge.text
            color: badge.tone
            font.family: Theme.fontFamily
            font.pixelSize: 11
        }
    }
}
