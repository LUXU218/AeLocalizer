import QtQuick
import QtQuick.Layouts
import AeLocalizer

Rectangle {
    id: card
    default property alias content: inner.data
    property string title: ""
    property string hint: ""

    Layout.fillWidth: true
    implicitHeight: col.implicitHeight + Theme.spaceLg * 2
    radius: Theme.radiusCard
    border.width: 0
    color: "transparent"

    // 染色矩形绘制在磨砂采样上方。
    GlassBackdrop { anchors.fill: parent; radius: card.radius }

    Rectangle {
        anchors.fill: parent
        radius: card.radius
        color: Theme.hasAnyBg ? Theme.cardFill : Theme.panelFill
        border.color: Theme.panelStroke
    }

    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: Theme.spaceLg
        anchors.rightMargin: Theme.spaceLg
        anchors.topMargin: Theme.spaceLg
        spacing: Theme.spaceSm

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1

            Text {
                text: card.title
                color: Theme.textOnBg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            Text {
                text: card.hint
                visible: card.hint.length > 0
                Layout.fillWidth: true
                color: Theme.textOnBgSecondary
                font.family: Theme.fontFamily
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                lineHeight: 1.3
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.borderSoft
        }

        ColumnLayout {
            id: inner
            Layout.fillWidth: true
            spacing: Theme.spaceSm
        }
    }
}
