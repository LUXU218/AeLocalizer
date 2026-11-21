import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import AeLocalizer

Item {
    id: panel
    property var backend: null
    property string selectionText: ""

    function syncSelection() {
        selectionText = backend ? "已选 " + backend.selectedCount + " 组 · "
                        + backend.selectedFiles + " 个文件" : ""
    }

    onBackendChanged: syncSelection()

    Connections {
        target: panel.backend
        function onInventoryChanged() { panel.syncSelection() }
        function onStateChanged() { panel.syncSelection() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceXs

        Basic.TextField {
            id: search
            objectName: "pluginSearch"
            Layout.fillWidth: true
            implicitHeight: 34
            Accessible.name: "搜索插件"
            placeholderText: "搜索插件"
            placeholderTextColor: Theme.textSecondary
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
            leftPadding: Theme.spaceMd + 20
            rightPadding: Theme.spaceSm
            selectByMouse: true
            onTextChanged: if (panel.backend) panel.backend.setGroupFilter(text)

            background: Rectangle {
                radius: Theme.radiusControl
                color: Theme.insetFill
                border.width: search.activeFocus ? 1 : 0
                border.color: Theme.accent
            }

            Text {
                anchors.left: parent.left
                anchors.leftMargin: Theme.spaceMd
                anchors.verticalCenter: parent.verticalCenter
                text: "\uE721"
                font.family: Theme.iconFamily
                font.pixelSize: 13
                color: Theme.textSecondary
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceXs

            Button {
                text: "全选"
                quiet: true
                Layout.preferredHeight: 26
                onClicked: if (panel.backend) panel.backend.selectAll()
            }
            Button {
                text: "清空"
                quiet: true
                Layout.preferredHeight: 26
                onClicked: if (panel.backend) panel.backend.selectNone()
            }
            Item { Layout.fillWidth: true }
        }

        ListView {
            id: groups
            objectName: "pluginChecklist"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: panel.backend ? panel.backend.groupModel : null
            boundsBehavior: Flickable.StopAtBounds
            Basic.ScrollBar.vertical: Basic.ScrollBar { policy: Basic.ScrollBar.AsNeeded }

            delegate: Basic.CheckDelegate {
                id: group
                width: groups.width
                height: Math.max(35, Theme.fontBody + Theme.spaceMd * 2)
                checked: model.selected
                text: model.label
                leftPadding: Theme.spaceSm
                rightPadding: Theme.spaceSm
                spacing: Theme.spaceSm
                hoverEnabled: true
                onClicked: if (panel.backend) panel.backend.toggleGroup(model.id)
                Basic.ToolTip.visible: hovered
                Basic.ToolTip.delay: 600
                Basic.ToolTip.text: model.label + "\n" + model.vendor + " · " + model.files + " 个文件"

                indicator: Rectangle {
                    x: group.leftPadding
                    y: (group.height - height) / 2
                    width: 16
                    height: 16
                    radius: 4
                    color: group.checked ? Theme.actionFill : "transparent"
                    border.width: group.checked ? 0 : 1
                    border.color: Theme.controlStroke

                    Text {
                        anchors.centerIn: parent
                        visible: group.checked
                        text: "\uE8FB"
                        color: Theme.actionText
                        font.family: Theme.iconFamily
                        font.pixelSize: 10
                    }
                }

                contentItem: Text {
                    leftPadding: group.indicator.width + group.spacing
                    text: group.text
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }

                background: Rectangle {
                    radius: Theme.radiusControl
                    color: group.hovered || group.visualFocus ? Theme.bgHover : "transparent"

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 1
                        color: Theme.borderSoft
                    }
                }
            }
        }

        Text {
            objectName: "pluginSelectionCount"
            Layout.fillWidth: true
            visible: !!panel.backend
            text: panel.selectionText
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            Basic.ToolTip.text: panel.backend ? "词典 " + panel.backend.dictCount + " 条" : ""
            Basic.ToolTip.visible: coverageHover.hovered
            Basic.ToolTip.delay: 600
            HoverHandler { id: coverageHover }
        }
    }
}
