import QtQuick
import QtQuick.Layouts
import AeLocalizer

Item {
    id: slider
    property real value: 0.5
    property real min: 0.0
    property real max: 1.0
    property string suffix: ""
    signal moved(real v)

    implicitWidth: 160
    implicitHeight: 22

    readonly property real ratio: max > min ? (value - min) / (max - min) : 0

    function apply(mx) {
        var t = Math.min(1, Math.max(0, (mx - 6) / Math.max(1, width - 12)))
        var v = min + t * (max - min)
        value = v
        moved(v)
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        height: 3
        radius: 1.5
        color: Theme.border
    }
    Rectangle {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: 6 + slider.ratio * (slider.width - 12)
        height: 3
        radius: 1.5
        color: Theme.accent
    }
    Rectangle {
        anchors.verticalCenter: parent.verticalCenter
        x: 6 + slider.ratio * (slider.width - 12) - 6
        width: 12; height: 12; radius: 6
        color: Theme.panelFill
        border.width: 2
        border.color: Theme.accent
    }
    MouseArea {
        anchors.fill: parent
        anchors.margins: -6
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        // 滑杆嵌在滚动区时需要保留拖动手势。
        preventStealing: true
        onPressed: function(mouse) { slider.apply(mouse.x) }
        onPositionChanged: function(mouse) { if (pressed) slider.apply(mouse.x) }
    }
}
