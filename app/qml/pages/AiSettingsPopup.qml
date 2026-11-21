import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import AeLocalizer
import "../components"

Basic.Popup {
    id: settings
    objectName: "aiSettingsPopup"
    property var backend

    padding: Theme.spaceMd
    implicitHeight: form.implicitHeight + 2 * padding
    focus: true
    closePolicy: Basic.Popup.CloseOnEscape | Basic.Popup.CloseOnPressOutside

    component FieldLabel: Text {
        color: Theme.textSecondary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        verticalAlignment: Text.AlignVCenter
    }

    component ConfigField: Basic.TextField {
        Layout.fillWidth: true
        implicitHeight: 32
        leftPadding: Theme.spaceSm
        rightPadding: Theme.spaceSm
        color: Theme.textPrimary
        placeholderTextColor: Theme.textSecondary
        selectionColor: Theme.accent
        selectedTextColor: "#FFFFFF"
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
        selectByMouse: true
        background: Rectangle {
            color: Theme.insetFill
            radius: Theme.radiusControl
            border.width: parent.activeFocus ? 1 : 0
            border.color: Theme.accent
        }
    }

    background: Rectangle {
        color: Theme.popupFill
        radius: Theme.radiusCard
        border.width: 1
        border.color: Theme.controlStroke
    }

    contentItem: Flickable {
        clip: true
        contentWidth: width
        contentHeight: form.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        Basic.ScrollBar.vertical: Basic.ScrollBar {}

        ColumnLayout {
            id: form
            width: Math.max(0, parent.width - Theme.spaceXs)
            spacing: Theme.spaceSm

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: Theme.spaceMd
                rowSpacing: Theme.spaceSm

                FieldLabel { text: "Base URL" }
                ConfigField {
                    Accessible.name: "Base URL"
                    text: settings.backend ? settings.backend.aiBaseUrl : ""
                    onEditingFinished: if (settings.backend && text !== settings.backend.aiBaseUrl)
                                           settings.backend.setAiBaseUrl(text)
                }

                FieldLabel { text: "模型" }
                ConfigField {
                    Accessible.name: "模型"
                    text: settings.backend ? settings.backend.aiModel : ""
                    onEditingFinished: if (settings.backend && text !== settings.backend.aiModel)
                                           settings.backend.setAiModel(text)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                FieldLabel {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    text: settings.backend && settings.backend.aiKeySaved ? "密钥已加密保存" : ""
                }

                Button {
                    text: "恢复上一个密钥"
                    quiet: true
                    visible: settings.backend && settings.backend.aiKeyCanRestore
                    onClicked: settings.backend.restoreAiKey()
                }

                Button {
                    text: "清除密钥"
                    quiet: true
                    visible: settings.backend && settings.backend.aiHasKey
                    onClicked: settings.backend.clearAiKey()
                }

                Button {
                    text: "测试连接"
                    onClicked: {
                        settings.contentItem.forceActiveFocus()
                        settings.backend.testAiConnection()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Theme.border
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm

                FieldLabel { text: "预算（美元）" }
                ConfigField {
                    Accessible.name: "预算（美元）"
                    Layout.fillWidth: false
                    Layout.preferredWidth: 60
                    text: settings.backend ? String(settings.backend.aiBudget) : ""
                    onEditingFinished: {
                        const amount = Number.parseFloat(text)
                        if (settings.backend && Number.isFinite(amount))
                            settings.backend.setAiBudget(amount)
                        if (settings.backend && text !== String(settings.backend.aiBudget))
                            text = Qt.binding(() => String(settings.backend.aiBudget))
                    }
                }

                FieldLabel { text: "每批条数" }
                ConfigField {
                    Accessible.name: "每批条数"
                    Layout.fillWidth: false
                    Layout.preferredWidth: 60
                    text: settings.backend ? String(settings.backend.aiBatch) : ""
                    onEditingFinished: {
                        const amount = Number.parseInt(text, 10)
                        if (settings.backend && Number.isFinite(amount))
                            settings.backend.setAiBatch(amount)
                        if (settings.backend && text !== String(settings.backend.aiBatch))
                            text = Qt.binding(() => String(settings.backend.aiBatch))
                    }
                }

                Item { Layout.fillWidth: true }

                Button {
                    text: "完成"
                    primary: true
                    onClicked: {
                        settings.contentItem.forceActiveFocus()
                        settings.close()
                    }
                }
            }
        }
    }
}
