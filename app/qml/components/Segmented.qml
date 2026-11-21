import QtQuick
import QtQuick.Layouts
import AeLocalizer

RowLayout {
    id: seg
    property var options: []
    property string current: ""
    property color activeColor: Theme.accent
    signal picked(string key)

    spacing: 2

    Repeater {
        model: seg.options

        delegate: Rectangle {
            id: chip
            required property var modelData
            readonly property bool active: seg.current === modelData.key

            implicitWidth: label.implicitWidth + Theme.spaceMd * 2
            implicitHeight: 28
            radius: Theme.radiusControl
            color: active ? Qt.rgba(seg.activeColor.r, seg.activeColor.g, seg.activeColor.b,
                                    Theme.dark ? 0.22 : 0.12)
                          : (area.containsMouse ? Theme.bgHover : "transparent")
            border.width: active ? 1 : 0
            border.color: seg.activeColor

            Behavior on color { ColorAnimation { duration: Theme.durFast } }

            Text {
                id: label
                anchors.centerIn: parent
                text: chip.modelData.label
                color: chip.active ? seg.activeColor
                                   : (area.containsMouse ? Theme.textPrimary : Theme.textSecondary)
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontBody
                font.weight: chip.active ? Font.DemiBold : Font.Normal

                Behavior on color { ColorAnimation { duration: Theme.durFast } }
            }

            MouseArea {
                id: area
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    if (chip.active) return
                    seg.picked(chip.modelData.key)
                }
            }
        }
    }
}
