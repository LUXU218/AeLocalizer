import QtQuick
import AeLocalizer

Rectangle {
    id: track
    required property bool checked
    implicitWidth: 34
    implicitHeight: 20
    radius: height / 2
    color: checked ? Theme.actionFill : Theme.insetFill
    border.width: 1
    border.color: checked ? Theme.actionFill : Theme.controlStroke

    Rectangle {
        width: 14
        height: width
        radius: width / 2
        x: track.checked ? track.width - width - 3 : 3
        anchors.verticalCenter: parent.verticalCenter
        color: track.checked ? Theme.actionText : Theme.bgSurface
        border.color: track.checked ? "transparent" : Theme.controlStroke
        Behavior on x { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutQuad } }
    }
}
