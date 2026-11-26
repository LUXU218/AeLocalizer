import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import AeLocalizer

Flickable {
    id: flick
    default property alias content: col.data

    contentWidth: width
    contentHeight: col.implicitHeight + Theme.spaceSm
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    Basic.ScrollBar.vertical: Basic.ScrollBar {
        id: bar
        policy: Basic.ScrollBar.AsNeeded
        padding: 2
        background: null
        contentItem: Rectangle {
            implicitWidth: 5
            implicitHeight: 28
            radius: width / 2
            color: Theme.textSecondary
            opacity: bar.pressed || bar.hovered ? 0.8 : 0.45
        }
    }

    ColumnLayout {
        id: col
        width: Math.max(0, flick.width - bar.implicitWidth - Theme.spaceXs)
        spacing: Theme.spaceMd
    }
}
