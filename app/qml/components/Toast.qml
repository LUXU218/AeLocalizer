import QtQuick
import AeLocalizer

Rectangle {
    id: toast
    property string level: "info"
    property string text: ""

    width: Math.min(label.implicitWidth + Theme.spaceLg * 2, 420)
    height: 38
    radius: Theme.radiusControl
    color: Theme.panelFill
    border.width: 1
    border.color: level === "error" ? Theme.danger
                 : (level === "warn" ? Theme.warn : Theme.border)
    opacity: 0

    Row {
        anchors.centerIn: parent
        spacing: Theme.spaceSm
        Rectangle {
            width: 6; height: 6; radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: toast.level === "error" ? Theme.danger
                  : (toast.level === "warn" ? Theme.warn : Theme.success)
        }
        Text {
            id: label
            text: toast.text
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            width: Math.min(implicitWidth, 380)
        }
    }

    Component.onCompleted: {
        enter.start()
        timer.start()
    }

    NumberAnimation {
        id: enter
        target: toast
        property: "opacity"
        to: 1
        duration: Theme.durNormal
        easing.type: Easing.OutCubic
    }

    Timer {
        id: timer
        interval: 2600
        onTriggered: exit.start()
    }

    NumberAnimation {
        id: exit
        target: toast
        property: "opacity"
        to: 0
        duration: Theme.durFast
        onFinished: toast.destroy()
    }
}
