import QtQuick
import AeLocalizer

Rectangle {
    id: bar
    property real value: 0
    property color fillColor: Theme.accent
    property real trackHeight: 8

    implicitHeight: trackHeight
    radius: trackHeight / 2
    color: Theme.dark ? "#1B222B" : "#E9EEF5"

    Rectangle {
        id: fill
        height: parent.height
        radius: parent.radius
        width: Math.max(0, Math.min(1, bar.value)) * parent.width
        color: bar.fillColor
        Behavior on width { NumberAnimation { duration: Theme.durNormal; easing.type: Easing.OutCubic } }
    }
}
