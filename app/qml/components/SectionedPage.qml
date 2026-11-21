import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import AeLocalizer

Flickable {
    id: flick
    default property alias content: col.data

    contentWidth: width
    contentHeight: col.implicitHeight + Theme.spaceSm
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

    ColumnLayout {
        id: col
        width: Math.max(0, flick.width - Theme.spaceXs)
        spacing: Theme.spaceMd
    }
}
