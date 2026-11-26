import QtQuick
import QtQuick.Templates as Templates
import AeLocalizer

Templates.Button {
    id: btn

    property bool primary: false
    property bool danger: false
    property bool quiet: false
    property bool enabledButton: true
    property string symbol: ""

    enabled: enabledButton
    hoverEnabled: true
    activeFocusOnTab: true
    horizontalPadding: Theme.spaceMd
    implicitWidth: contentItem.implicitWidth + horizontalPadding * 2
    implicitHeight: Math.max(32, contentItem.implicitHeight + Theme.spaceSm)
    opacity: enabled ? 1 : 0.52
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody
    font.weight: Font.Medium

    readonly property color ink: primary ? Theme.actionText : danger ? Theme.danger : Theme.textPrimary
    HoverHandler { cursorShape: btn.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor }

    contentItem: Item {
        implicitWidth: caption.implicitWidth
        implicitHeight: caption.implicitHeight
        Row {
            id: caption
            anchors.centerIn: parent
            spacing: 7
            Text {
                visible: btn.symbol !== ""
                anchors.verticalCenter: parent.verticalCenter
                text: btn.symbol
                font.family: Theme.iconFamily
                font.pixelSize: 13
                color: btn.ink
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: btn.text
                font: btn.font
                color: btn.ink
            }
        }
    }

    background: Rectangle {
        radius: Theme.radiusControl
        color: btn.primary ? (btn.down || btn.hovered ? Theme.actionHover : Theme.actionFill)
               : btn.down ? Theme.bgActive : btn.hovered ? Theme.bgHover
               : btn.quiet ? "transparent" : Theme.fieldFill
        border.width: btn.quiet ? 0 : 1
        border.color: btn.primary ? Theme.actionFill : btn.danger
                      ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.42) : Theme.controlStroke

        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: parent.radius + 3
            color: "transparent"
            border.width: 2
            border.color: Theme.focusRing
            visible: btn.visualFocus
        }
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 1
            width: Math.max(0, parent.width - parent.radius * 2)
            height: 1
            color: Qt.rgba(1, 1, 1, 0.16)
            visible: btn.primary && btn.enabled && !btn.down
        }
    }
}
