import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Dialogs
import AeLocalizer
import "../components"

ColumnLayout {
    id: page

    property var controller: null
    property bool scanned: false
    readonly property bool available: !!controller && !controller.busy
    readonly property var terms: controller ? controller.terms : null
    readonly property bool signedCopy: !!controller && controller.usesSignedCopy
    readonly property bool canApply: available && !controller.blockedReason && terms.readyCount > 0

    onVisibleChanged: {
        if (visible && controller && !scanned) {
            scanned = true
            controller.scan()
        }
    }

    component Caption: Text {
        color: Theme.textSecondary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        textFormat: Text.PlainText
    }

    component TermCheckBox: Basic.CheckBox {
        id: checkBox
        implicitWidth: 34
        implicitHeight: 30
        hoverEnabled: true

        indicator: Rectangle {
            x: (checkBox.width - width) / 2
            y: (checkBox.height - height) / 2
            width: 16
            height: 16
            radius: 5
            antialiasing: true
            opacity: checkBox.enabled ? 1 : 0.55
            color: checkBox.checked ? Theme.actionFill
                   : checkBox.hovered ? Theme.bgHover : Theme.fieldFill
            border.width: checkBox.visualFocus ? 2 : 1
            border.color: checkBox.hovered || checkBox.visualFocus ? Theme.accent
                          : checkBox.checked ? Theme.actionFill : Theme.controlStroke

            Text {
                anchors.centerIn: parent
                visible: checkBox.checked
                text: "\uE73E"
                color: Theme.actionText
                font.family: Theme.iconFamily
                font.pixelSize: 10
            }
        }
    }

    SettingCard {
        title: "汉化目标"
        radius: Theme.radiusDialog

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            ChoiceBox {
                objectName: "panelTargets"
                Layout.fillWidth: true
                model: page.controller ? page.controller.targets : []
                textRole: "label"
                currentIndex: page.controller ? page.controller.targetIndex : -1
                enabled: page.available
                Accessible.name: "选择面板或脚本"
                onActivated: function(index) { page.controller.select(index) }
            }
            Button {
                objectName: "panelScan"
                text: "扫描"
                enabledButton: page.available
                onClicked: page.controller.scan()
            }
            Button {
                text: "添加 CEP"
                enabledButton: page.available
                onClicked: selectPanel.open()
            }
            Button {
                text: "添加 JSX"
                enabledButton: page.available
                onClicked: selectScript.open()
            }
        }

        Caption {
            Layout.fillWidth: true
            visible: text.length > 0
            text: page.controller ? page.controller.targetPath : ""
            elide: Text.ElideMiddle
        }

        Caption {
            Layout.fillWidth: true
            visible: text.length > 0
            text: page.signedCopy ? "已签名 CEP · 汉化后在 AE 菜单中打开简体中文副本"
                                  : page.controller ? page.controller.blockedReason : ""
            color: Theme.warn
            wrapMode: Text.Wrap
        }
    }

    Surface {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 120
        radius: Theme.radiusDialog
        clip: true

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme.spaceMd
            spacing: Theme.spaceSm

            Flow {
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                spacing: Theme.spaceSm

                Button {
                    objectName: "panelExtract"
                    text: "读取界面文字"
                    primary: true
                    enabledButton: page.available && page.controller.canRead
                    onClicked: page.controller.extract()
                }
                Button {
                    objectName: "panelTranslate"
                    text: "AI 翻译所选"
                    enabledButton: page.available && page.terms.selectedCount > 0
                    onClicked: page.controller.translate()
                }
                Button {
                    text: "保存译文"
                    enabledButton: page.available && page.terms.count > 0
                    onClicked: page.controller.save()
                }
                Button {
                    objectName: "panelPreview"
                    text: page.signedCopy ? "安装汉化副本" : "预览并应用"
                    primary: true
                    enabledButton: page.canApply
                    onClicked: {
                        if (page.signedCopy && !page.controller.hasCepSigner)
                            selectSigner.open()
                        else
                            page.controller.preview()
                    }
                }
                Button {
                    objectName: "panelExport"
                    visible: !!page.controller && page.controller.isScript
                    text: "导出汉化副本"
                    enabledButton: page.canApply
                    onClicked: exportScript.open()
                }
                Button {
                    objectName: "panelRestore"
                    text: page.signedCopy ? "移除汉化副本" : "恢复原文"
                    enabledButton: page.available && page.controller.canRestore
                    onClicked: confirmRestore.open()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.rightMargin: Theme.spaceSm
                TermCheckBox {
                    objectName: "panelSelectAll"
                    opacity: page.terms && page.terms.count > 0 ? 1 : 0
                    enabled: page.available && page.terms.count > 0
                    checked: !!page.terms && page.terms.count > 0
                             && page.terms.selectedCount === page.terms.count
                    Accessible.name: "选择全部词条"
                    onToggled: page.controller.chooseAll(checked)
                }
                Caption { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "原文" }
                Caption { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "中文译文" }
            }

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: page.terms
                boundsBehavior: Flickable.StopAtBounds
                Basic.ScrollBar.vertical: Basic.ScrollBar {}

                delegate: Rectangle {
                    id: row
                    required property int index
                    required property string sourceText
                    required property string translation
                    required property string origin
                    required property bool selected
                    width: list.width
                    height: Math.max(46, words.implicitHeight + Theme.spaceSm * 2)
                    color: index % 2 === 0 ? Theme.insetFill : "transparent"
                    radius: Theme.radiusControl

                    RowLayout {
                        id: words
                        anchors.fill: parent
                        anchors.rightMargin: Theme.spaceSm
                        enabled: page.available

                        TermCheckBox {
                            checked: row.selected
                            Accessible.name: "选择 " + row.sourceText
                            onToggled: page.controller.choose(row.index, checked)
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.preferredWidth: (list.width - 62) / 2
                            spacing: Theme.spaceXs
                            Caption {
                                Layout.fillWidth: true
                                maximumLineCount: 2
                                elide: Text.ElideRight
                                wrapMode: Text.Wrap
                                color: Theme.textPrimary
                                text: row.sourceText
                            }
                            Caption {
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                                text: row.origin
                            }
                        }
                        Basic.TextField {
                            id: translationField
                            objectName: "panelTranslationField"
                            Layout.fillWidth: true
                            Layout.preferredWidth: (list.width - 62) / 2
                            selectByMouse: true
                            text: row.translation
                            Accessible.name: "中文译文"
                            color: Theme.textPrimary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontBody
                            onTextEdited: page.controller.edit(row.index, text)
                            background: Rectangle {
                                radius: Theme.radiusControl
                                color: Theme.fieldFill
                                border.color: translationField.activeFocus ? Theme.accent : Theme.border
                            }
                        }
                    }
                }

                Caption {
                    anchors.centerIn: parent
                    visible: list.count === 0
                    text: "暂无界面词条"
                }
            }
        }
    }

    Caption {
        Layout.fillWidth: true
        visible: text.length > 0
        text: page.controller ? page.controller.statusText : ""
        color: page.controller && page.controller.error ? Theme.danger : Theme.textSecondary
        wrapMode: Text.Wrap
        maximumLineCount: 5
        elide: Text.ElideRight
    }

    FolderDialog {
        id: selectPanel
        title: "选择含 CSXS/manifest.xml 的扩展目录"
        onAccepted: page.controller.addLocation(selectedFolder)
    }
    FileDialog {
        id: selectScript
        title: "选择明文 JSX 脚本"
        nameFilters: ["明文 JSX (*.jsx)"]
        onAccepted: page.controller.addLocation(selectedFile)
    }
    FileDialog {
        id: selectSigner
        title: "选择 Adobe ZXPSignCmd 4.1.103（Windows 64 位）"
        nameFilters: ["Adobe ZXPSignCmd (ZXPSignCmd.exe)"]
        onAccepted: page.controller.chooseCepSigner(selectedFile)
    }
    FileDialog {
        id: exportScript
        title: "保存汉化副本"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "jsx"
        nameFilters: ["明文 JSX (*.jsx)"]
        onAccepted: page.controller.preview(selectedFile)
    }

    PanelReviewDialog {
        id: review
        onAccepted: page.controller.commit()
        onRejected: page.controller.cancelReview()
    }

    Controls.Dialog {
        id: confirmRestore
        objectName: "panelRestoreDialog"
        title: page.signedCopy ? "移除汉化副本" : "恢复原文"
        parent: Controls.Overlay.overlay
        anchors.centerIn: parent
        implicitWidth: 390
        modal: true
        standardButtons: Controls.Dialog.Ok | Controls.Dialog.Cancel
        onOpened: {
            standardButton(Controls.Dialog.Ok).text = page.signedCopy ? "移除" : "恢复"
            standardButton(Controls.Dialog.Cancel).text = "取消"
        }
        onAccepted: page.controller.restore()
        contentItem: Caption {
            text: page.signedCopy ? "将移除当前用户的汉化副本。已保存的译文会保留。"
                                  : "将使用本机备份恢复目标文件。已保存的译文会保留。"
            wrapMode: Text.Wrap
        }
    }

    Connections {
        target: page.controller
        function onReviewReady(details) {
            review.preview = details
            review.open()
        }
    }
}
