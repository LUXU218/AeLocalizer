import QtQuick
import QtQuick.Layouts
import QtQuick.Templates as Templates
import AeLocalizer

Rectangle {
    id: seg
    property var options: []
    property string current: ""
    signal picked(string key)

    implicitWidth: choices.implicitWidth + 6
    implicitHeight: choices.implicitHeight + 6
    radius: Theme.radiusControl + 2
    color: Theme.insetFill

    RowLayout {
        id: choices
        anchors.centerIn: parent
        spacing: 2

        Repeater {
            model: seg.options

            delegate: Templates.Button {
                id: chip
                required property var modelData
                readonly property bool selected: seg.current === modelData.key
                text: modelData.label
                Accessible.role: Accessible.RadioButton
                Accessible.checked: selected
                hoverEnabled: true
                horizontalPadding: Theme.spaceSm
                implicitWidth: contentItem.implicitWidth + 2 * horizontalPadding
                implicitHeight: Math.max(26, contentItem.implicitHeight + 4)
                onClicked: if (!selected) seg.picked(modelData.key)
                HoverHandler { cursorShape: Qt.PointingHandCursor }

                contentItem: Text {
                    text: chip.text
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    color: chip.selected || chip.hovered ? Theme.textPrimary : Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                    font.weight: chip.selected ? Font.DemiBold : Font.Normal
                }
                background: Rectangle {
                    radius: Theme.radiusControl - 1
                    color: chip.selected ? Theme.selectedControlFill : chip.hovered ? Theme.bgHover : "transparent"
                    border.width: chip.visualFocus ? 2 : chip.selected ? 1 : 0
                    border.color: chip.visualFocus ? Theme.focusRing : Theme.border
                }
            }
        }
    }
}
