import QtQuick
import AeLocalizer

Rectangle {
    id: btn
    property string text: ""
    property bool primary: false
    property bool danger: false
    property bool quiet: false
    property bool enabledButton: true
    signal clicked()

    readonly property color primaryBgPressed: Qt.darker(Theme.actionFill, 1.2)

    implicitWidth: label.implicitWidth + Theme.spaceLg * 2
    implicitHeight: 30
    radius: Theme.radiusControl
    opacity: enabledButton ? 1.0 : 0.4

    color: {
        if (danger) return area.containsMouse
                ? (Theme.dark ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.18) : "#FEF2F2")
                : "transparent"
        if (primary) return area.pressed ? primaryBgPressed
                : (area.containsMouse ? Theme.actionHover : Theme.actionFill)
        if (quiet) return area.containsMouse ? Theme.bgHover : "transparent"
        return area.pressed ? Theme.bgHover
                            : (area.containsMouse
                               ? (Theme.dark ? Theme.bgActive : "#FBFBFC")
                               : Theme.panelFill)
    }
    border.width: primary || quiet ? 0 : 1
    border.color: danger ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.35) : Theme.border

    Behavior on color { ColorAnimation { duration: Theme.durFast } }
    Behavior on border.color { ColorAnimation { duration: Theme.durFast } }

    Text {
        id: label
        anchors.centerIn: parent
        text: btn.text
        color: btn.primary ? Theme.actionText : (btn.danger ? Theme.danger : Theme.textSecondary)
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
        font.weight: btn.primary ? Font.DemiBold : Font.Normal

        Behavior on color { ColorAnimation { duration: Theme.durFast } }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: btn.enabledButton ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (btn.enabledButton) btn.clicked()
    }
}
