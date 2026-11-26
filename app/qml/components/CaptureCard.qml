import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import AeLocalizer
import "."

Item {
    id: card
    property var backend: null
    property var capturedRows: []
    property var manualRows: []
    property var rows: []
    property var zhMap: Object.create(null)
    property int seenCount: 0
    property bool extracted: false
    property var diagnosticChannels: []
    property string kindFilter: "all"
    property var activeEditor: null
    property double lastEditAt: 0

    readonly property int panePadding: Theme.spaceMd
    readonly property int kindWidth: 94
    readonly property real englishWidth: (captureList.width - 2 * panePadding - kindWidth) * 0.43
    readonly property var displayedRows: kindFilter === "all" ? rows
                                        : rows.filter(row => rowKind(row) === kindFilter)
    readonly property int filledCount: Object.keys(zhMap).length

    Basic.Dialog {
        id: effectOverrideDialog
        title: "按效果修改下拉译名"
        modal: true
        width: Math.min(600, Math.max(340, card.width - 32))
        x: (card.width - width) / 2
        y: (card.height - height) / 2
        standardButtons: Basic.Dialog.Cancel
        property var options: []
        property string captureMessage: ""
        function refreshOptions() {
            var state = card.backend.effectOptionState(verifiedOptions.checked)
            options = state.options
            captureMessage = state.message
            scopedPicker.currentIndex = -1
            scopedText.text = ""
        }
        onOpened: {
            verifiedOptions.checked = false
            refreshOptions()
        }
        contentItem: ColumnLayout {
            spacing: Theme.spaceSm
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: Theme.textPrimary
                text: effectOverrideDialog.captureMessage
            }
            Basic.CheckBox {
                id: verifiedOptions
                text: "列出已在 AE 18.0.1.1 核验的 Sapphire 条目"
                onClicked: effectOverrideDialog.refreshOptions()
            }
            Basic.ComboBox {
                id: scopedPicker
                Layout.fillWidth: true
                model: effectOverrideDialog.options
                textRole: "label"
                displayText: currentIndex < 0 ? "请选择效果、参数和下拉项" : currentText
                enabled: model.length > 0
                onActivated: scopedText.text = model[currentIndex].zh || ""
            }
            Basic.TextField {
                id: scopedText
                Layout.fillWidth: true
                placeholderText: "输入译名（留空则移除覆盖）"
                maximumLength: 120
                enabled: scopedPicker.currentIndex >= 0
            }
            Button {
                Layout.alignment: Qt.AlignRight
                text: "保存覆盖"
                enabledButton: scopedPicker.currentIndex >= 0
                onClicked: {
                    var choice = scopedPicker.model[scopedPicker.currentIndex]
                    if (card.backend.saveEffectOption(choice.effect, choice.parameter, choice.en, scopedText.text))
                        effectOverrideDialog.close()
                }
            }
        }
    }

    function rowKind(row) {
        if (["enum", "manual", "checkbox", "effect"].includes(row.kind))
            return row.kind
        return "param"
    }

    function uniqueRows(items) {
        var seen = new Set()
        return items.filter(function (row) {
            var key = row.en + ":" + rowKind(row)
            if (seen.has(key))
                return false
            seen.add(key)
            return true
        })
    }

    function recompose() {
        var next = uniqueRows(capturedRows.concat(manualRows))
        if (JSON.stringify(rows) === JSON.stringify(next))
            return
        rows = next
        var translations = Object.create(null)
        for (var row of rows) {
            if (zhMap[row.en])
                translations[row.en] = zhMap[row.en]
        }
        zhMap = translations
    }

    function applyCapture(result) {
        if (!result)
            return
        capturedRows = result.items || []
        seenCount = result.seen || 0
        diagnosticChannels = result.channels || []
        recompose()
    }

    function refreshIfClean() {
        // Quiet refresh pauses during editing and shortly after focus leaves the row.
        if (filledCount > 0 || activeEditor || captureList.moving
            || Date.now() - lastEditAt < 6000)
            return
        applyCapture(backend.captureMissingParamsQuiet())
    }

    function extract() {
        if (!backend)
            return
        applyCapture(backend.captureMissingParams())
        extracted = true
    }

    function applyCatalog() {
        var items = backend.catalogItems
        if (!items || items.length === 0)
            return
        // Snapshot the QVariantList elements before the next catalog result replaces them.
        var snapshot = items.map(row => Object.assign({}, row))
        capturedRows = uniqueRows(capturedRows.concat(snapshot))
        seenCount = backend.catalogSeen
        extracted = true
        recompose()
    }

    function setZh(en, zh) {
        if ((zhMap[en] || "") === zh)
            return
        var translations = Object.assign(Object.create(null), zhMap)
        if (zh)
            translations[en] = zh
        else
            delete translations[en]
        zhMap = translations
    }

    function distribute(startIndex, raw) {
        var lines = raw.split(/\r?\n/).map(line => line.trim()).filter(line => line.length > 0)
        if (lines.length === 0)
            return ""
        var translations = Object.assign(Object.create(null), zhMap)
        var count = Math.min(lines.length, displayedRows.length - startIndex)
        for (var i = 0; i < count; i++)
            translations[displayedRows[startIndex + i].en] = lines[i]
        zhMap = translations
        return lines[0]
    }

    function parsePaste() {
        var newRows = []
        var translations = Object.assign(Object.create(null), zhMap)
        for (var line of pasteEdit.text.split("\n")) {
            var parts = line.trim().split(/(?:→|->|=|\t)/)
            var en = parts[0].trim()
            if (!en)
                continue
            var zh = parts.slice(1).join(" ").trim()
            newRows.push({ en: en, kind: "manual" })
            if (zh)
                translations[en] = zh
        }
        if (newRows.length === 0)
            return
        zhMap = translations
        manualRows = newRows.concat(manualRows)
        recompose()
        if (kindFilter !== "all" && kindFilter !== "manual")
            kindFilter = "all"
        pasteEdit.clear()
    }

    function commit() {
        if (!backend)
            return
        var pairs = Object.keys(zhMap).map(en => ({ en: en, zh: zhMap[en] }))
        if (pairs.length === 0 || backend.commitCapturedWords(pairs) <= 0)
            return
        card.forceActiveFocus()
        var saved = new Set(pairs.map(pair => pair.en))
        manualRows = manualRows.filter(row => !saved.has(row.en))
        zhMap = Object.create(null)
        extract()
    }

    Connections {
        target: card.backend
        function onCatalogChanged() { card.applyCatalog() }
    }

    Timer {
        interval: 4000
        repeat: true
        running: card.visible && card.extracted && card.backend
                 && card.backend.engineRunning && !card.backend.busy && card.filledCount === 0
        onTriggered: card.refreshIfClean()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceMd

        Surface {
            Layout.fillWidth: true
            Layout.preferredHeight: commands.implicitHeight + Theme.spaceSm * 2

            RowLayout {
                id: commands
                anchors.fill: parent
                anchors.margins: Theme.spaceSm
                anchors.leftMargin: card.panePadding
                anchors.rightMargin: card.panePadding
                spacing: Theme.spaceSm

                Button {
                    objectName: "extractCaptureButton"
                    text: "提取"
                    primary: true
                    enabledButton: card.backend && !card.backend.busy
                    onClicked: card.extract()
                }
                Button {
                    objectName: "enumerateCaptureButton"
                    text: "全量枚举"
                    enabledButton: card.backend && !card.backend.busy
                    onClicked: card.backend.captureCatalog()
                }
                Button {
                    objectName: "corpusCaptureButton"
                    text: "语料全量"
                    enabledButton: card.backend && !card.backend.busy
                    onClicked: card.backend.corpusFullDiff()
                }

                Item { Layout.fillWidth: true }

                Button {
                    objectName: "aiFillCaptureButton"
                    text: "AI 补译"
                    enabledButton: card.backend && !card.backend.busy && card.rows.length > 0
                    onClicked: card.backend.aiFillMissing()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceMd

            Surface {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: card.panePadding
                        Layout.rightMargin: card.panePadding
                        Layout.topMargin: Theme.spaceSm
                        Layout.bottomMargin: Theme.spaceXs
                        spacing: Theme.spaceSm

                        Text {
                            Layout.fillWidth: true
                            text: "待补词"
                            color: Theme.textPrimary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontTitle
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: (card.extracted ? "已采集 " + card.seenCount + " 项 · " : "")
                                  + (card.kindFilter === "all" ? card.rows.length + " 条"
                                     : card.displayedRows.length + " / " + card.rows.length + " 条")
                            color: Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                        }
                    }

                    Text {
                        visible: card.diagnosticChannels.length > 0
                        Layout.fillWidth: true
                        Layout.leftMargin: card.panePadding
                        Layout.rightMargin: card.panePadding
                        text: card.diagnosticChannels.join("；")
                        color: Theme.warn
                        wrapMode: Text.WordWrap
                        font.pixelSize: Theme.fontSmall
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: card.panePadding
                        Layout.rightMargin: card.panePadding
                        Layout.bottomMargin: Theme.spaceSm
                        spacing: Theme.spaceXs

                        Rectangle {
                            Layout.preferredWidth: filters.implicitWidth + 4
                            Layout.preferredHeight: filters.implicitHeight + 4
                            radius: Theme.radiusControl
                            color: Theme.insetFill

                            Row {
                                id: filters
                                anchors.centerIn: parent
                                spacing: 2

                                Repeater {
                                    model: [
                                        { key: "all", label: "全部" },
                                        { key: "param", label: "参数" },
                                        { key: "enum", label: "下拉" },
                                        { key: "checkbox", label: "复选框" },
                                        { key: "effect", label: "效果名" },
                                        { key: "manual", label: "手动" }
                                    ]
                                    delegate: Basic.Button {
                                        id: filter
                                        required property var modelData
                                        objectName: "captureFilter_" + modelData.key
                                        text: modelData.label
                                        checkable: true
                                        autoExclusive: true
                                        checked: card.kindFilter === modelData.key
                                        hoverEnabled: true
                                        horizontalPadding: Theme.spaceSm
                                        implicitHeight: 24
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSmall
                                        font.weight: checked ? Font.DemiBold : Font.Normal
                                        onClicked: {
                                            card.kindFilter = modelData.key
                                            captureList.positionViewAtBeginning()
                                        }
                                        contentItem: Text {
                                            text: filter.text
                                            color: filter.checked ? Theme.textPrimary : Theme.textSecondary
                                            font: filter.font
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            radius: Theme.radiusControl - 2
                                            color: filter.checked ? Theme.panelFill
                                                   : filter.hovered ? Theme.bgHover : "transparent"
                                            border.width: filter.visualFocus ? 1 : 0
                                            border.color: Theme.accent
                                        }
                                    }
                                }
                            }
                        }

                        Item { Layout.fillWidth: true }

                        Button {
                            objectName: "copyCaptureButton"
                            text: "复制英文清单"
                            quiet: true
                            enabledButton: card.backend && card.rows.length > 0
                            onClicked: card.backend.copyCapturedEnglish(card.rows.map(row => row.en))
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Theme.border
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 28
                        color: Theme.insetFill

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: card.panePadding
                            anchors.rightMargin: card.panePadding
                            spacing: Theme.spaceSm

                            Text {
                                Layout.preferredWidth: card.kindWidth
                                text: "类型"
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                            }
                            Text {
                                Layout.preferredWidth: card.englishWidth
                                text: "英文原文"
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                            }
                            Text {
                                Layout.fillWidth: true
                                text: "中文译名"
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        ListView {
                            id: captureList
                            objectName: "captureList"
                            anchors.fill: parent
                            clip: true
                            model: card.displayedRows
                            boundsBehavior: Flickable.StopAtBounds
                            Basic.ScrollBar.vertical: Basic.ScrollBar { policy: Basic.ScrollBar.AsNeeded }

                            delegate: Item {
                                id: entry
                                required property var modelData
                                required property int index
                                width: ListView.view.width
                                height: 35

                                Rectangle {
                                    anchors.fill: parent
                                    color: zhInput.activeFocus || sourceName.activeFocus
                                           ? Theme.selectionFill
                                           : rowHover.hovered ? Theme.bgHover : "transparent"
                                }
                                HoverHandler { id: rowHover }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: card.panePadding
                                    anchors.rightMargin: card.panePadding
                                    spacing: Theme.spaceSm

                                    Text {
                                        Layout.preferredWidth: card.kindWidth
                                        property string kind: card.rowKind(entry.modelData)
                                        property string kindText: ({enum:"下拉", checkbox:"复选框", effect:"效果名", manual:"手动"})[kind] || "参数"
                                        text: kindText + (entry.modelData.stuck ? " · 未生效"
                                              : entry.modelData.same ? " · 未译"
                                              : entry.modelData.junk ? " · 待确认"
                                              : kind === "manual" ? "" : " · 未收录")
                                        color: entry.modelData.same || entry.modelData.stuck ? Theme.warn
                                               : Theme.textSecondary
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSmall
                                    }

                                    TextEdit {
                                        id: sourceName
                                        Layout.preferredWidth: card.englishWidth
                                        Layout.fillHeight: true
                                        text: entry.modelData.en
                                        readOnly: true
                                        selectByMouse: true
                                        wrapMode: TextEdit.NoWrap
                                        clip: true
                                        color: Theme.textPrimary
                                        font.family: Theme.monoFamily
                                        font.pixelSize: Theme.fontSmall
                                        selectionColor: Theme.accent
                                        selectedTextColor: "#FFFFFF"
                                        verticalAlignment: TextEdit.AlignVCenter
                                    }

                                    Basic.TextField {
                                        id: zhInput
                                        objectName: "captureTranslationInput"
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        padding: 0
                                        background: null
                                        color: Theme.textPrimary
                                        placeholderTextColor: Theme.textSecondary
                                        placeholderText: entry.modelData.stuck
                                                         ? (entry.modelData.why === "capacity" ? "译文超出显示容量" : "已有译文，当前未观察到生效")
                                                         : "输入译名"
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontBody
                                        selectByMouse: true
                                        clip: true
                                        Accessible.name: entry.modelData.en + "的中文译名"
                                        Keys.onReturnPressed: focus = false
                                        Keys.onEnterPressed: focus = false

                                        Binding on text {
                                            when: !zhInput.activeFocus
                                            value: card.zhMap[entry.modelData.en] || ""
                                            restoreMode: Binding.RestoreNone
                                        }
                                        onActiveFocusChanged: {
                                            if (activeFocus)
                                                card.activeEditor = zhInput
                                            else if (card.activeEditor === zhInput)
                                                card.activeEditor = null
                                        }
                                        onTextEdited: {
                                            card.lastEditAt = Date.now()
                                            if (text.indexOf("\n") >= 0)
                                                text = card.distribute(entry.index, text)
                                            else
                                                card.setZh(entry.modelData.en, text)
                                        }
                                    }
                                }

                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.bottom: parent.bottom
                                    anchors.leftMargin: card.panePadding
                                    anchors.rightMargin: card.panePadding
                                    height: 1
                                    color: Theme.borderSoft
                                }
                            }
                        }

                        Column {
                            anchors.centerIn: parent
                            width: parent.width - 2 * card.panePadding
                            spacing: Theme.spaceXs
                            visible: captureList.count === 0

                            Text {
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                text: card.rows.length > 0 ? "此类暂无待补词"
                                      : !card.extracted ? "还未提取缺词"
                                      : card.seenCount > 0 ? "本次采集未发现待补词"
                                      : "暂未采集到参数"
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                font.weight: Font.DemiBold
                            }
                            Text {
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.Wrap
                                visible: card.rows.length === 0
                                text: !card.extracted
                                      ? "在 AE 中展开效果参数，再点「提取」。"
                                      : card.seenCount === 0
                                        ? "打开 AE 并展开效果参数，引擎采集后会自动刷新。"
                                        : "若 AE 仍显示英文，可手动添加，或重启引擎后重新提取。"
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                            }
                        }
                    }
                }
            }

            Surface {
                Layout.preferredWidth: Math.max(170, Math.min(272, card.width * 0.29))
                Layout.fillHeight: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: card.panePadding
                    spacing: Theme.spaceSm

                    Text {
                        Layout.fillWidth: true
                        text: "手动添加"
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontTitle
                        font.weight: Font.DemiBold
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 42
                        radius: Theme.radiusControl
                        color: Theme.insetFill
                        border.width: 1
                        border.color: pasteEdit.activeFocus ? Theme.accent : Theme.border
                        clip: true

                        Basic.ScrollView {
                            anchors.fill: parent
                            anchors.margins: 1
                            contentWidth: availableWidth
                            Basic.ScrollBar.horizontal.policy: Basic.ScrollBar.AlwaysOff
                            Basic.ScrollBar.vertical.policy: Basic.ScrollBar.AsNeeded

                            Basic.TextArea {
                                id: pasteEdit
                                objectName: "capturePasteInput"
                                padding: Theme.spaceSm
                                placeholderText: "英文 → 中文"
                                placeholderTextColor: Theme.textSecondary
                                wrapMode: TextEdit.WrapAnywhere
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                selectByMouse: true
                                background: null
                                Accessible.name: "手动补词"
                            }
                        }
                    }
                    Button {
                        Layout.fillWidth: true
                        text: "按效果改下拉译名"
                        quiet: true
                        enabledButton: !!card.backend
                        onClicked: effectOverrideDialog.open()
                    }
                    Button {
                        objectName: "addCaptureButton"
                        Layout.fillWidth: true
                        text: "解析并添加"
                        enabledButton: pasteEdit.text.trim().length > 0
                        onClicked: card.parsePaste()
                    }
                }
            }
        }

        Surface {
            Layout.fillWidth: true
            Layout.preferredHeight: saveBar.implicitHeight + Theme.spaceSm * 2

            RowLayout {
                id: saveBar
                anchors.fill: parent
                anchors.margins: Theme.spaceSm
                anchors.leftMargin: card.panePadding
                anchors.rightMargin: card.panePadding
                spacing: Theme.spaceMd

                Item { Layout.fillWidth: true }
                Button {
                    objectName: "commitCaptureButton"
                    text: "记入词典（" + card.filledCount + "）"
                    primary: true
                    enabledButton: card.backend && card.filledCount > 0
                    onClicked: card.commit()
                }
            }
        }
    }
}
