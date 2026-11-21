import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import AeLocalizer
// 同目录控件在 QtQuick.Controls 之后导入。
import "."

Popup {
    id: picker
    property var backend: null
    property var items: []

    property string query: ""
    property string kindFilter: "all"
    property var filtered: []
    property var selected: null
    property var weProps: []

    modal: true
    dim: true
    padding: 0
    width: Math.min(1010, (parent ? parent.width : 800) - 36)
    height: Math.min(600, (parent ? parent.height : 600) - 60)
    anchors.centerIn: Overlay.overlay

    background: Rectangle {
        color: Theme.bgElevatedGlass
        radius: Theme.radiusDialog
        border.width: 1
        border.color: Theme.border
    }

    function reload() {
        items = backend ? backend.weWallpapers() : []
        refilter()
    }

    function refilter() {
        var out = []
        var q = String(query || "").toLowerCase()
        for (var i = 0; i < items.length; ++i) {
            var w = items[i]
            if (kindFilter !== "all" && String(w.kind) !== kindFilter) continue
            if (q !== "" && String(w.title || "").toLowerCase().indexOf(q) < 0) continue
            out.push(w)
        }
        filtered = out
        if (selected) {
            var still = null
            for (var j = 0; j < out.length; ++j) if (out[j].id === selected.id) still = out[j]
            selected = still
        }
    }

    function selectItem(w) {
        selected = w
        tuneRead(w.id)
        weProps = backend ? backend.weProps(w.id) : []
    }

    property string propMsg: ""

    function propSet(name, value) {
        if (!backend || !selected) return
        var r = backend.setWeProp(selected.id, name, value)
        propMsg = (r && r.msg) ? String(r.msg) : ""
        weProps = backend.weProps(selected.id)
    }

    function comboLabel(md) {
        var opts = md.options || []
        var v = String(md.effective)
        for (var i = 0; i < opts.length; ++i)
            if (String(opts[i].value) === v) return String(opts[i].label)
        return v
    }

    function comboNext(md) {
        var opts = md.options || []
        if (opts.length === 0) return
        var v = String(md.effective)
        for (var i = 0; i < opts.length; ++i) {
            if (String(opts[i].value) === v) {
                propSet(md.name, String(opts[(i + 1) % opts.length].value))
                return
            }
        }
        propSet(md.name, String(opts[0].value))
    }

    function colorSwatch(v) {
        var p = String(v || "").trim().split(/\s+/)
        if (p.length < 3) return String(v || "")
        var out = "#"
        for (var i = 0; i < 3; ++i) {
            var n = Math.round(Math.max(0, Math.min(1, parseFloat(p[i]))) * 255)
            var s = n.toString(16)
            out += (s.length < 2 ? "0" + s : s)
        }
        return out
    }

    property var tune: ({})
    function tuneRead(wid) {
        tune = backend ? backend.weTune(wid) : ({})
    }
    function tuneSet(key, val) {
        if (!backend || !selected) return
        tune = backend.setWeTune(selected.id, key, val)
    }

    onOpened: {
        reload()
        for (var i = 0; i < filtered.length; ++i) {
            if (filtered[i].current) { list.positionViewAtIndex(i, ListView.Center); break }
        }
    }

    onQueryChanged: refilter()
    onKindFilterChanged: refilter()
    onItemsChanged: refilter()

    contentItem: ColumnLayout {
        spacing: 0

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 62

            Column {
                anchors.left: parent.left
                anchors.leftMargin: Theme.spaceLg
                anchors.right: headBtnRow.left
                anchors.rightMargin: Theme.spaceLg
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3
                Text {
                    width: parent.width
                    text: "壁纸引擎"
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontTitle
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    text: checkInstall()
                    color: Theme.textTertiary
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
            }

            Row {
                id: headBtnRow
                anchors.right: parent.right
                anchors.rightMargin: Theme.spaceLg
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spaceSm

                Button {
                    text: "刷新"
                    onClicked: if (picker.backend) picker.items = picker.backend.refreshWallpapers()
                }
                Button {
                    text: "关闭"
                    onClicked: picker.close()
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spaceMd
            Layout.rightMargin: Theme.spaceMd
            Layout.topMargin: Theme.spaceMd
            spacing: Theme.spaceSm

            Rectangle {
                Layout.preferredWidth: 200
                Layout.preferredHeight: 30
                radius: Theme.radiusControl
                color: Theme.fieldFill
                border.width: 1
                border.color: searchInput.activeFocus ? Theme.accent : Theme.borderSoft

                TextInput {
                    id: searchInput
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spaceSm
                    anchors.rightMargin: Theme.spaceSm
                    verticalAlignment: TextInput.AlignVCenter
                    color: Theme.textPrimary
                    selectionColor: Theme.accent
                    selectedTextColor: "#FFFFFF"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    clip: true
                    onTextChanged: picker.query = text
                }
                Text {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spaceSm
                    verticalAlignment: Text.AlignVCenter
                    visible: searchInput.text.length === 0
                    text: "搜标题…"
                    color: Theme.textTertiary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                }
            }

            Segmented {
                current: picker.kindFilter
                options: [
                    { key: "all",    label: "全部" },
                    { key: "video",  label: "视频" },
                    { key: "scene",  label: "场景" },
                    { key: "web",    label: "网页" },
                    { key: "image",  label: "图片" }
                ]
                onPicked: function(key) { picker.kindFilter = key }
            }

            Item { Layout.fillWidth: true }
            Text {
                text: picker.filtered.length + " / " + picker.items.length
                color: Theme.textTertiary
                font.family: Theme.monoFamily
                font.pixelSize: 11
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: Theme.spaceMd
            spacing: Theme.spaceMd

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 100
                clip: true
                model: picker.filtered
                spacing: Theme.spaceXs
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Rectangle {
                    required property var modelData
                    required property int index

                    width: list.width - Theme.spaceSm
                    height: 60
                    radius: Theme.radiusControl
                    color: picker.selected && picker.selected.id === modelData.id
                           ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b,
                                     Theme.darkGlass ? 0.35 : 0.16)
                           : (rowMouse.containsMouse ? Theme.bgHover
                              : (modelData.current ? Theme.bgHover : "transparent"))

                    MouseArea {
                        id: rowMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.LeftButton
                        onClicked: picker.selectItem(modelData)
                        onDoubleClicked: {
                            if (!picker.backend || modelData.current) return
                            picker.backend.applyWallpaper(modelData.id)
                            picker.close()
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: Theme.spaceSm
                        spacing: Theme.spaceMd

                        Rectangle {
                            Layout.preferredWidth: 84
                            Layout.preferredHeight: 44
                            radius: 4
                            color: Theme.fieldFill
                            clip: true
                            Image {
                                anchors.fill: parent
                                source: modelData.previewUrl
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                visible: modelData.previewUrl !== ""
                            }
                            Text {
                                anchors.centerIn: parent
                                visible: modelData.previewUrl === ""
                                text: "无预览"
                                color: Theme.textTertiary
                                font.family: Theme.fontFamily
                                font.pixelSize: 10
                            }
                        }

                        Column {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                width: parent.width
                                text: modelData.title
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                elide: Text.ElideRight
                            }
                            Text {
                                width: parent.width
                                text: (modelData.current ? "使用中 · " : "")
                                      + kindLabel(modelData.kind)
                                      + (modelData.source === "workshop" ? " · 工坊" : " · 本地")
                                      + (modelData.current && modelData.kind === "scene"
                                         ? (picker.backend && picker.backend.bgSceneWeId === modelData.id
                                            ? " · 实时渲染" : " · 提取画面")
                                         : "")
                                color: modelData.current ? Theme.accent : Theme.textTertiary
                                font.family: Theme.fontFamily
                                font.pixelSize: 11
                                elide: Text.ElideRight
                            }
                        }

                        Button {
                            text: modelData.current ? "使用中" : "设为背景"
                            primary: !modelData.current
                            enabledButton: picker.backend && !picker.backend.busy
                            onClicked: {
                                if (!picker.backend) return
                                picker.backend.applyWallpaper(modelData.id)
                                picker.close()
                            }
                        }
                    }

                }

                Text {
                    anchors.centerIn: parent
                    visible: list.count === 0
                    width: list.width - Theme.spaceXl * 2
                    horizontalAlignment: Text.AlignHCenter
                    text: picker.items.length === 0
                          ? picker.checkInstall()
                          : "没有匹配的壁纸 —— 换个关键词或把类型切回「全部」"
                    color: Theme.textTertiary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.WordWrap
                }
            }

            Rectangle {
                Layout.preferredWidth: Math.min(380, Math.max(320, picker.width * 0.38))
                Layout.fillHeight: true
                radius: Theme.radiusCard
                color: Theme.fieldFill
                border.width: 1
                border.color: Theme.borderSoft

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.spaceMd
                    spacing: Theme.spaceSm

                    Text {
                        text: picker.selected ? picker.selected.title : "参数"
                        Layout.fillWidth: true
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Text {
                        Layout.fillWidth: true
                        text: picker.selected
                              ? "这些参数按壁纸分开记住，切回来还在。选「设为背景」后即生效。"
                              : "左边点一张壁纸，这里就能单独调它。"
                        color: Theme.textTertiary
                        font.family: Theme.fontFamily
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                        lineHeight: 1.3
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: picker.backend && picker.backend.weEngineRunning
                        text: "壁纸引擎正在运行：改动会先记下来，等它关闭后本工具再写一次。"
                        color: Theme.warn
                        font.family: Theme.fontFamily
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                        lineHeight: 1.3
                    }

                    Flickable {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: colBody.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                        ColumnLayout {
                            id: colBody
                            width: parent.width - 24
                            spacing: Theme.spaceSm

                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

                            ColumnLayout {
                                Layout.fillWidth: true
                                enabled: picker.selected !== null
                                opacity: enabled ? 1.0 : 0.45
                                spacing: Theme.spaceSm

                                Repeater {
                                    model: [
                                        { key: "blur",       label: "磨砂",   min: 0,  max: 60,  suffix: "" },
                                        { key: "brightness", label: "亮度",   min: 50, max: 160, suffix: "%" },
                                        { key: "saturation", label: "饱和度", min: 0,  max: 200, suffix: "%" },
                                        { key: "contrast",   label: "对比度", min: 50, max: 160, suffix: "%" }
                                    ]
                                    delegate: ColumnLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        spacing: 0
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text {
                                                text: modelData.label
                                                color: Theme.textSecondary
                                                font.family: Theme.fontFamily
                                                font.pixelSize: Theme.fontSmall
                                            }
                                            Item { Layout.fillWidth: true }
                                            Text {
                                                text: (picker.tune && picker.tune[modelData.key] !== undefined
                                                       ? picker.tune[modelData.key] : "-") + modelData.suffix
                                                color: Theme.textTertiary
                                                font.family: Theme.monoFamily
                                                font.pixelSize: 11
                                                Layout.preferredWidth: 52
                                                Layout.rightMargin: 4
                                                horizontalAlignment: Text.AlignRight
                                            }
                                        }
                                        Slider {
                                            id: sl
                                            Layout.fillWidth: true
                                            min: modelData.min
                                            max: modelData.max
                                            function sync() {
                                                if (picker.tune && picker.tune[modelData.key] !== undefined)
                                                    value = picker.tune[modelData.key]
                                            }
                                            Component.onCompleted: sync()
                                            Connections {
                                                target: picker
                                                function onTuneChanged() { sl.sync() }
                                            }
                                            onMoved: function(v) {
                                                picker.tuneSet(modelData.key, Math.round(v))
                                            }
                                        }
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        text: "填充"
                                        color: Theme.textSecondary
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontSmall
                                    }
                                    Item { Layout.fillWidth: true }
                                    Segmented {
                                        current: picker.tune && picker.tune.fit ? picker.tune.fit : "cover"
                                        options: [
                                            { key: "cover",   label: "铺满" },
                                            { key: "contain", label: "完整" }
                                        ]
                                        onPicked: function(key) { picker.tuneSet("fit", key) }
                                    }
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

                            Text {
                                Layout.fillWidth: true
                                text: "壁纸自带参数 " + picker.weProps.length
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                                font.weight: Font.DemiBold
                            }
                            Text {
                                Layout.fillWidth: true
                                text: picker.weProps.length > 0
                                      ? "这些是壁纸自己的参数：改完立刻作用到背景（实时渲染，不用重启壁纸引擎）。"
                                      : "这张壁纸没有声明自己的参数。"
                                color: Theme.textTertiary
                                font.family: Theme.fontFamily
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                                lineHeight: 1.3
                            }

                            Repeater {
                                model: picker.weProps
                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 1

                                    Text {
                                        visible: modelData.type === "group"
                                        Layout.fillWidth: true
                                        text: modelData.label
                                        color: Theme.textPrimary
                                        font.family: Theme.fontFamily
                                        font.pixelSize: 10
                                        font.weight: Font.DemiBold
                                        topPadding: 6
                                    }

                                    RowLayout {
                                        visible: modelData.type !== "group"
                                        Layout.fillWidth: true
                                        spacing: Theme.spaceSm

                                        Text {
                                            Layout.fillWidth: true
                                            text: modelData.label
                                                + (modelData.hasOverride ? "  ·已改" : "")
                                            color: modelData.hasOverride ? Theme.accent
                                                                         : Theme.textSecondary
                                            font.family: Theme.fontFamily
                                            font.pixelSize: 10
                                            wrapMode: Text.Wrap
                                            maximumLineCount: 2
                                            elide: Text.ElideRight
                                            Layout.minimumWidth: 60
                                        }

                                        Rectangle {
                                            visible: modelData.type === "bool"
                                            implicitWidth: 44
                                            implicitHeight: 18
                                            radius: 4
                                            color: modelData.effective ? Theme.accentSoft : Theme.fieldFill
                                            border.width: 1
                                            border.color: modelData.effective ? Theme.accent : Theme.borderSoft
                                            Text {
                                                anchors.centerIn: parent
                                                text: modelData.effective ? "开" : "关"
                                                color: modelData.effective ? Theme.accent : Theme.textTertiary
                                                font.family: Theme.fontFamily
                                                font.pixelSize: 10
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: picker.propSet(modelData.name,
                                                                          !modelData.effective)
                                            }
                                        }

                                        Rectangle {
                                            visible: modelData.type === "combo"
                                            implicitWidth: 96
                                            implicitHeight: 18
                                            radius: 4
                                            color: Theme.fieldFill
                                            border.width: 1
                                            border.color: Theme.borderSoft
                                            Text {
                                                anchors.centerIn: parent
                                                width: parent.width - 8
                                                horizontalAlignment: Text.AlignHCenter
                                                text: picker.comboLabel(modelData)
                                                color: Theme.textSecondary
                                                font.family: Theme.fontFamily
                                                font.pixelSize: 10
                                                elide: Text.ElideRight
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: picker.comboNext(modelData)
                                            }
                                        }

                                        Rectangle {
                                            visible: modelData.type === "color"
                                            implicitWidth: 18
                                            implicitHeight: 18
                                            radius: 4
                                            color: modelData.type === "color"
                                                   ? picker.colorSwatch(modelData.effective) : "transparent"
                                            border.width: 1
                                            border.color: Theme.borderSoft
                                        }
                                        TextInput {
                                            visible: modelData.type === "color"
                                            Layout.preferredWidth: 78
                                            text: modelData.type === "color"
                                                  ? picker.colorSwatch(modelData.effective) : ""
                                            color: Theme.textSecondary
                                            selectionColor: Theme.accent
                                            selectedTextColor: "#FFFFFF"
                                            font.family: Theme.monoFamily
                                            font.pixelSize: 10
                                            clip: true
                                            onEditingFinished: picker.propSet(modelData.name, text)
                                        }

                                        TextInput {
                                            visible: modelData.type === "text" || modelData.type === "textinput"
                                            Layout.preferredWidth: 110
                                            text: String(modelData.effective === undefined
                                                         || modelData.effective === null
                                                         ? "" : modelData.effective)
                                            color: Theme.textSecondary
                                            selectionColor: Theme.accent
                                            selectedTextColor: "#FFFFFF"
                                            font.family: Theme.fontFamily
                                            font.pixelSize: 10
                                            clip: true
                                            onEditingFinished: picker.propSet(modelData.name, text)
                                        }

                                        Text {
                                            visible: modelData.type === "slider"
                                            text: String(modelData.effective)
                                            color: Theme.textTertiary
                                            font.family: Theme.monoFamily
                                            font.pixelSize: 9
                                            Layout.preferredWidth: 52
                                            Layout.rightMargin: 4
                                            horizontalAlignment: Text.AlignRight
                                        }
                                    }

                                    Slider {
                                        id: propSlider
                                        visible: modelData.type === "slider"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 16
                                        min: modelData.min !== undefined ? modelData.min : 0
                                        max: modelData.max !== undefined ? modelData.max : 1
                                        function sync() {
                                            if (modelData.type === "slider"
                                                    && modelData.effective !== undefined)
                                                value = Number(modelData.effective)
                                        }
                                        Component.onCompleted: sync()
                                        Connections {
                                            target: picker
                                            function onWePropsChanged() { propSlider.sync() }
                                        }
                                        onMoved: function(v) { picker.propSet(modelData.name, v) }
                                    }

                                    Text {
                                        visible: modelData.type !== "bool" && modelData.type !== "combo"
                                                 && modelData.type !== "color" && modelData.type !== "text"
                                                 && modelData.type !== "textinput" && modelData.type !== "slider"
                                                 && modelData.type !== "group"
                                        Layout.fillWidth: true
                                        text: String(modelData.effective)
                                        color: Theme.textTertiary
                                        font.family: Theme.monoFamily
                                        font.pixelSize: 9
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: picker.propMsg !== ""
                        text: picker.propMsg
                        color: Theme.warn
                        font.family: Theme.fontFamily
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                        lineHeight: 1.25
                    }

                    Button {
                        Layout.fillWidth: true
                        text: picker.selected && picker.selected.current ? "正在使用" : "设为背景"
                        primary: picker.selected !== null && !(picker.selected && picker.selected.current)
                        enabledButton: picker.backend && picker.selected !== null
                                        && !picker.backend.busy
                        onClicked: {
                            if (!picker.backend || !picker.selected) return
                            picker.backend.applyWallpaper(picker.selected.id)
                            picker.close()
                        }
                    }
                }
            }
        }
    }

    function checkInstall() {
        if (!backend) return ""
        if (!backend.weAvailable) return "没找到壁纸引擎安装 —— 需要 Steam 版 Wallpaper Engine"
        if (backend.weError) return backend.weError
        return "选中一张就能单独调它的画面参数；视频壁纸实时播放"
    }

    function kindLabel(kind) {
        if (kind === "video") return "视频"
        if (kind === "scene") return "场景"
        if (kind === "web") return "网页"
        if (kind === "image") return "图片"
        return kind || "未知"
    }
}
