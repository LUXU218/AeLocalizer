import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import AeLocalizer
import "../components"

Basic.Dialog {
    id: dialog
    objectName: "panelApplyDialog"
    property var preview: ({ files: [], termCount: 0, targetName: "", mode: "apply" })
    readonly property bool installing: preview.mode === "install"
    readonly property bool exporting: preview.mode === "export"
    readonly property string actionLabel: installing ? "安装副本" : exporting ? "导出副本" : "应用译文"
    title: installing ? "安装汉化副本" : exporting ? "导出汉化副本" : "应用译文"
    parent: Basic.Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(508, parent ? parent.width - 24 : 508)
    height: Math.min(implicitHeight, parent ? parent.height - 24 : implicitHeight)
    modal: true
    focus: true
    closePolicy: Basic.Popup.CloseOnEscape
    padding: Theme.spaceXl
    header: null
    footer: null
    background: Rectangle {
        radius: Theme.radiusDialog
        color: Theme.popupFill
        border.color: Theme.controlStroke
    }
    Basic.Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, Theme.dark ? 0.52 : 0.32) }
    onOpened: {
        files.positionViewAtBeginning()
        cancelButton.forceActiveFocus(Qt.TabFocusReason)
    }

    component Label: Text {
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceLg
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceMd
            Rectangle {
                implicitWidth: 42
                implicitHeight: 42
                radius: Theme.radiusCard
                color: Theme.selectionFill
                Label {
                    anchors.centerIn: parent
                    font.family: Theme.iconFamily
                    font.pixelSize: 19
                    text: dialog.installing ? "\uE896" : dialog.exporting ? "\uE8B5" : "\uE70F"
                    Accessible.ignored: true
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs
                Label {
                    Layout.fillWidth: true
                    text: dialog.title
                    font.pixelSize: Theme.fontHeading
                    font.weight: Font.DemiBold
                }
                Label {
                    Layout.fillWidth: true
                    text: dialog.preview.targetName + "  ·  " + dialog.preview.termCount + " 条译文"
                    color: Theme.textSecondary
                    elide: Text.ElideMiddle
                    maximumLineCount: 2
                }
            }
        }
        Label {
            Layout.fillWidth: true
            text: dialog.installing ? "副本将安装到当前 Windows 用户。重启 AE 后，在「窗口 → 扩展」中打开带「简体中文」的面板。"
                  : dialog.exporting ? "将所选译文写入新的 JSX 副本，原脚本保留。"
                  : "将所选译文写入下列文件。重新打开面板或脚本后生效。"
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceSm
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: dialog.exporting ? "输出文件" : "涉及文件"
                    font.weight: Font.DemiBold
                }
                Label {
                    text: dialog.preview.files.length + " 个文件"
                    font.pixelSize: Theme.fontSmall
                    color: Theme.textSecondary
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 32
                Layout.preferredHeight: Math.max(44, Math.min(files.contentHeight, 168) + 12)
                color: Theme.insetFill
                border.color: files.activeFocus ? Theme.focusRing : Theme.border
                radius: Theme.radiusControl
                ListView {
                    id: files
                    objectName: "panelReviewFiles"
                    anchors.fill: parent
                    anchors.margins: 6
                    clip: true
                    activeFocusOnTab: contentHeight > height
                    Accessible.name: "预览文件列表"
                    boundsBehavior: Flickable.StopAtBounds
                    model: dialog.preview.files
                    Basic.ScrollBar.vertical: Basic.ScrollBar {}
                    delegate: Item {
                        required property string modelData
                        width: files.width
                        height: Math.max(30, filePath.implicitHeight + 8)
                        Label {
                            anchors.left: parent.left
                            anchors.leftMargin: Theme.spaceSm
                            anchors.verticalCenter: parent.verticalCenter
                            font.family: Theme.iconFamily
                            text: "\uE8A5"
                            color: Theme.textSecondary
                            Accessible.ignored: true
                        }
                        TextEdit {
                            id: filePath
                            anchors.left: parent.left
                            anchors.leftMargin: 34
                            anchors.right: parent.right
                            anchors.rightMargin: 13
                            anchors.verticalCenter: parent.verticalCenter
                            readOnly: true
                            selectByMouse: true
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            color: Theme.textPrimary
                            textFormat: TextEdit.PlainText
                            wrapMode: TextEdit.WrapAnywhere
                            text: parent.modelData
                        }
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: Theme.spaceSm
            Button {
                id: cancelButton
                objectName: "panelReviewCancel"
                text: "取消"
                onClicked: dialog.reject()
            }
            Button {
                objectName: "panelReviewConfirm"
                primary: true
                text: dialog.actionLabel
                onClicked: dialog.accept()
            }
        }
    }
}
