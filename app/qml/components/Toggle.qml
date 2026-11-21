import QtQuick
import AeLocalizer

Rectangle {
    id: tog
    property bool checked: true
    property string label: ""
    property color activeColor: Theme.accent
    signal toggled(bool nextState)

    implicitWidth: row.implicitWidth + Theme.spaceSm * 2
    implicitHeight: 22
    radius: 11
    color: checked ? Qt.rgba(activeColor.r, activeColor.g, activeColor.b, 0.18)
                   : (area.containsMouse ? Theme.bgHover : "transparent")
    border.width: 1
    border.color: checked ? activeColor : Theme.border

    Behavior on color { ColorAnimation { duration: Theme.durFast } }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 4
        Rectangle {
            width: 6; height: 6; radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: tog.checked ? tog.activeColor : Theme.textTertiary
            Behavior on color { ColorAnimation { duration: Theme.durFast } }
        }
        Text {
            text: tog.label
            anchors.verticalCenter: parent.verticalCenter
            color: tog.checked ? Theme.textPrimary : Theme.textTertiary
            font.family: Theme.fontFamily
            font.pixelSize: 11
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        // 由调用方更新状态，保留 checked 的绑定。
        onClicked: tog.toggled(!tog.checked)
    }
}
