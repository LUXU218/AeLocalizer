import QtQuick
import QtQuick.Controls.Basic as Basic
import AeLocalizer

Basic.ComboBox {
    id: choice
    property string popupName: ""

    implicitHeight: 34
    hoverEnabled: true
    leftPadding: Theme.spaceMd
    rightPadding: 30
    font.family: Theme.fontFamily
    font.pixelSize: Theme.fontBody

    contentItem: Text {
        text: choice.displayText
        color: Theme.textPrimary
        font: choice.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Text {
        x: choice.width - width - Theme.spaceMd
        y: (choice.height - height) / 2
        text: choice.popup.visible ? "\uE70E" : "\uE70D"
        color: Theme.textSecondary
        font.family: Theme.iconFamily
        font.pixelSize: 10
    }

    background: Rectangle {
        radius: Theme.radiusControl
        color: choice.hovered ? Theme.insetFill : Theme.fieldFill
        border.width: 1
        border.color: choice.visualFocus || choice.popup.visible ? Theme.accent : Theme.controlStroke
    }

    delegate: Basic.ItemDelegate {
        id: option
        required property int index
        required property var modelData
        width: ListView.view.width
        height: 32
        highlighted: choice.highlightedIndex === index

        contentItem: Text {
            text: choice.textRole ? option.modelData[choice.textRole] : option.modelData
            color: Theme.textPrimary
            font: choice.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        background: Rectangle {
            color: option.highlighted || option.hovered ? Theme.selectionFill : "transparent"
            radius: Theme.radiusControl
        }
    }

    popup: Basic.Popup {
        objectName: choice.popupName
        y: choice.height + 4
        width: choice.width
        padding: 4
        onOpened: if (choice.currentIndex >= 0)
                      options.positionViewAtIndex(choice.currentIndex, ListView.Center)

        contentItem: ListView {
            id: options
            implicitHeight: Math.min(contentHeight, 256)
            clip: true
            model: choice.delegateModel
            currentIndex: choice.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            Basic.ScrollBar.vertical: Basic.ScrollBar {}
        }

        background: Rectangle {
            radius: Theme.radiusControl
            color: Theme.popupFill
            border.width: 1
            border.color: Theme.controlStroke
        }
    }
}
