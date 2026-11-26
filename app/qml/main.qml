import QtQuick
import QtQuick.Layouts
import QtQuick.Templates as Templates
import QtQuick.Window
import QtQuick.Effects
import AeLocalizer
import "components"
import "pages"

Window {
    id: root
    readonly property rect startupArea: typeof initialWindowWorkArea !== "undefined"
                                        ? initialWindowWorkArea : Qt.rect(0, 0, 0, 0)
    readonly property bool hasStartupArea: startupArea.width > 0 && startupArea.height > 0
    minimumWidth: hasStartupArea ? Math.min(840, startupArea.width) : 840
    minimumHeight: hasStartupArea ? Math.min(500, startupArea.height) : 500
    width: hasStartupArea ? initialExtent(startupArea.width, minimumWidth, 0.62) : 1180
    height: hasStartupArea ? initialExtent(startupArea.height, minimumHeight, 0.70) : 760
    visible: !hasStartupArea
    title: "AeLocalizer · 映"
    color: Theme.bgBase
    readonly property bool systemWindowFrame: typeof useWindowsFrame !== "undefined"
                                              && useWindowsFrame
                                              && Qt.ExpandedClientAreaHint !== undefined
    flags: Qt.Window | (systemWindowFrame
           ? Qt.ExpandedClientAreaHint | Qt.NoTitleBarBackgroundHint | Qt.CustomizeWindowHint
           : Qt.FramelessWindowHint)
    readonly property real titleBarHeight: titleBar.height
    readonly property real titleBarButtonsLeft: windowControls.x
    property bool windowResizing: false
    onWindowResizingChanged: if (backend) backend.setWindowResizing(windowResizing)
    onClosing: function(close) {
        if (!backend)
            return
        if (backend.panelLocalization && backend.panelLocalization.busy) {
            close.accepted = false
            pageIndex = 5
            return
        }
        backend.shutdown()
    }

    readonly property var backend: typeof appBackend !== "undefined" ? appBackend : null
    property int currentIndex: 0
    property int settingsSection: 0

    property int pageIndex: 0
    readonly property var navModel: [
        { section: "工作区", first: true, pages: [
            { page: 0, label: "插件翻译", icon: "\uE8C1" },
            { page: 1, label: "效果名称", icon: "\uE8EC" },
            { page: 3, label: "词条补录", icon: "\uE70F" },
            { page: 5, label: "面板与脚本", icon: "\uE943" }
        ] },
        { section: "管理", first: false, pages: [
            { page: 2, label: "汉化引擎", icon: "\uE950" },
            { page: 4, label: "设置", icon: "\uE713" }
        ] }
    ]

    // 窗口保留少量向内缩放余量，并限制在屏幕工作区内。
    function initialExtent(available, minimum, ratio) {
        var withResizeRoom = Math.min(minimum + 40, available - 16)
        return Math.max(minimum, withResizeRoom, Math.round(available * ratio))
    }

    function syncWallpaper() {
        if (!backend) return
        Theme.bgImage = backend.bgImage
        Theme.bgVideo = backend.bgVideo
        Theme.bgSceneUrl = backend.bgSceneUrl
        Theme.bgSceneRev = backend.bgSceneRev
        Theme.bgBlur = backend.bgBlur
        Theme.bgBrightness = backend.bgBrightness
        Theme.bgSaturation = backend.bgSaturation
        Theme.bgContrast = backend.bgContrast
        Theme.bgFit = backend.bgFit
        Theme.bgDim = backend.bgDim
        Theme.glassBlur = backend.glassBlur
    }

    Component.onCompleted: {
        if (hasStartupArea) {
            x = startupArea.x + Math.floor((startupArea.width - width) / 2)
            y = startupArea.y + Math.floor((startupArea.height - height) / 2)
            visible = true
        }
        if (!backend) return
        Theme.themeMode = backend.uiTheme
        syncWallpaper()
        Theme.accentKey = backend.accentKey
        Theme.density = backend.density
        Theme.fontScale = backend.fontScale
        Theme.reduceMotion = backend.reduceMotion
        Theme.plate = backend.plate
        Theme.uiFont = backend.uiFont
        Theme.textCustom = backend.textColor !== "" ? backend.textColor : "transparent"
    }

    Connections {
        target: backend
        function onUiThemeChanged() { if (backend) Theme.themeMode = backend.uiTheme }
        function onBgChanged() { root.syncWallpaper() }
        function onVideoBgChanged() { root.syncWallpaper() }
        function onAppearanceChanged() {
            if (!backend) return
            Theme.accentKey = backend.accentKey
            Theme.density = backend.density
            Theme.fontScale = backend.fontScale
            Theme.reduceMotion = backend.reduceMotion
            Theme.plate = backend.plate
            Theme.uiFont = backend.uiFont
            Theme.textCustom = backend.textColor !== "" ? backend.textColor : "transparent"
        }
    }

    Rectangle {
        id: bgLayer
        color: Theme.bgBase
        clip: true
        anchors.fill: parent

        Item {
            id: bgSource
            anchors.fill: parent
            layer.enabled: Theme.hasAnyBg && Theme.bgNeedsEffect && GraphicsInfo.api !== GraphicsInfo.Software
            layer.effect: MultiEffect {
                autoPaddingEnabled: false
                blurEnabled: Theme.bgBlur > 0
                blur: Theme.bgBlurNorm
                blurMax: Theme.bgBlurMax
                saturation: Theme.bgSaturationDelta
                brightness: Theme.bgBrightnessDelta
                contrast: Theme.bgContrastDelta
            }

            Loader {
                id: bgVideoLoader
                anchors.fill: parent
                active: Theme.bgVideo !== ""
                source: active ? "components/VideoBackground.qml" : ""
                onStatusChanged: if (status === Loader.Error)
                                     console.warn("视频背景不可用（缺少 QtMultimedia）")
            }

            Loader {
                id: bgSceneLoader
                anchors.fill: parent
                active: Theme.bgSceneUrl !== ""
                source: active ? "components/SceneBackground.qml" : ""
                onLoaded: if (item) item.backend = root.backend
                onStatusChanged: if (status === Loader.Error)
                                     console.warn("场景渲染不可用（缺少 QtWebEngine）")
            }

            Image {
                anchors.fill: parent
                visible: Theme.bgImage !== "" && Theme.bgVideo === ""
                         && Theme.bgSceneUrl === ""
                source: Theme.bgImage
                cache: false
                fillMode: Theme.bgFit === "contain" ? Image.PreserveAspectFit
                                                    : Image.PreserveAspectCrop
                asynchronous: true
            }
        }

        Rectangle {
            anchors.fill: parent
            visible: Theme.hasAnyBg
            color: "#000000"
            opacity: Theme.bgDim
        }
    }

    Binding {
        target: Theme
        property: "backdropItem"
        value: bgLayer
    }

    Rectangle {
        id: titleBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 44
        GlassBackdrop { anchors.fill: parent; z: -1 }
        color: Theme.titlebarFill

        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: Theme.border
        }

        MouseArea {
            anchors.fill: parent
            enabled: !root.systemWindowFrame
            onPressed: root.startSystemMove()
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.spaceLg
            anchors.rightMargin: Theme.spaceSm
            spacing: 6

            Rectangle {
                implicitWidth: 26
                implicitHeight: 26
                radius: 7
                color: Theme.actionFill
                Text {
                    anchors.centerIn: parent
                    text: "映"
                    font.family: Theme.fontDefault
                    font.pixelSize: 15
                    font.weight: Font.Bold
                    color: Theme.actionText
                }
            }

            Text {
                text: "AeLocalizer"
                color: Theme.textOnBg
                font.family: "Segoe UI"
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }

            Item { Layout.fillWidth: true }

            Text {
                text: (backend ? ("安装 " + backend.currentVersion + " · " + backend.hostAeVersionText) : "AE -")
                color: Theme.textTertiary
                font.family: Theme.monoFamily
                font.pixelSize: 11
            }
            Item { width: Theme.spaceSm }

            Row {
                id: windowControls
                spacing: 6

                Repeater {
                    model: [
                        { t: "—", act: "min" },
                        { t: "□", act: "max" },
                        { t: "✕", act: "close" }
                    ]
                    delegate: Rectangle {
                        width: 34; height: 26; radius: 6
                        color: area.containsMouse
                               ? (modelData.act === "close" ? Theme.danger : Theme.bgHover)
                               : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: modelData.t
                            color: area.containsMouse && modelData.act === "close" ? "#FFF" : Theme.textTertiary
                            font.pixelSize: 12
                        }
                        MouseArea {
                            id: area
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (modelData.act === "min") root.showMinimized()
                                else if (modelData.act === "max") {
                                    if (root.visibility === Window.Maximized) root.showNormal()
                                    else root.showMaximized()
                                } else root.close()
                            }
                        }
                    }
                }
            }
        }
    }

    Row {
        anchors.top: titleBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: statusBar.top
        spacing: 0

        Rectangle {
            id: side
            objectName: "appNavigation"
            width: Math.max(134, Math.min(190, 134 + (root.width - 840) / 6))
            height: parent.height
            GlassBackdrop { anchors.fill: parent; z: -1 }
            color: Theme.chromeFill

            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: Theme.borderSoft
            }

            readonly property bool compactSidebar: width < 164

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: side.compactSidebar ? 6 : Theme.spaceMd
                spacing: 0

                Repeater {
                    model: root.navModel
                    delegate: ColumnLayout {
                        id: navGroup
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 3

                        Text {
                            Layout.leftMargin: Theme.spaceSm
                            Layout.topMargin: navGroup.modelData.first ? Theme.spaceXs : Theme.spaceLg
                            Layout.bottomMargin: Theme.spaceSm
                            text: navGroup.modelData.section
                            color: Theme.textOnBgTertiary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            font.weight: Font.Medium
                        }

                        Repeater {
                            model: navGroup.modelData.pages
                            delegate: Templates.Button {
                                id: navItem
                                required property var modelData
                                readonly property bool selected: root.pageIndex === modelData.page
                                objectName: "navPage" + modelData.page
                                Layout.fillWidth: true
                                implicitHeight: Theme.dense ? 34 : 38
                                leftPadding: side.compactSidebar ? 7 : Theme.spaceSm + 2
                                rightPadding: side.compactSidebar ? 5 : Theme.spaceSm
                                hoverEnabled: true
                                text: modelData.label
                                Accessible.role: Accessible.PageTab
                                Accessible.selected: selected
                                onClicked: root.pageIndex = modelData.page
                                HoverHandler { cursorShape: Qt.PointingHandCursor }

                                background: Rectangle {
                                    radius: Theme.radiusControl
                                    color: navItem.selected ? Theme.selectionFill
                                           : navItem.hovered ? Theme.bgHover : "transparent"
                                    border.width: navItem.visualFocus ? 2 : 0
                                    border.color: Theme.focusRing
                                }
                                contentItem: RowLayout {
                                    spacing: side.compactSidebar ? 6 : Theme.spaceSm + 2
                                    Text {
                                        text: navItem.modelData.icon
                                        color: navItem.selected ? Theme.textPrimary : Theme.textSecondary
                                        font.family: Theme.iconFamily
                                        font.pixelSize: 16
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: navItem.text
                                        color: navItem.selected ? Theme.textPrimary : Theme.textSecondary
                                        font.family: Theme.fontFamily
                                        font.pixelSize: Theme.fontBody
                                        font.weight: navItem.selected ? Font.DemiBold : Font.Normal
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: profile.implicitHeight + Theme.spaceMd * 2
                    radius: Theme.radiusCard
                    color: Theme.panelFill
                    border.color: Theme.panelStroke
                    ColumnLayout {
                        id: profile
                        anchors.fill: parent
                        anchors.margins: side.compactSidebar ? 8 : Theme.spaceMd
                        spacing: Theme.spaceXs
                        Text {
                            text: "引擎状态"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            color: Theme.textSecondary
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Rectangle {
                                implicitWidth: 7
                                implicitHeight: 7
                                radius: 4
                                color: backend && backend.engineProfileConfirmed ? Theme.success : Theme.textTertiary
                            }
                            Text {
                                Layout.fillWidth: true
                                text: backend ? backend.engineProfileStatus : "引擎未运行"
                                color: Theme.textPrimary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: !!backend && backend.engineRunning
                            text: backend ? "词典 " + backend.engineDictEntries + " 条" : ""
                            color: Theme.textSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontSmall
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }

        StackLayout {
            width: parent.width - side.width
            height: parent.height
            currentIndex: root.pageIndex

            PageShell {
                title: "翻译工作台"
                description: "选择插件，生成译文并校对草稿。"
                actions: translationPage.headerActions
                AiPage { id: translationPage; anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "效果名称"
                description: "按厂商和套装查找效果，编辑显示名称。"
                NamesPage { anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "汉化引擎"
                description: "查看当前配置的运行状态，管理实时汉化通道。"
                EnginePage { anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "词条补录"
                description: "从 AE 提取词条，补充参数和下拉选项的译名。"
                CapturePage { anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "设置"
                description: "管理工作台外观、数据与更新。"
                SettingsPage {
                    id: settingsPage
                    anchors.fill: parent
                    backend: root.backend
                    Component.onCompleted: section = root.settingsSection
                    Connections {
                        target: root
                        function onSettingsSectionChanged() {
                            settingsPage.section = root.settingsSection
                        }
                    }
                }
            }
            PageShell {
                title: "面板与脚本"
                description: "选择 CEP 面板或 JSX 脚本，校对词条并预览汉化结果。"
                PanelPage {
                    anchors.fill: parent
                    controller: root.backend ? root.backend.panelLocalization : null
                }
            }
        }
    }

    Rectangle {
        id: statusBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 28
        GlassBackdrop { anchors.fill: parent; z: -1 }
        color: Theme.chromeFill

        Rectangle {
            anchors.top: parent.top
            width: parent.width
            height: 1
            color: Theme.borderSoft
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.spaceXl
            anchors.rightMargin: Theme.spaceXl
            spacing: Theme.spaceMd

            Rectangle {
                width: 6; height: 6; radius: 3
                color: backend && backend.busy ? Theme.warn : Theme.success
            }
            Text {
                text: backend && backend.busy ? backend.busyText
                                              : (backend ? backend.statusText : "就绪")
                color: Theme.textOnBgSecondary
                font.family: Theme.fontFamily
                font.pixelSize: 11
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            Text {
                text: backend ? ("构建 " + backend.appBuildTag) : ""
                color: Theme.textOnBgTertiary
                font.family: Theme.monoFamily
                font.pixelSize: 10
            }
            Text {
                text: (backend ? backend.selectedCount : 0) + " 组已选"
                color: Theme.textOnBgTertiary
                font.family: Theme.monoFamily
                font.pixelSize: 11
            }
        }
    }

    Column {
        id: toastArea
        anchors.bottom: statusBar.top
        anchors.right: parent.right
        anchors.margins: Theme.spaceLg
        spacing: Theme.spaceSm
        z: 100

        function push(level, text) {
            var c = Qt.createComponent("components/Toast.qml")
            if (c.status !== Component.Ready) return
            var o = c.createObject(toastArea, { level: level, text: text, parent: toastArea })
        }

        Connections {
            target: root.backend
            function onToast(level, text) { toastArea.push(level, text) }
        }
    }

    // 拖动时一次提交位置和尺寸，以免窗口分步重绘。
    Item {
        id: resizeFrame
        objectName: "windowResizeFrame"
        anchors.fill: parent
        z: 101
        visible: !root.systemWindowFrame && root.visibility === Window.Windowed
        readonly property int grip: 8
        property int activeEdges: 0
        property rect startGeometry
        property point startPoint

        function begin(edges, point) {
            activeEdges = edges
            startGeometry = Qt.rect(root.x, root.y, root.width, root.height)
            startPoint = point
            root.windowResizing = true
        }

        function move(point) {
            if (!activeEdges) return
            var dx = Math.round(point.x - startPoint.x)
            var dy = Math.round(point.y - startPoint.y)
            var start = startGeometry
            var left = activeEdges & Qt.LeftEdge
            var right = activeEdges & Qt.RightEdge
            var top = activeEdges & Qt.TopEdge
            var bottom = activeEdges & Qt.BottomEdge
            var width = Math.max(root.minimumWidth, start.width + (left ? -dx : right ? dx : 0))
            var height = Math.max(root.minimumHeight, start.height + (top ? -dy : bottom ? dy : 0))
            var x = left ? start.x + start.width - width : start.x
            var y = top ? start.y + start.height - height : start.y
            if (x !== root.x || y !== root.y || width !== root.width || height !== root.height)
                root.setGeometry(x, y, width, height)
        }

        function finish(point) {
            move(point)
            cancel()
        }

        function cancel() {
            if (!activeEdges) return
            activeEdges = 0
            root.windowResizing = false
        }

        onVisibleChanged: if (!visible) cancel()

        Connections {
            target: root
            function onActiveChanged() {
                if (!root.active) resizeFrame.cancel()
            }
        }

        Repeater {
            model: [
                { edges: Qt.LeftEdge, cursor: Qt.SizeHorCursor },
                { edges: Qt.RightEdge, cursor: Qt.SizeHorCursor },
                { edges: Qt.TopEdge, cursor: Qt.SizeVerCursor },
                { edges: Qt.BottomEdge, cursor: Qt.SizeVerCursor },
                { edges: Qt.TopEdge | Qt.LeftEdge, cursor: Qt.SizeFDiagCursor },
                { edges: Qt.TopEdge | Qt.RightEdge, cursor: Qt.SizeBDiagCursor },
                { edges: Qt.BottomEdge | Qt.LeftEdge, cursor: Qt.SizeBDiagCursor },
                { edges: Qt.BottomEdge | Qt.RightEdge, cursor: Qt.SizeFDiagCursor }
            ]
            delegate: MouseArea {
                required property int index
                required property var modelData
                objectName: "windowResizeGrip" + index
                readonly property bool leftEdge: (modelData.edges & Qt.LeftEdge) !== 0
                readonly property bool rightEdge: (modelData.edges & Qt.RightEdge) !== 0
                readonly property bool topEdge: (modelData.edges & Qt.TopEdge) !== 0
                readonly property bool bottomEdge: (modelData.edges & Qt.BottomEdge) !== 0
                x: leftEdge ? 0 : rightEdge ? resizeFrame.width - width : resizeFrame.grip
                y: topEdge ? 0 : bottomEdge ? resizeFrame.height - height : resizeFrame.grip
                width: leftEdge || rightEdge ? resizeFrame.grip : resizeFrame.width - 2 * resizeFrame.grip
                height: topEdge || bottomEdge ? resizeFrame.grip : resizeFrame.height - 2 * resizeFrame.grip
                hoverEnabled: true
                preventStealing: true
                cursorShape: modelData.cursor
                onPressed: function(mouse) {
                    resizeFrame.begin(modelData.edges, mapToGlobal(mouse.x, mouse.y))
                }
                onPositionChanged: function(mouse) {
                    if (pressed) resizeFrame.move(mapToGlobal(mouse.x, mouse.y))
                }
                onReleased: function(mouse) {
                    resizeFrame.finish(mapToGlobal(mouse.x, mouse.y))
                }
                onCanceled: resizeFrame.cancel()
            }
        }
    }
}
