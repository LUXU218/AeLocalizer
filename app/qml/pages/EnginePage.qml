import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Templates as Templates
import QtQuick.Layouts
import AeLocalizer
import "../components"

Item {
    id: root

    property var backend: null

    component Disclosure: ColumnLayout {
        id: group

        default property alias content: body.data
        property string title: ""
        property alias buttonName: header.objectName

        Layout.fillWidth: true
        spacing: 0

        Templates.Button {
            id: header

            Layout.fillWidth: true
            text: group.title
            checkable: true
            padding: Theme.spaceSm
            implicitHeight: Math.max(38, contentItem.implicitHeight + topPadding + bottomPadding)
            hoverEnabled: true

            background: Rectangle {
                radius: Theme.radiusControl
                color: header.checked || header.hovered ? Theme.insetFill : "transparent"
                border.color: Theme.accent
                border.width: header.visualFocus ? 1 : 0
            }

            contentItem: RowLayout {
                spacing: Theme.spaceSm

                Text {
                    Layout.fillWidth: true
                    text: header.text
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }

                Text {
                    text: header.checked ? "\uE70E" : "\uE70D"
                    color: Theme.textSecondary
                    font.family: Theme.iconFamily
                    font.pixelSize: Theme.fontSmall
                }
            }
        }

        ColumnLayout {
            id: body

            Layout.fillWidth: true
            Layout.margins: Theme.spaceSm
            visible: header.checked
            spacing: Theme.spaceSm
        }
    }

    Flickable {
        id: viewport

        anchors.fill: parent
        contentWidth: width
        contentHeight: page.implicitHeight + Theme.spaceXs
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }

        ColumnLayout {
            id: page

            x: Math.round((viewport.width - width) / 2)
            width: Math.min(viewport.width, 1100)
            spacing: Theme.spaceMd

            EngineCard {
                Layout.fillWidth: true
                backend: root.backend
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: advanced.implicitHeight + Theme.spaceMd * 2
                radius: Theme.radiusCard
                color: Theme.panelFill
                border.width: 1
                border.color: Theme.hasAnyBg ? "transparent" : Theme.border

                ColumnLayout {
                    id: advanced

                    anchors.fill: parent
                    anchors.leftMargin: Theme.spaceLg
                    anchors.rightMargin: Theme.spaceLg
                    anchors.topMargin: Theme.spaceMd
                    anchors.bottomMargin: Theme.spaceMd
                    spacing: 0

                    Text {
                        Layout.bottomMargin: Theme.spaceSm
                        text: "高级"
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontTitle
                        font.weight: Font.DemiBold
                    }

                    Disclosure {
                        buttonName: "engineFixerDisclosure"
                        title: "UTF-8 修复层"

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.spaceMd

                            Button {
                                objectName: "engineFixerAction"
                                text: root.backend && root.backend.fixerInstalled ? "移除" : "安装"
                                enabledButton: !!root.backend && !root.backend.busy
                                onClicked: {
                                    if (root.backend.fixerInstalled)
                                        root.backend.removeFixer()
                                    else
                                        root.backend.installFixer()
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.leftMargin: Theme.spaceSm
                        Layout.rightMargin: Theme.spaceSm
                        implicitHeight: 1
                        color: Theme.border
                    }

                    Disclosure {
                        buttonName: "engineRescueDisclosure"
                        title: "AE 启动故障处理"

                        Flow {
                            Layout.fillWidth: true
                            Layout.preferredHeight: implicitHeight
                            spacing: Theme.spaceSm

                            Button {
                                objectName: "engineRescueAction"
                                text: "停止引擎并结束 AE"
                                danger: true
                                enabledButton: !!root.backend
                                onClicked: root.backend.rescueStopAll()
                            }

                            Button {
                                objectName: "engineLaunchAction"
                                text: "启动 AE"
                                enabledButton: !!root.backend
                                onClicked: root.backend.launchAE()
                            }

                            Button {
                                objectName: "engineDismissAction"
                                text: "关闭阻挡对话框"
                                enabledButton: !!root.backend
                                onClicked: root.backend.dismissAEDialogs()
                            }
                        }
                    }
                }
            }
        }
    }
}
