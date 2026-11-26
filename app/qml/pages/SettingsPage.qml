import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import QtQuick.Controls.Basic as Basic
import QtQuick.Dialogs
import AeLocalizer
import "../components"

Item {
    id: root
    property var backend: null
    property int section: 0

    readonly property var sections: ["外观", "常规", "数据", "更新", "诊断", "关于"]

    readonly property var themeOptions: [
        { key: "system", label: "跟随系统" },
        { key: "light",  label: "浅色" },
        { key: "dark",   label: "深色" }
    ]
    readonly property var accentOptions: [
        { key: "blue",   label: "群青" },
        { key: "violet", label: "紫罗兰" },
        { key: "teal",   label: "青碧" },
        { key: "amber",  label: "琥珀" },
        { key: "rose",   label: "玫瑰" },
        { key: "slate",  label: "石板" }
    ]
    readonly property var densityOptions: [
        { key: "compact", label: "紧凑" },
        { key: "normal",  label: "标准" },
        { key: "relaxed", label: "宽松" }
    ]
    readonly property var fontOptions: [
        { key: "small",  label: "小" },
        { key: "normal", label: "标准" },
        { key: "large",  label: "大" }
    ]

    function applyTextColor(v) {
        if (!backend) return
        backend.setTextColor(v)
        Theme.textCustom = backend.textColor !== "" ? backend.textColor : "transparent"
        colorInput.text = backend.textColor
    }

    function filterFonts(kw) {
        if (!backend) return []
        var all = backend.systemFonts()
        var q = String(kw || "").trim().toLowerCase()
        if (q === "") return all
        var out = []
        for (var i = 0; i < all.length; i++) {
            if (String(all[i]).toLowerCase().indexOf(q) >= 0) out.push(all[i])
        }
        return out
    }

    FileDialog {
        id: exportDialog
        title: "导出词典备份"
        fileMode: FileDialog.SaveFile
        nameFilters: ["词典包 (*.aeloc)"]
        onAccepted: if (backend) backend.exportDict(selectedFile.toString())
    }
    FileDialog {
        id: importDialog
        title: "导入词典包"
        nameFilters: ["词典包 (*.json *.aeloc)", "所有文件 (*)"]
        onAccepted: if (backend) backend.importPack(selectedFile.toString().replace("file:///", ""))
    }

    ColorDialog {
        id: textColorDialog
        title: "选择文字颜色"
        selectedColor: root.backend && root.backend.textColor !== "" ? root.backend.textColor : "#ffffff"
        onAccepted: root.applyTextColor(selectedColor.toString())
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceMd

        Surface {
            Layout.fillWidth: true
            Layout.preferredHeight: sectionTabs.implicitHeight + Theme.spaceXs * 2
            color: Theme.insetFill

            RowLayout {
                id: sectionTabs
                anchors.fill: parent
                anchors.margins: Theme.spaceXs
                spacing: Theme.spaceXs

                Repeater {
                    model: root.sections
                    delegate: Basic.Button {
                        id: tab
                        required property int index
                        required property string modelData
                        readonly property bool selected: root.section === index

                        objectName: "settingsTab" + index
                        Layout.fillWidth: true
                        Layout.preferredWidth: 64
                        implicitHeight: 34
                        hoverEnabled: true
                        text: modelData
                        Accessible.name: modelData
                        onClicked: root.section = index

                        contentItem: Text {
                            text: tab.text
                            color: tab.selected ? Theme.textPrimary : Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontBody
                            font.weight: tab.selected ? Font.DemiBold : Font.Normal
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        background: Rectangle {
                            radius: Theme.radiusControl
                            color: tab.selected ? Theme.selectedControlFill
                                   : (tab.hovered ? Theme.bgHover : "transparent")
                            border.width: tab.visualFocus ? 2 : tab.selected ? 1 : 0
                            border.color: tab.visualFocus ? Theme.focusRing : Theme.border
                        }
                    }
                }

                Item { Layout.fillWidth: true; Layout.preferredWidth: Math.max(0, root.width - 620) }
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.section

            SectionedPage {

                WallpaperSettings { backend: root.backend }

                SettingCard {
                    title: "主题与强调色"

                    SettingRow {
                        title: "配色模式"
                        Segmented {
                            current: root.backend ? root.backend.uiTheme : "light"
                            options: root.themeOptions
                            onPicked: function(key) {
                                if (!root.backend) return
                                root.backend.setUiTheme(key)
                                Theme.themeMode = key
                            }
                        }
                    }

                    SettingRow {
                        title: "强调色"
                        Segmented {
                            current: root.backend ? root.backend.accentKey : "blue"
                            options: root.accentOptions
                            onPicked: function(key) {
                                if (!root.backend) return
                                root.backend.setAccentKey(key)
                                Theme.accentKey = key
                            }
                        }
                    }
                }

                SettingCard {
                    title: "排版"

                    SettingRow {
                        title: "界面密度"
                        Segmented {
                            current: root.backend ? root.backend.density : "normal"
                            options: root.densityOptions
                            onPicked: function(key) {
                                if (!root.backend) return
                                root.backend.setDensity(key)
                                Theme.density = key
                            }
                        }
                    }

                    SettingRow {
                        title: "字号"
                        Segmented {
                            current: root.backend ? root.backend.fontScale : "normal"
                            options: root.fontOptions
                            onPicked: function(key) {
                                if (!root.backend) return
                                root.backend.setFontScale(key)
                                Theme.fontScale = key
                            }
                        }
                    }

                    SettingRow {
                        title: "字体"

                        Rectangle {
                            id: fontBox
                            Layout.preferredWidth: 260
                            implicitHeight: 30
                            radius: Theme.radiusControl
                            color: Theme.fieldFill
                            border.width: 1
                            border.color: fontPop.visible ? Theme.accent : Theme.border

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.leftMargin: Theme.spaceMd
                                anchors.right: parent.right
                                anchors.rightMargin: Theme.spaceXl
                                text: root.backend && root.backend.uiFont !== ""
                                      ? root.backend.uiFont : Theme.fontDefault + "（默认）"
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                                elide: Text.ElideRight
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.right: parent.right
                                anchors.rightMargin: Theme.spaceMd
                                text: "▾"
                                color: Theme.textTertiary
                                font.pixelSize: 11
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: fontPop.visible = !fontPop.visible
                            }

                            Popup {
                                id: fontPop
                                y: fontBox.height + 4
                                width: fontBox.width
                                height: 320
                                padding: 0
                                z: 1000

                                background: Rectangle {
                                    color: Theme.bgElevatedGlass
                                    radius: Theme.radiusControl
                                    border.width: 1
                                    border.color: Theme.border
                                }

                                contentItem: ColumnLayout {
                                    spacing: 0
                                    Layout.fillWidth: true

                                    TextInput {
                                        id: fontSearch
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 28
                                        Layout.leftMargin: Theme.spaceMd
                                        Layout.rightMargin: Theme.spaceMd
                                        verticalAlignment: TextInput.AlignVCenter
                                        color: Theme.textPrimary
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSmall
                                        clip: true
                                        Text {
                                            anchors.verticalCenter: parent.verticalCenter
                                            visible: fontSearch.text.length === 0
                                            text: "搜索字体…"
                                            color: Theme.textTertiary
                                            font.family: Theme.fontFamily
                                            font.pixelSize: Theme.fontSmall
                                        }
                                    }
                                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

                                    ListView {
                                        id: fontList
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        clip: true
                                        boundsBehavior: Flickable.StopAtBounds
                                        model: root.filterFonts(fontSearch.text)
                                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                                        delegate: Rectangle {
                                            required property string modelData
                                            width: fontList.width
                                            height: 28
                                            color: fontRow.containsMouse ? Theme.bgHover : "transparent"
                                            Text {
                                                anchors.fill: parent
                                                anchors.leftMargin: Theme.spaceMd
                                                anchors.rightMargin: Theme.spaceMd
                                                verticalAlignment: Text.AlignVCenter
                                                text: parent.modelData
                                                color: Theme.textPrimary
                                                font.family: parent.modelData
                                                font.pixelSize: Theme.fontSmall
                                                elide: Text.ElideRight
                                            }
                                            MouseArea {
                                                id: fontRow
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    if (root.backend) root.backend.setUiFont(parent.modelData)
                                                    Theme.uiFont = parent.modelData
                                                    fontPop.close()
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Button {
                            text: "默认"
                            visible: root.backend && root.backend.uiFont !== ""
                            onClicked: {
                                if (!root.backend) return
                                root.backend.setUiFont("")
                                Theme.uiFont = ""
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    SettingRow {
                        title: "文字颜色"
                        visible: root.backend && Theme.hasAnyBg

                        Segmented {
                            current: root.backend && root.backend.textColor !== "" ? "custom" : "auto"
                            options: [
                                { key: "auto",   label: "跟随主题" },
                                { key: "light",  label: "浅字" },
                                { key: "dark",   label: "深字" }
                            ]
                            onPicked: function(key) {
                                if (!root.backend) return
                                if (key === "light") root.applyTextColor("#f2f5fa")
                                else if (key === "dark") root.applyTextColor("#10151d")
                                else root.applyTextColor("")
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    SettingRow {
                        title: "调色板"
                        visible: root.backend && Theme.hasAnyBg

                        Repeater {
                            model: ["#ffffff", "#f2f5fa", "#10151d", "#1f2a37",
                                    "#ffd166", "#7cffcb", "#8fb8ff", "#ff9fbe"]

                            delegate: Rectangle {
                                required property string modelData
                                implicitWidth: 22
                                implicitHeight: 22
                                radius: 11
                                color: modelData
                                border.width: root.backend && root.backend.textColor.toLowerCase() === modelData
                                              ? 2 : 1
                                border.color: (root.backend && root.backend.textColor.toLowerCase() === modelData)
                                              ? Theme.accent : Theme.border
                                Rectangle {
                                    anchors.fill: parent
                                    anchors.margins: 1
                                    radius: parent.radius - 1
                                    color: "transparent"
                                    border.width: modelData.toLowerCase() === "#ffffff" ? 1 : 0
                                    border.color: Theme.borderSoft
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.applyTextColor(modelData)
                                }
                            }
                        }

                        Button {
                            text: "取色"
                            onClicked: textColorDialog.open()
                        }
                        Item { Layout.fillWidth: true }
                    }

                    SettingRow {
                        title: "色值"
                        visible: root.backend && Theme.hasAnyBg

                        Rectangle {
                            Layout.preferredWidth: 120
                            implicitHeight: 30
                            radius: Theme.radiusControl
                            color: Theme.fieldFill
                            border.width: 1
                            border.color: colorInput.activeFocus ? Theme.accent : Theme.border

                            TextInput {
                                id: colorInput
                                anchors.fill: parent
                                anchors.leftMargin: Theme.spaceMd
                                anchors.rightMargin: Theme.spaceMd
                                verticalAlignment: TextInput.AlignVCenter
                                color: Theme.textPrimary
                                font.family: Theme.monoFamily
                                font.pixelSize: Theme.fontSmall
                                selectByMouse: true
                                clip: true
                                text: root.backend ? root.backend.textColor : ""
                                onEditingFinished: root.applyTextColor(text)
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: colorInput.text.length === 0
                                    text: "跟随主题"
                                    color: Theme.textTertiary
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSmall
                                }
                            }
                        }
                        Rectangle {
                            Layout.preferredWidth: 30
                            Layout.preferredHeight: 30
                            radius: Theme.radiusControl
                            border.width: 1
                            border.color: Theme.border
                            color: Theme.hasCustomText ? Theme.textPrimary : "transparent"
                        }
                        Item { Layout.fillWidth: true }
                    }

                    SettingRow {
                        title: "减少动效"

                        Toggle {
                            label: root.backend && root.backend.reduceMotion ? "已开启" : "已关闭"
                            checked: root.backend ? root.backend.reduceMotion : false
                            onToggled: function(nextState) {
                                if (!root.backend) return
                                root.backend.setReduceMotion(nextState)
                                Theme.reduceMotion = nextState
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                Button {
                    text: "恢复默认外观"
                    Layout.alignment: Qt.AlignRight
                    onClicked: if (root.backend) root.backend.resetAppearance()
                }
            }

            SectionedPage {

                SettingCard {
                    title: "启动"

                    SettingRow {
                        title: "启动时自动扫描"

                        Toggle {
                            label: root.backend && root.backend.autoScan ? "已开启" : "已关闭"
                            checked: root.backend ? root.backend.autoScan : true
                            onToggled: function(nextState) {
                                if (root.backend) root.backend.setAutoScan(nextState)
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    SettingRow {
                        title: "检查更新"

                        Text {
                            Layout.fillWidth: true
                            text: "手动触发 —— 入口在「更新」分区"
                            color: Theme.textTertiary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            SectionedPage {

                SettingCard {
                    title: "数据目录"

                    SettingBlock {
                        title: "位置"

                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 30
                            radius: Theme.radiusControl
                            color: Theme.fieldFill
                            border.width: 1
                            border.color: Theme.border

                            Text {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.spaceMd
                                anchors.rightMargin: Theme.spaceMd
                                verticalAlignment: Text.AlignVCenter
                                text: root.backend ? root.backend.dataDirText : ""
                                color: Theme.textSecondary
                                font.family: Theme.monoFamily
                                font.pixelSize: 12
                                elide: Text.ElideMiddle
                            }
                        }
                    }

                    SettingRow {
                        title: "占用"

                        Text {
                            id: usageText
                            Layout.fillWidth: true
                            text: root.backend ? root.backend.dataDirUsage() : "—"
                            color: Theme.textSecondary
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fontSmall
                        }
                        Button {
                            text: "打开目录"
                            onClicked: if (root.backend) root.backend.openDataDir()
                        }
                        Button {
                            text: "刷新"
                            onClicked: usageText.text = root.backend ? root.backend.dataDirUsage() : "—"
                        }
                    }

                    Component.onCompleted: if (root.backend) usageText.text = root.backend.dataDirUsage()
                }

                SettingCard {
                    title: "词典"

                    SettingRow {
                        title: "备份与恢复"

                        Button {
                            text: "导出词典"
                            onClicked: exportDialog.open()
                        }
                        Button {
                            text: "导入词典包"
                            onClicked: importDialog.open()
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                SettingCard {
                    title: "缓存"

                    SettingRow {
                        title: "翻译缓存"

                        Text {
                            Layout.fillWidth: true
                            text: (root.backend ? root.backend.aiCacheEntries : 0) + " 条"
                            color: Theme.textSecondary
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fontSmall
                        }
                        Button {
                            text: "清空缓存"
                            danger: true
                            enabledButton: root.backend && root.backend.aiCacheEntries > 0
                            onClicked: {
                                if (!root.backend) return
                                root.backend.clearAiCache()
                            }
                        }
                    }
                }
            }

            UpdateSection {
                backend: root.backend
            }

            SectionedPage {

                SettingCard {
                    title: "运行信息"

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Math.min(260, diagText.contentHeight + Theme.spaceMd * 2)
                        radius: Theme.radiusControl
                        color: Theme.fieldFill
                        border.width: 1
                        border.color: Theme.borderSoft
                        clip: true

                        Flickable {
                            anchors.fill: parent
                            anchors.margins: Theme.spaceSm
                            contentWidth: width
                            contentHeight: diagText.height
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                            TextEdit {
                                id: diagText
                                width: parent.width
                                text: root.backend ? root.backend.diagnosticText() : ""
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.WrapAtWordBoundaryOrAnywhere
                                color: Theme.textSecondary
                                font.family: Theme.monoFamily
                                font.pixelSize: 12
                                selectionColor: Theme.accent
                                selectedTextColor: "#FFFFFF"
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceSm
                        Button {
                            text: "复制诊断信息"
                            primary: true
                            onClicked: {
                                if (!root.backend) return
                                diagText.text = root.backend.copyDiagnostic()
                            }
                        }
                        Button {
                            text: "打开引擎日志"
                            onClicked: if (root.backend) root.backend.openEngineLog()
                        }
                        Button {
                            text: "刷新"
                            onClicked: diagText.text = root.backend ? root.backend.diagnosticText() : ""
                        }
                        Item { Layout.fillWidth: true }
                    }
                }
            }

            SectionedPage {

                SettingCard {
                    title: "AeLocalizer · 映"


                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

                    SettingRow {
                        title: "版本"
                        Text {
                            text: "v" + (root.backend ? root.backend.appVersion : "?")
                            color: Theme.textPrimary
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fontBody
                        }
                        Item { Layout.fillWidth: true }
                    }

                    SettingRow {
                        title: "运行时"
                        Text {
                            text: "PySide6 6.12 · Qt 6.12 · Frida 17.26.3（项目私有）"
                            color: Theme.textSecondary
                            font.family: Theme.monoFamily
                            font.pixelSize: Theme.fontSmall
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }

                    SettingRow {
                        title: "许可"
                        Text {
                            text: "词典数据与程序分离，词典包可二次分发"
                            color: Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
    }
}
