import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import AeLocalizer

ColumnLayout {
    id: root
    property var backend: null
    property string tip: ""
    property bool addMode: false
    property bool suppressDictReload: false

    readonly property int tableMargin: Theme.spaceMd + 4
    readonly property int englishWidth: Math.round(Math.min(330, width * 0.31))
    readonly property int layerWidth: Math.max(60, Theme.fontSmall * 5)
    readonly property int gaugeWidth: Math.round(Math.min(200, width * 0.25))

    spacing: 0

    component EntryInput: Basic.TextField {
        id: field
        Layout.fillWidth: true
        Layout.preferredHeight: 34
        leftPadding: Theme.spaceMd
        rightPadding: Theme.spaceMd
        color: Theme.textPrimary
        placeholderTextColor: Theme.textSecondary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        selectByMouse: true

        background: Rectangle {
            radius: Theme.radiusControl
            color: Theme.insetFill
            border.width: field.activeFocus ? 1 : 0
            border.color: Theme.accent
        }
    }

    component FileAction: Basic.MenuItem {
        id: action
        implicitHeight: 34
        leftPadding: Theme.spaceMd
        rightPadding: Theme.spaceMd

        contentItem: Text {
            text: action.text
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: Theme.radiusControl
            color: action.highlighted ? Theme.selectionFill : "transparent"
        }
    }

    function reload() {
        if (!backend) return
        var status = backend.refreshDict()
        if (status.startsWith("载入词典失败")) tip = status
    }

    function addEntry() {
        if (!backend) return
        tip = backend.addDictEntry(newEnInput.text, newZhInput.text)
        if (tip.startsWith("已保存")) {
            newEnInput.clear()
            newZhInput.clear()
            newEnInput.forceActiveFocus()
        }
    }

    FileDialog {
        id: effectsDialog
        title: "选择效果名词典（JSON）"
        nameFilters: ["JSON 文件 (*.json)", "所有文件 (*)"]
        onAccepted: if (root.backend)
                        root.backend.pickEffectsFileResult(selectedFile.toString().replace("file:///", ""))
    }

    FileDialog {
        id: importDialog
        title: "选择词典包（.json / .aeloc）"
        nameFilters: ["词典包 (*.json *.aeloc)", "所有文件 (*)"]
        onAccepted: if (root.backend)
                        root.backend.importPack(selectedFile.toString().replace("file:///", ""))
    }

    FileDialog {
        id: exportDialog
        title: "导出生效词典（.aeloc 词典包）"
        fileMode: FileDialog.SaveFile
        nameFilters: ["词典包 (*.aeloc)"]
        onAccepted: if (root.backend)
                        root.tip = root.backend.exportDict(selectedFile.toString().replace("file:///", ""))
    }

    Connections {
        target: root.backend
        function onEffectsPickRequested() { effectsDialog.open() }
        function onDictChanged() {
            if (root.visible && !root.suppressDictReload) root.reload()
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: root.tableMargin
        Layout.rightMargin: root.tableMargin
        Layout.topMargin: Theme.spaceSm
        Layout.bottomMargin: Theme.spaceSm
        spacing: Theme.spaceSm

        EntryInput {
            id: dictSearch
            objectName: "dictSearch"
            Layout.minimumWidth: 100
            Layout.maximumWidth: 324
            leftPadding: 32
            placeholderText: "搜索参数名或下拉值"
            onTextChanged: if (root.backend) root.backend.filterDict(text)

            Text {
                anchors.left: parent.left
                anchors.leftMargin: Theme.spaceMd
                anchors.verticalCenter: parent.verticalCenter
                text: "\uE721"
                color: Theme.textSecondary
                font.family: Theme.iconFamily
                font.pixelSize: 13
            }
        }

        Item { Layout.fillWidth: true }

        Button {
            id: fileButton
            objectName: "dictionaryFileActions"
            text: "词典文件  ▾"
            onClicked: fileMenu.open()

            Basic.Menu {
                id: fileMenu
                objectName: "dictionaryFileMenu"
                y: fileButton.height + Theme.spaceXs
                x: fileButton.width - width
                width: Math.max(fileButton.width, 156)
                padding: Theme.spaceXs

                background: Rectangle {
                    radius: Theme.radiusControl
                    color: Theme.popupFill
                    border.width: 1
                    border.color: Theme.controlStroke
                }

                FileAction {
                    text: "载入效果名表"
                    onTriggered: if (root.backend) root.backend.pickEffectsFile()
                }
                FileAction {
                    text: "导入词典包"
                    onTriggered: importDialog.open()
                }
                FileAction {
                    text: "导出词典"
                    onTriggered: exportDialog.open()
                }
            }
        }

        Button {
            objectName: "addDictionaryEntry"
            text: root.addMode ? "收起新增" : "新增词条"
            primary: !root.addMode
            onClicked: {
                root.addMode = !root.addMode
                if (root.addMode) newEnInput.forceActiveFocus()
            }
        }
    }

    RowLayout {
        visible: root.addMode
        Layout.fillWidth: true
        Layout.leftMargin: root.tableMargin
        Layout.rightMargin: root.tableMargin
        Layout.bottomMargin: Theme.spaceSm
        spacing: Theme.spaceSm

        EntryInput {
            id: newEnInput
            objectName: "newDictionaryEnglish"
            placeholderText: "英文原名"
            onAccepted: newZhInput.forceActiveFocus()
        }

        EntryInput {
            id: newZhInput
            objectName: "newDictionaryTranslation"
            placeholderText: "中文译文"
            onAccepted: root.addEntry()
        }

        Button {
            text: "保存词条"
            primary: true
            onClicked: root.addEntry()
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 30
        color: Theme.insetFill

        MouseArea {
            anchors.fill: parent
            onPressed: root.forceActiveFocus()
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: root.tableMargin
            anchors.rightMargin: root.tableMargin
            spacing: Theme.spaceMd

            Text {
                Layout.preferredWidth: root.englishWidth
                text: "英文原名"
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: "中文译文"
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
            }
            Text {
                Layout.preferredWidth: root.layerWidth
                text: "来源"
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
            }
            Text {
                Layout.preferredWidth: root.gaugeWidth
                text: "运行时可写性"
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
            }
        }
    }

    ListView {
        id: dictList
        objectName: "dictList"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        model: root.backend ? root.backend.dictModel : null
        boundsBehavior: Flickable.StopAtBounds
        Basic.ScrollBar.vertical: Basic.ScrollBar {}

        MouseArea {
            anchors.fill: parent
            z: -1
            onPressed: root.forceActiveFocus()
        }

        Text {
            anchors.centerIn: parent
            visible: dictList.count === 0
            text: dictSearch.text.length > 0 ? "没有匹配的词条" : "词典暂无可编辑词条"
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
        }

        delegate: Item {
            width: dictList.width
            height: Theme.roomy ? 34 : 30

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.leftMargin: root.tableMargin
                anchors.rightMargin: root.tableMargin
                height: 1
                color: Theme.borderSoft
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: root.tableMargin
                anchors.rightMargin: root.tableMargin
                spacing: Theme.spaceMd

                Text {
                    Layout.preferredWidth: root.englishWidth
                    text: model.en
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    elide: Text.ElideRight
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    TextInput {
                        id: zhInput
                        objectName: "dictZhInput"
                        anchors.fill: parent
                        verticalAlignment: TextInput.AlignVCenter
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                        selectByMouse: true
                        selectionColor: Theme.accent
                        selectedTextColor: "#FFFFFF"
                        clip: true
                        text: model.zh

                        onEditingFinished: {
                            if (!root.backend || text === model.zh) return
                            // 保存会同步发出 dictChanged，单行已更新，无需重建列表。
                            root.suppressDictReload = true
                            root.tip = root.backend.saveDictEntry(model.en, text)
                            root.suppressDictReload = false
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: zhInput.text.length === 0 && !zhInput.activeFocus
                            text: "无译文"
                            color: Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                        }
                    }

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 2
                        visible: zhInput.activeFocus
                        color: Theme.accent
                    }
                }

                Text {
                    Layout.preferredWidth: root.layerWidth
                    text: model.layerLabel
                    color: model.layer === "user_locked" ? Theme.warn
                         : model.layer === "user" ? Theme.success
                         : Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    elide: Text.ElideRight
                }

                Text {
                    id: gaugeText
                    Layout.preferredWidth: root.gaugeWidth
                    text: model.gauge
                    color: Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    elide: Text.ElideRight
                    Basic.ToolTip.visible: gaugeHover.hovered && truncated
                    Basic.ToolTip.text: text

                    HoverHandler { id: gaugeHover }
                }
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.borderSoft
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.leftMargin: root.tableMargin
        Layout.rightMargin: root.tableMargin
        Layout.preferredHeight: 31
        spacing: Theme.spaceMd

        Text {
            Layout.fillWidth: true
            text: root.tip
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
        }

        Text {
            text: "显示 " + dictList.count + " 条"
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
        }
    }
}
