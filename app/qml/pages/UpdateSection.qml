import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import AeLocalizer
import "../components"

SectionedPage {
    id: root

    property var backend: null
    readonly property string repository: backend ? backend.updateRepository : ""
    readonly property string updateState: backend ? backend.updateState : ""
    readonly property string statusText: backend ? backend.updateText : ""
    readonly property bool busy: ["checking", "downloading", "installing"].indexOf(updateState) >= 0
    readonly property bool canDownload: updateState === "available" || updateState === "download-error"
    readonly property bool failed: updateState === "error" || updateState === "download-error"
    readonly property int progress: backend ? backend.updateProgress : -1
    property bool editingSource: false
    property bool invalidSource: false

    function saveSource() {
        if (!backend || busy)
            return
        invalidSource = !backend.setUpdateRepository(sourceInput.text)
        if (!invalidSource) {
            sourceInput.text = repository
            editingSource = false
        }
    }

    component Caption: Text {
        color: Theme.textSecondary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        textFormat: Text.PlainText
    }

    component Action: Basic.Button {
        id: action
        property bool primary: false
        implicitHeight: 34
        leftPadding: Theme.spaceMd
        rightPadding: Theme.spaceMd
        contentItem: Text {
            text: action.text
            color: action.primary && action.enabled ? Theme.actionText : Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            font.weight: action.primary ? Font.DemiBold : Font.Normal
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            opacity: action.enabled ? 1 : 0.55
        }
        background: Rectangle {
            radius: Theme.radiusControl
            color: action.primary && action.enabled
                ? (action.hovered || action.down ? Theme.actionHover : Theme.actionFill)
                : (action.hovered || action.down ? Theme.bgHover : Theme.panelFill)
            border.width: action.visualFocus ? 2 : (action.primary && action.enabled ? 0 : 1)
            border.color: action.visualFocus ? Theme.accent : Theme.border
        }
    }

    SettingCard {
        title: "版本"

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd

            Caption { text: "当前版本" }

            Text {
                objectName: "updateCurrentVersion"
                text: root.backend ? "v" + root.backend.appVersion : "—"
                color: Theme.textPrimary
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fontBody + 2
                font.weight: Font.DemiBold
            }

            Item { Layout.fillWidth: true }

            Action {
                objectName: "updateCheckAction"
                text: root.updateState === "checking" ? "检查中…" : "检查更新"
                primary: !root.canDownload && root.updateState !== "downloaded"
                enabled: root.repository.length > 0 && !root.busy
                onClicked: root.backend.checkUpdate()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.borderSoft
            visible: updateDetails.visible
        }

        ColumnLayout {
            id: updateDetails
            Layout.fillWidth: true
            visible: root.statusText.length > 0
            spacing: Theme.spaceMd

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm
                Rectangle {
                    Layout.preferredWidth: 7
                    Layout.preferredHeight: 7
                    radius: 4
                    color: root.failed ? Theme.danger
                         : root.updateState === "latest" || root.updateState === "downloaded" ? Theme.success
                         : Theme.accent
                }
                Caption {
                    objectName: "updateStatus"
                    Layout.fillWidth: true
                    text: root.statusText
                    color: root.failed ? Theme.danger : Theme.textPrimary
                    font.pixelSize: Theme.fontBody
                    wrapMode: Text.Wrap
                }
                Caption {
                    visible: root.updateState === "downloading" && root.progress >= 0
                    text: root.progress + "%"
                    font.family: Theme.monoFamily
                }
            }

            Basic.ProgressBar {
                Layout.fillWidth: true
                visible: root.updateState === "downloading"
                to: 100
                value: Math.max(0, root.progress)
                indeterminate: visible && root.progress < 0
                Accessible.name: "下载进度"
            }

            Flow {
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                visible: root.canDownload || root.updateState === "downloaded"
                spacing: Theme.spaceSm

                Action {
                    objectName: "updateDownloadAction"
                    visible: root.canDownload
                    text: root.updateState === "download-error" ? "重试下载" : "下载更新"
                    primary: true
                    onClicked: root.backend.downloadUpdate()
                }
                Action {
                    objectName: "updateInstallAction"
                    visible: root.updateState === "downloaded"
                    text: "安装并重启"
                    primary: true
                    onClicked: installConfirmation.open()
                }
                Action {
                    objectName: "updateOpenFolderAction"
                    visible: root.updateState === "downloaded"
                    text: "打开下载文件夹"
                    onClicked: root.backend.openUpdateFolder()
                }
            }
        }
    }

    SettingCard {
        title: "更新源"

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd

            Text {
                text: "\uE71B"
                color: Theme.textSecondary
                font.family: Theme.iconFamily
                font.pixelSize: Theme.fontTitle
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Caption {
                    text: "GitHub Releases"
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                }
                Caption {
                    Layout.fillWidth: true
                    text: root.repository || "未配置仓库"
                    elide: Text.ElideMiddle
                    font.family: root.repository ? Theme.monoFamily : Theme.fontFamily
                }
            }
            Action {
                objectName: "updateReleasePageAction"
                visible: root.repository.length > 0
                text: "查看发布页 ↗"
                onClicked: Qt.openUrlExternally("https://github.com/" + root.repository + "/releases")
            }
            Action {
                objectName: "updateEditSourceAction"
                visible: !root.editingSource
                enabled: !root.busy
                text: root.repository ? "更改仓库" : "配置仓库"
                onClicked: {
                    sourceInput.text = root.repository
                    root.invalidSource = false
                    root.editingSource = true
                    sourceInput.forceActiveFocus()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: root.editingSource
            enabled: !root.busy
            spacing: Theme.spaceSm
            Basic.TextField {
                id: sourceInput
                objectName: "updateRepositoryInput"
                Layout.fillWidth: true
                Layout.preferredHeight: 34
                placeholderText: "GitHub 仓库地址或 owner/repo"
                Accessible.name: "GitHub 仓库"
                selectByMouse: true
                color: Theme.textPrimary
                placeholderTextColor: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontBody
                onAccepted: root.saveSource()
                onTextEdited: root.invalidSource = false
                background: Rectangle {
                    radius: Theme.radiusControl
                    color: Theme.fieldFill
                    border.width: sourceInput.activeFocus ? 2 : 1
                    border.color: root.invalidSource ? Theme.danger
                                : sourceInput.activeFocus ? Theme.accent : Theme.border
                }
            }
            Action {
                objectName: "updateSaveSourceAction"
                text: "保存"
                primary: true
                onClicked: root.saveSource()
            }
            Action {
                text: "取消"
                onClicked: {
                    root.editingSource = false
                    root.invalidSource = false
                }
            }
        }

        Caption {
            Layout.fillWidth: true
            visible: root.editingSource && root.invalidSource
            text: "请输入有效的 GitHub 仓库地址或 owner/repo。"
            color: Theme.danger
        }
    }

    Dialog {
        id: installConfirmation
        parent: Overlay.overlay
        anchors.centerIn: parent
        implicitWidth: 360
        modal: true
        title: "安装更新"
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: {
            standardButton(Dialog.Ok).text = "安装并重启"
            standardButton(Dialog.Cancel).text = "取消"
        }
        onAccepted: if (root.backend) root.backend.installUpdate()
        contentItem: Caption {
            text: root.backend ? "安装 v" + root.backend.updateLatest + " 后，AeLocalizer 将重新启动。" : ""
            color: Theme.textPrimary
            wrapMode: Text.Wrap
        }
    }
}
