import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import AeLocalizer
import "../components"

Item {
    id: page
    property var backend

    readonly property real widthScale: Math.max(0, Math.min(1, (width - 700) / 270))
    readonly property int panePadding: Math.round(Theme.spaceMd + (Theme.spaceLg - Theme.spaceMd) * widthScale)

    function saveApiKey() {
        const value = apiKey.text.trim()
        if (!backend || !value.length)
            return
        apiKey.clear()
        backend.setAiKey(value)
    }

    component ToolbarLabel: Text {
        color: Theme.textSecondary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceMd

        Rectangle {
            id: toolbar
            objectName: "translationToolbar"
            Layout.fillWidth: true
            Layout.preferredHeight: controls.implicitHeight + 2 * Theme.spaceSm
            color: Theme.panelFill
            radius: Theme.radiusCard

            ColumnLayout {
                id: controls
                anchors.fill: parent
                anchors.leftMargin: page.panePadding
                anchors.rightMargin: page.panePadding
                anchors.topMargin: Theme.spaceSm
                anchors.bottomMargin: Theme.spaceSm
                spacing: Theme.spaceSm

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceSm

                    ChoiceBox {
                        objectName: "versionBox"
                        popupName: "versionPop"
                        Layout.preferredWidth: 104 + 50 * page.widthScale
                        Layout.minimumWidth: 92
                        Accessible.name: "After Effects 版本"
                        model: page.backend ? page.backend.versions : []
                        currentIndex: page.backend ? model.indexOf(page.backend.currentVersion) : -1
                        displayText: currentIndex >= 0 ? "AE " + currentText : "选择 AE"
                        onActivated: if (page.backend) page.backend.setVersion(currentText)
                    }

                    Button {
                        text: "扫描"
                        Layout.preferredHeight: 34
                        enabledButton: page.backend && !page.backend.busy
                        onClicked: page.backend.scan()
                    }

                    Item { Layout.fillWidth: true }

                    Button {
                        objectName: "startTranslationButton"
                        text: "开始翻译"
                        primary: true
                        Layout.preferredHeight: 34
                        enabledButton: page.backend && !page.backend.busy
                        onClicked: {
                            page.saveApiKey()
                            page.backend.startTranslate()
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: Theme.borderSoft
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceSm

                    ToolbarLabel { text: "服务商" }

                    ChoiceBox {
                        objectName: "aiProviderBox"
                        popupName: "providerPop"
                        Layout.minimumWidth: 108
                        Layout.preferredWidth: 138 + 54 * page.widthScale
                        Accessible.name: "翻译服务商"
                        textRole: "label"
                        valueRole: "key"
                        model: page.backend ? page.backend.aiProviders : []
                        currentIndex: page.backend ? model.findIndex(item => item.key === page.backend.aiProvider) : -1
                        displayText: currentText.replace("（默认）", "")
                        onActivated: if (page.backend && currentValue !== page.backend.aiProvider)
                                         page.backend.setAiProvider(currentValue)
                    }

                    ToolbarLabel {
                        Layout.leftMargin: Theme.spaceXs
                        text: "API Key"
                    }

                    Basic.TextField {
                        id: apiKey
                        objectName: "aiKeyInput"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 120
                        Layout.preferredHeight: 34
                        Accessible.name: "API Key"
                        enabled: !!page.backend
                        echoMode: TextInput.Password
                        inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                        placeholderText: page.backend && page.backend.aiHasKey
                                         ? page.backend.aiKeyMasked : "粘贴服务商密钥"
                        placeholderTextColor: Theme.textSecondary
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                        selectByMouse: true
                        leftPadding: Theme.spaceSm
                        rightPadding: Theme.spaceSm
                        onAccepted: page.saveApiKey()

                        background: Rectangle {
                            radius: Theme.radiusControl
                            color: Theme.insetFill
                            border.width: apiKey.activeFocus ? 1 : 0
                            border.color: Theme.accent
                        }
                    }

                    Button {
                        objectName: "saveApiKeyButton"
                        text: "保存"
                        Layout.preferredHeight: 34
                        enabledButton: page.backend && apiKey.text.trim().length > 0
                        onClicked: page.saveApiKey()
                    }

                    Button {
                        objectName: "aiSettingsButton"
                        text: "接口设置"
                        Layout.preferredHeight: 34
                        enabledButton: !!page.backend
                        onClicked: {
                            page.saveApiKey()
                            settings.open()
                        }
                    }
                }
            }
        }

        AiSettingsPopup {
            id: settings
            backend: page.backend
            parent: page
            y: toolbar.height + Theme.spaceXs
            width: Math.min(page.width, 580)
            x: page.width - width
            height: Math.min(implicitHeight, page.height - y)
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: progressContent.implicitHeight + Theme.spaceSm * 2
            visible: page.backend && page.backend.busy && page.backend.aiProgressText.length > 0
            radius: Theme.radiusCard
            color: Theme.panelFill

            RowLayout {
                id: progressContent
                anchors.fill: parent
                anchors.leftMargin: page.panePadding
                anchors.rightMargin: page.panePadding
                spacing: Theme.spaceMd

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceXs

                    RowLayout {
                        Layout.fillWidth: true

                        Text {
                            Layout.fillWidth: true
                            text: page.backend ? page.backend.aiProgressText : ""
                            color: Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            elide: Text.ElideRight
                        }

                        Text {
                            text: page.backend ? page.backend.aiCostText : ""
                            color: Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                        }
                    }

                    StatBar {
                        Layout.fillWidth: true
                        value: page.backend ? page.backend.aiProgress / 100 : 0
                        fillColor: Theme.accent
                    }
                }

                Button {
                    text: "取消"
                    quiet: true
                    onClicked: page.backend.cancelTranslate()
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panelFill
            radius: Theme.radiusCard

            RowLayout {
                anchors.fill: parent
                spacing: 0

                ColumnLayout {
                    Layout.preferredWidth: Math.max(176, Math.min(238, page.width * 0.25))
                    Layout.fillWidth: false
                    Layout.fillHeight: true
                    Layout.margins: page.panePadding
                    spacing: Theme.spaceSm

                    Text {
                        text: "插件"
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontTitle
                        font.weight: Font.DemiBold
                    }

                    PluginsPanel {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        backend: page.backend
                    }
                }

                Rectangle {
                    Layout.fillHeight: true
                    Layout.preferredWidth: 1
                    color: Theme.borderSoft
                }

                DraftTable {
                    id: drafts
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.margins: page.panePadding
                    backend: page.backend
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Text {
                Layout.fillWidth: true
                text: page.backend ? page.backend.aiDraftSummary : ""
                color: Theme.textOnBgSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                elide: Text.ElideRight
            }

            Button {
                objectName: "discardDraftButton"
                text: "丢弃"
                Layout.minimumWidth: 70
                Layout.preferredHeight: 34
                enabledButton: page.backend && page.backend.aiDraftCount > 0
                onClicked: {
                    drafts.commitActiveEdit()
                    page.backend.discardDraft()
                }
            }

            Button {
                objectName: "acceptDraftButton"
                text: "全部接受"
                primary: true
                Layout.minimumWidth: 96
                Layout.preferredHeight: 34
                enabledButton: page.backend && page.backend.aiDraftCount > 0
                onClicked: {
                    drafts.commitActiveEdit()
                    page.backend.acceptDraft()
                }
            }
        }
    }
}
