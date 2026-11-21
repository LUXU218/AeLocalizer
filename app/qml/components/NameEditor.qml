import QtQuick
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Window
import AeLocalizer

Item {
    id: root
    objectName: "nameWorkspace"
    property var backend: null
    property string statsText: ""
    property string tip: ""
    property var writePreview: null
    property int writeSelectionCount: 0
    property var vendorItems: []
    property string vendorFilter: ""
    property var suiteCounts: ({})
    property bool onlyTranslated: false

    // 边缘滚动会回收行委托，拖选锚点由编辑器持有。
    property bool dragPressed: false
    property bool dragActive: false
    property int dragAnchorIdx: -1
    property int dragLastIdx: -1
    property int dragScrollDir: 0
    property int dragTicks: 0
    property string selColumn: "en"

    readonly property int tableMargin: Theme.spaceMd + 4
    readonly property int colEnW: Math.round(Math.min(370, workspace.width * 0.44))
    readonly property int colStatusW: Math.max(104, Theme.fontSmall * 8)
    readonly property int colZhStart: tableMargin + colEnW + Theme.spaceMd
    readonly property int selectionCount: backend ? backend.nameSelectedCount : 0
    readonly property real statusStart: workspace.width - tableMargin - colStatusW

    component Tab: Basic.TabButton {
        id: tab
        implicitWidth: tabLabel.implicitWidth + Theme.spaceLg * 2
        implicitHeight: 36
        hoverEnabled: true

        contentItem: Text {
            id: tabLabel
            text: tab.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: tab.checked ? Theme.textPrimary : Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            font.weight: tab.checked ? Font.DemiBold : Font.Normal
        }

        background: Rectangle {
            radius: Theme.radiusControl
            color: tab.checked ? Theme.selectionFill : tab.hovered ? Theme.bgHover : "transparent"
            border.width: tab.visualFocus ? 1 : 0
            border.color: Theme.accent
        }
    }

    function finishDrag() {
        dragPressed = false
        dragActive = false
        dragAnchorIdx = -1
        dragLastIdx = -1
        dragScrollDir = 0
        dragTicks = 0
    }

    function clearSelection() {
        finishDrag()
        forceActiveFocus()
        if (backend) backend.clearNameSelection()
    }

    // 按实际坐标查找行，跨过套装分组标题。
    function rowIdxAt(viewportX, viewportY) {
        var contentY = viewportY + list.contentY
        var offsets = [0, -3, 3, -8, 8, -16, 16]
        for (var i = 0; i < offsets.length; i++) {
            var item = list.contentItem.childAt(viewportX, contentY + offsets[i])
            for (var depth = 0; item && item.rowIndex === undefined && depth < 4; depth++) {
                item = list.contentItem.childAt(viewportX, item.y + item.height + 2)
            }
            if (item && item.rowIndex !== undefined) return item.rowIndex
        }
        return -1
    }

    function ensureCursorVisible(flick, edit) {
        var cursor = edit.cursorRectangle
        var top = edit.y + cursor.y
        var bottom = top + cursor.height
        if (top < flick.contentY + 2) {
            flick.contentY = Math.max(0, top - 4)
        } else if (bottom > flick.contentY + flick.height - 2) {
            var maxY = Math.max(0, flick.contentHeight - flick.height)
            flick.contentY = Math.min(maxY, bottom - flick.height + 4)
        }
    }

    readonly property bool textFieldFocused: {
        var window = root.Window.window
        if (!window || !window.activeFocusItem) return false
        var item = String(window.activeFocusItem)
        return item.includes("TextInput") || item.includes("TextEdit") || item.includes("TextArea")
    }

    Shortcut {
        sequence: "Ctrl+C"
        enabled: root.visible && tabs.currentIndex === 0 && !writePop.opened
                 && root.selectionCount > 0 && !root.textFieldFocused
        onActivated: {
            if (root.selColumn === "zh") root.backend.copySelectedTranslations()
            else root.backend.copySelectedNames()
        }
    }

    function reload() {
        refreshNames()
        if (tabs.currentIndex === 1) dictPane.reload()
    }

    function refreshNames() {
        if (!backend) return
        tip = backend.refreshNames()
        statsText = backend.nameStats()
        var options = [{value: "", label: "全部插件"}]
        var vendors = backend.nameVendors()
        for (var i = 0; i < vendors.length; i++) {
            var vendor = vendors[i]
            options.push({value: vendor.value, label: vendor.value + "  ·  " + vendor.count})
        }
        vendorItems = options
        if (vendorBox.indexOfValue(vendorFilter) < 0) vendorFilter = ""
        applyFilter()
    }

    function applyFilter() {
        if (!backend) return
        backend.filterNames(searchInput.text, onlyTranslated, vendorFilter)
        suiteCounts = backend.nameSuiteCounts()
    }

    function openBatchWrite() {
        if (!backend) return
        var entries = backend.selectedNameEnList()
        var lines = []
        for (var i = 0; i < entries.length; i++) lines.push(entries[i])
        writeSelectionCount = lines.length
        leftColumn.text = lines.join("\n")
        leftColumn.scrollY = 0
        rightColumn.text = ""
        rightColumn.scrollY = 0
        writePreview = backend.previewBatchWrite(leftColumn.text, rightColumn.text)
        writePop.open()
    }

    Timer {
        id: searchDebounce
        interval: 200
        onTriggered: root.applyFilter()
    }

    Connections {
        target: root.backend
        function onInventoryChanged() { root.refreshNames() }
    }

    MouseArea {
        anchors.fill: parent
        z: -1
        onPressed: root.clearSelection()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceSm

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceSm

            Basic.TabBar {
                id: tabs
                objectName: "nameEditorTabs"
                Layout.preferredWidth: namesTab.implicitWidth + dictionaryTab.implicitWidth + spacing
                spacing: Theme.spaceXs
                background: null
                onCurrentIndexChanged: {
                    if (currentIndex === 1) dictPane.reload()
                }

                Tab {
                    id: namesTab
                    text: "效果名"
                    objectName: "effectNamesTab"
                    width: implicitWidth
                }

                Tab {
                    id: dictionaryTab
                    text: "词典"
                    objectName: "dictionaryTab"
                    width: implicitWidth
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.rightMargin: Theme.spaceXs
                visible: tabs.currentIndex === 0 && root.statsText.length > 0
                text: root.statsText
                horizontalAlignment: Text.AlignRight
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                color: Theme.textSecondary
                elide: Text.ElideLeft
            }

            Item {
                Layout.fillWidth: true
                visible: tabs.currentIndex !== 0
            }
        }

        Rectangle {
            id: workspace
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.panelFill
            radius: Theme.radiusCard

            ColumnLayout {
                anchors.fill: parent
                visible: tabs.currentIndex === 0
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: root.tableMargin
                    Layout.rightMargin: root.tableMargin
                    Layout.topMargin: Theme.spaceSm
                    Layout.bottomMargin: Theme.spaceXs
                    spacing: Theme.spaceSm

                    Basic.TextField {
                        id: searchInput
                        objectName: "nameSearch"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 100
                        Layout.maximumWidth: 324
                        Layout.preferredHeight: 34
                        leftPadding: 32
                        rightPadding: Theme.spaceSm
                        placeholderText: "搜索英文或中文"
                        placeholderTextColor: Theme.textSecondary
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                        selectByMouse: true
                        onTextChanged: searchDebounce.restart()

                        background: Rectangle {
                            radius: Theme.radiusControl
                            color: Theme.insetFill
                            border.width: searchInput.activeFocus ? 1 : 0
                            border.color: Theme.accent

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
                    }

                    ChoiceBox {
                        id: vendorBox
                        objectName: "nameVendorBox"
                        popupName: "nameVendorPop"
                        Layout.preferredWidth: Math.min(186, Math.round(workspace.width * 0.27))
                        textRole: "label"
                        valueRole: "value"
                        model: root.vendorItems
                        currentIndex: indexOfValue(root.vendorFilter)
                        displayText: root.vendorFilter.length > 0 ? root.vendorFilter : "全部插件"
                        onActivated: function(index) {
                            root.vendorFilter = valueAt(index)
                            root.applyFilter()
                        }
                    }

                    Basic.CheckBox {
                        id: translatedToggle
                        text: "只看已译"
                        checked: root.onlyTranslated
                        spacing: Theme.spaceXs
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                        onToggled: {
                            root.onlyTranslated = checked
                            root.applyFilter()
                        }

                        indicator: Rectangle {
                            width: 14
                            height: 14
                            radius: 4
                            x: translatedToggle.leftPadding
                            anchors.verticalCenter: parent.verticalCenter
                            color: translatedToggle.checked ? Theme.accent : Theme.insetFill
                            border.width: 1
                            border.color: translatedToggle.checked || translatedToggle.visualFocus
                                          ? Theme.accent : Theme.controlStroke

                            Text {
                                anchors.centerIn: parent
                                visible: translatedToggle.checked
                                text: "\uE73E"
                                color: "#FFFFFF"
                                font.family: Theme.iconFamily
                                font.pixelSize: 9
                            }
                        }

                        contentItem: Text {
                            leftPadding: translatedToggle.indicator.width + translatedToggle.spacing
                            text: translatedToggle.text
                            font: translatedToggle.font
                            color: Theme.textSecondary
                            verticalAlignment: Text.AlignVCenter
                        }
                    }

                    MouseArea {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 34
                        onPressed: root.clearSelection()
                        onDoubleClicked: root.refreshNames()
                    }

                    Button {
                        objectName: "openBatchNameWrite"
                        text: "批量写入"
                        primary: true
                        onClicked: root.openBatchWrite()
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: root.tableMargin
                    Layout.rightMargin: root.tableMargin
                    Layout.bottomMargin: Theme.spaceXs
                    spacing: Theme.spaceXs

                    Text {
                        text: "已选 " + root.selectionCount + " 条"
                        Layout.rightMargin: Theme.spaceXs
                        color: root.selectionCount > 0 ? Theme.accent : Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }

                    Button {
                        text: "全选筛选结果"
                        quiet: true
                        implicitWidth: Theme.fontBody * 6 + 12
                        implicitHeight: 28
                        onClicked: if (root.backend) root.backend.selectAllNames(true)
                    }

                    Button {
                        text: "取消选择"
                        quiet: true
                        implicitWidth: Theme.fontBody * 4 + 12
                        implicitHeight: 28
                        enabledButton: root.selectionCount > 0
                        onClicked: root.clearSelection()
                    }

                    Item { Layout.fillWidth: true }

                    Button {
                        text: "AI 重译所选"
                        quiet: true
                        implicitWidth: Theme.fontBody * 6 + 12
                        implicitHeight: 28
                        enabledButton: root.selectionCount > 0
                        onClicked: root.backend.retranslateSelected()
                    }

                    Button {
                        id: clearButton
                        text: armed ? "再次点击确认清空" : "清空所选译文"
                        quiet: true
                        danger: armed
                        implicitWidth: Theme.fontBody * (armed ? 8 : 6) + 12
                        implicitHeight: 28
                        enabledButton: root.selectionCount > 0
                        property bool armed: false
                        onEnabledButtonChanged: if (!enabledButton) armed = false

                        Timer {
                            id: clearTimer
                            interval: 3000
                            onTriggered: clearButton.armed = false
                        }

                        onClicked: {
                            if (!armed) {
                                armed = true
                                clearTimer.restart()
                                return
                            }
                            armed = false
                            root.tip = root.backend.batchClearNames()
                            root.statsText = root.backend.nameStats()
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    color: Theme.insetFill

                    MouseArea {
                        anchors.fill: parent
                        onClicked: root.clearSelection()
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: root.tableMargin
                        anchors.rightMargin: root.tableMargin
                        spacing: Theme.spaceMd

                        Text {
                            Layout.minimumWidth: root.colEnW
                            Layout.preferredWidth: root.colEnW
                            Layout.maximumWidth: root.colEnW
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
                            Layout.preferredWidth: root.colStatusW
                            text: "槽位 / 状态"
                            color: Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            font.weight: Font.DemiBold
                        }
                    }
                }

                ListView {
                    id: list
                    objectName: "nameList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: root.backend ? root.backend.nameModel : null
                    boundsBehavior: Flickable.StopAtBounds
                    Basic.ScrollBar.vertical: Basic.ScrollBar {}
                    cacheBuffer: 320

                    MouseArea {
                        anchors.fill: parent
                        z: -1
                        onPressed: root.clearSelection()
                    }

                    Text {
                        anchors.centerIn: parent
                        visible: list.count === 0
                        text: root.tip === "请先扫描插件" ? root.tip : "没有匹配的效果名"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                    }

                    section.property: "suite"
                    section.criteria: ViewSection.FullString
                    section.delegate: Rectangle {
                        width: list.width
                        height: 30
                        color: Theme.bgHover

                        MouseArea {
                            anchors.fill: parent
                            z: -1
                            onPressed: root.clearSelection()
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: root.tableMargin
                            anchors.rightMargin: root.tableMargin
                            spacing: Theme.spaceSm

                            Text {
                                Layout.maximumWidth: Math.round(parent.width * 0.5)
                                elide: Text.ElideRight
                                text: section
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                                font.weight: Font.DemiBold
                            }

                            Text {
                                text: (root.suiteCounts[section] || 0) + " 条"
                                color: Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                            }

                            Item { Layout.fillWidth: true }

                            Button {
                                text: "全选本组"
                                quiet: true
                                implicitWidth: Theme.fontBody * 4 + 10
                                implicitHeight: 26
                                onClicked: if (root.backend) root.backend.selectNameSuite(section, true)
                            }

                            Button {
                                text: "取消本组"
                                quiet: true
                                implicitWidth: Theme.fontBody * 4 + 10
                                implicitHeight: 26
                                onClicked: if (root.backend) root.backend.selectNameSuite(section, false)
                            }
                        }
                    }

                    delegate: Rectangle {
                        id: rowDelegate
                        readonly property int rowIndex: index
                        width: list.width
                        height: Theme.roomy ? 34 : 30
                        color: model.status === "long"
                               ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.09)
                               : "transparent"

                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.leftMargin: root.tableMargin
                            anchors.rightMargin: root.tableMargin
                            height: 1
                            color: Theme.borderSoft
                        }

                        // 行内选字和跨行框选共享鼠标抓取，禁止列表抢占。
                        MouseArea {
                            id: rowArea
                            objectName: "nameRowArea"
                            anchors.fill: parent
                            z: 10
                            cursorShape: Qt.IBeamCursor
                            preventStealing: true
                            property bool pending: false
                            property bool shiftMode: false
                            property bool inTranslation: false
                            property int charAnchor: -1

                            function resetPress() {
                                pending = false
                                shiftMode = false
                                inTranslation = false
                                charAnchor = -1
                            }

                            function positionInTranslation(mouse) {
                                var point = mapToItem(rowInput, mouse.x, mouse.y)
                                return rowInput.positionAt(point.x, point.y)
                            }

                            onPressed: function(mouse) {
                                if (!root.backend) return
                                inTranslation = mouse.x >= root.colZhStart && mouse.x < root.statusStart
                                root.selColumn = inTranslation ? "zh" : "en"
                                if (inTranslation) {
                                    rowInput.forceActiveFocus()
                                    charAnchor = positionInTranslation(mouse)
                                } else {
                                    root.forceActiveFocus()
                                }
                                if ((mouse.modifiers & Qt.ShiftModifier) !== 0) {
                                    shiftMode = true
                                    root.backend.toggleNameSelected(model.en, true)
                                    return
                                }
                                pending = true
                                root.dragPressed = true
                                root.dragAnchorIdx = index
                                root.dragLastIdx = index
                                root.dragActive = false
                            }

                            onPositionChanged: function(mouse) {
                                if (!pending || !root.backend) return
                                var point = mapToItem(list, mouse.x, mouse.y)
                                var row = root.rowIdxAt(point.x, point.y)
                                if (!root.dragActive && row !== root.dragAnchorIdx) {
                                    root.dragActive = true
                                    charAnchor = -1
                                    if (inTranslation) {
                                        root.forceActiveFocus()
                                        rowInput.deselect()
                                    }
                                    root.backend.clearNameSelection()
                                }
                                if (root.dragActive) {
                                    var edge = Math.min(52, list.height / 4)
                                    var direction = point.y > list.height - edge ? 1 : (point.y < edge ? -1 : 0)
                                    if (direction !== root.dragScrollDir) {
                                        root.dragScrollDir = direction
                                        root.dragTicks = 0
                                    }
                                    if (row >= 0 && row !== root.dragLastIdx) {
                                        root.dragLastIdx = row
                                        root.backend.sweepNameReplace(root.dragAnchorIdx, row)
                                    }
                                } else if (inTranslation && charAnchor >= 0) {
                                    var current = positionInTranslation(mouse)
                                    rowInput.select(Math.min(charAnchor, current), Math.max(charAnchor, current))
                                }
                            }

                            onCanceled: {
                                resetPress()
                                if (!root.backend || !root.backend.leftButtonDown()) root.finishDrag()
                            }

                            onReleased: function(mouse) {
                                if (root.backend && !root.dragActive && !shiftMode) {
                                    if (inTranslation) {
                                        if (charAnchor < 0 || rowInput.selectionStart === rowInput.selectionEnd) {
                                            rowInput.forceActiveFocus()
                                            rowInput.deselect()
                                            rowInput.cursorPosition = positionInTranslation(mouse)
                                        }
                                    } else {
                                        root.backend.toggleNameSelected(model.en, false)
                                    }
                                }
                                resetPress()
                                root.finishDrag()
                            }
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: root.tableMargin
                            anchors.rightMargin: root.tableMargin
                            spacing: Theme.spaceMd

                            TextEdit {
                                id: enEdit
                                Layout.minimumWidth: root.colEnW
                                Layout.preferredWidth: root.colEnW
                                Layout.maximumWidth: root.colEnW
                                Layout.fillHeight: true
                                readOnly: true
                                textFormat: TextEdit.PlainText
                                selectByMouse: false
                                activeFocusOnPress: false
                                verticalAlignment: TextEdit.AlignVCenter
                                clip: true
                                text: model.en + (model.extraFiles > 0
                                                  ? "  ×" + (model.extraFiles + 1) : "")
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                                selectionColor: Theme.accent
                                selectedTextColor: "#FFFFFF"

                                readonly property bool blockSelected:
                                    model.selected && root.selColumn === "en"
                                onBlockSelectedChanged: syncSelection()
                                Component.onCompleted: syncSelection()
                                function syncSelection() {
                                    if (blockSelected) select(0, text.length)
                                    else deselect()
                                }
                            }

                            Item {
                                Layout.fillWidth: true
                                Layout.fillHeight: true

                                TextInput {
                                    id: rowInput
                                    objectName: "nameZhInput"
                                    anchors.fill: parent
                                    verticalAlignment: TextInput.AlignVCenter
                                    color: Theme.textPrimary
                                    font.family: Theme.fontFamily
                                    font.pixelSize: Theme.fontSmall
                                    selectByMouse: false
                                    clip: true
                                    text: model.zh
                                    selectionColor: Theme.accent
                                    selectedTextColor: "#FFFFFF"

                                    readonly property bool blockSelected:
                                        model.selected && root.selColumn === "zh"
                                    onBlockSelectedChanged: syncSelection()
                                    Component.onCompleted: syncSelection()
                                    function syncSelection() {
                                        if (blockSelected && !activeFocus) select(0, text.length)
                                        else if (!blockSelected) deselect()
                                    }

                                    onEditingFinished: {
                                        if (!root.backend || text === model.zh) return
                                        root.tip = root.backend.saveName(model.en, text)
                                        root.statsText = root.backend.nameStats()
                                    }

                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        visible: rowInput.text.length === 0 && !rowInput.activeFocus
                                        text: "未翻译"
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
                                    color: Theme.accent
                                    visible: rowInput.activeFocus
                                }
                            }

                            Text {
                                Layout.preferredWidth: root.colStatusW
                                text: (model.slot > 0 ? model.slot + " B · " : "") + model.statusText
                                color: model.status === "long" ? Theme.danger : Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                                elide: Text.ElideRight
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
                        text: root.tip.length > 0 && !root.tip.startsWith("已载入")
                              ? root.tip : "点击译文编辑，按回车或移开焦点保存"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                        elide: Text.ElideRight
                    }

                    Text {
                        text: "显示 " + list.count + " 条"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontSmall
                    }
                }
            }

            DictPane {
                id: dictPane
                anchors.fill: parent
                visible: tabs.currentIndex === 1
                backend: root.backend
            }
        }
    }

    component BatchColumn: ColumnLayout {
        id: column
        property string title: ""
        property string placeholder: ""
        property string inputName: ""
        property string flickName: ""
        property bool readOnly: false
        property alias text: editor.text
        property alias edit: editor
        property alias scrollY: flick.contentY

        spacing: Theme.spaceXs

        Text {
            text: column.title
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontSmall
            font.weight: Font.DemiBold
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusControl
            color: Theme.insetFill
            border.width: 1
            border.color: editor.activeFocus ? Theme.accent : Theme.controlStroke

            Flickable {
                id: flick
                objectName: column.flickName
                anchors.fill: parent
                anchors.margins: Theme.spaceSm
                contentWidth: width
                contentHeight: Math.max(height, editor.implicitHeight)
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                Basic.ScrollBar.vertical: Basic.ScrollBar {}

                TextEdit {
                    id: editor
                    objectName: column.inputName
                    width: flick.width
                    height: Math.max(flick.height, implicitHeight)
                    readOnly: column.readOnly
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.NoWrap
                    selectByMouse: true
                    color: column.readOnly ? Theme.textSecondary : Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    onCursorPositionChanged: root.ensureCursorVisible(flick, editor)
                }

                Text {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    visible: editor.text.length === 0
                    text: column.placeholder
                    color: Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    Basic.Popup {
        id: writePop
        objectName: "batchWritePopup"
        parent: Basic.Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(720, parent.width - Theme.spaceXl * 2)
        height: Math.min(460, parent.height - Theme.spaceXl * 2)
        padding: Theme.spaceLg
        modal: true
        focus: true
        closePolicy: Basic.Popup.CloseOnEscape | Basic.Popup.CloseOnPressOutside
        onOpened: rightColumn.edit.forceActiveFocus()

        background: Rectangle {
            radius: Theme.radiusDialog
            color: Theme.popupFill
            border.width: 1
            border.color: Theme.controlStroke
        }

        contentItem: ColumnLayout {
            spacing: Theme.spaceMd

            RowLayout {
                Layout.fillWidth: true

                Text {
                    Layout.fillWidth: true
                    text: "批量写入译文"
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontHeading
                    font.weight: Font.DemiBold
                }

                Button {
                    text: "复制英文"
                    enabledButton: root.writeSelectionCount > 0
                    onClicked: if (root.backend) root.backend.copySelectedNames()
                }
            }

            Text {
                Layout.fillWidth: true
                text: "逐行粘贴中文，与左栏一一对应。也可粘贴由 Tab、等号或空格分隔的中英对照，按英文原名匹配。清空译文后恢复词典原值。"
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: Theme.spaceMd

                BatchColumn {
                    id: leftColumn
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 1
                    title: "所选英文 · " + root.writeSelectionCount + " 条"
                    placeholder: "请在列表中勾选或拖选英文原名"
                    inputName: "batchWriteLeft"
                    flickName: "batchWriteLeftFlick"
                    readOnly: true
                }

                BatchColumn {
                    id: rightColumn
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 1
                    title: "译文"
                    placeholder: "在这里粘贴或输入"
                    inputName: "batchWriteInput"
                    flickName: "batchWriteRightFlick"
                    onTextChanged: {
                        if (root.backend)
                            root.writePreview = root.backend.previewBatchWrite(leftColumn.text, text)
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: {
                    var preview = root.writePreview
                    if (!preview || preview.pairs + preview.zhMapped + preview.unknown === 0)
                        return "粘贴后显示写入预览"
                    var parts = []
                    if (preview.zhMapped > 0)
                        parts.push("按左栏顺序写入 " + preview.zhMapped + " 条")
                    if (preview.pairs > 0)
                        parts.push("按原名写入 " + preview.pairs + " 条")
                    if (preview.zhSkipped > 0)
                        parts.push(preview.zhSkipped + " 行译文无对应原名")
                    if (preview.pairsUnknown > 0)
                        parts.push(preview.pairsUnknown + " 条仅写入词典")
                    if (preview.unknown > 0)
                        parts.push(preview.unknown + " 行无法识别")
                    return parts.join("  ·  ")
                }
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceSm

                Item { Layout.fillWidth: true }

                Button {
                    text: "取消"
                    quiet: true
                    onClicked: writePop.close()
                }

                Button {
                    text: "写入"
                    primary: true
                    onClicked: {
                        if (!root.backend) return
                        root.tip = root.backend.writeNamesFromPopup(leftColumn.text, rightColumn.text)
                        root.statsText = root.backend.nameStats()
                        writePop.close()
                    }
                }
            }
        }
    }

    Timer {
        interval: 16
        repeat: true
        running: root.dragPressed && root.dragScrollDir !== 0
        onTriggered: {
            if (!root.backend || !root.backend.leftButtonDown()) {
                root.finishDrag()
                return
            }
            root.dragTicks++
            var speed = Math.min(70, 14 + root.dragTicks * 1.5)
            var minY = list.originY
            var maxY = minY + Math.max(0, list.contentHeight - list.height)
            var nextY = Math.max(minY, Math.min(maxY, list.contentY + root.dragScrollDir * speed))
            if (nextY === list.contentY) {
                root.dragScrollDir = 0
                return
            }
            list.contentY = nextY
            var edgeY = root.dragScrollDir > 0 ? list.height - 2 : 2
            var edgeIndex = root.rowIdxAt(list.width / 2, edgeY)
            if (edgeIndex >= 0 && edgeIndex !== root.dragLastIdx) {
                root.dragLastIdx = edgeIndex
                root.backend.sweepNameReplace(root.dragAnchorIdx, edgeIndex)
            }
        }
    }
}
