import QtQuick
import QtQuick.Templates as Templates
import AeLocalizer

Item {
    id: control
    property bool checked: true
    property string label: ""
    signal toggled(bool nextState)

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    Templates.Button {
        id: button
        anchors.fill: parent
        text: control.label
        implicitWidth: contentItem.implicitWidth + 8
        implicitHeight: Math.max(30, contentItem.implicitHeight + 4)
        hoverEnabled: true
        Accessible.role: Accessible.CheckBox
        Accessible.checked: control.checked
        onClicked: control.toggled(!control.checked)
        HoverHandler { cursorShape: Qt.PointingHandCursor }

        background: Rectangle {
            radius: Theme.radiusControl
            color: button.hovered ? Theme.bgHover : "transparent"
            border.width: button.visualFocus ? 2 : 0
            border.color: Theme.focusRing
        }
        contentItem: Row {
            spacing: Theme.spaceSm
            SwitchTrack {
                anchors.verticalCenter: parent.verticalCenter
                checked: control.checked
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: control.label
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
            }
        }
    }
}
