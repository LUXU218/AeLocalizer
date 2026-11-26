import QtQuick
import AeLocalizer

Rectangle {
    id: badge
    property string text: ""
    property color tone: Theme.textSecondary
    property bool filled: false
    property bool pulse: false

    implicitWidth: row.implicitWidth + 12
    implicitHeight: Math.max(20, row.implicitHeight + 4)
    radius: 5
    color: Qt.rgba(tone.r, tone.g, tone.b, filled ? 0.16 : 0.08)
    border.color: Qt.rgba(tone.r, tone.g, tone.b, 0.22)

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6

        Rectangle {
            width: 6; height: 6; radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: badge.tone

            SequentialAnimation on scale {
                running: badge.pulse && !Theme.reduceMotion
                loops: Animation.Infinite
                NumberAnimation { to: 1.35; duration: 900; easing.type: Easing.InOutQuad }
                NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutQuad }
            }
        }

        Text {
            text: badge.text
            color: badge.tone
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
        }
    }
}
