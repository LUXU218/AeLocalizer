import QtQuick
import QtQuick.Layouts
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
        { label: "翻译", icon: "\uE8C1" },
        { label: "效果名", icon: "\uE81E" },
        { label: "引擎", icon: "\uE8AB" },
        { label: "补词", icon: "\uE82D" },
        { label: "设置", icon: "\uE713" },
        { label: "面板与脚本", icon: "\uE8F1" }
    ]

    // 窗口保留少量向内缩放余量，并限制在屏幕工作区内。
    function initialExtent(available, minimum, ratio) {
        var withResizeRoom = Math.min(minimum + 40, available - 16)
        return Math.max(minimum, withResizeRoom, Math.round(available * ratio))
    }

    Component.onCompleted: {
        if (hasStartupArea) {
            x = startupArea.x + Math.floor((startupArea.width - width) / 2)
            y = startupArea.y + Math.floor((startupArea.height - height) / 2)
            visible = true
        }
        if (!backend) return
        Theme.themeMode = backend.uiTheme
        Theme.bgImage = backend.bgImage
        Theme.bgDim = backend.bgDim
        Theme.bgVideo = backend.bgVideo
        Theme.bgSceneUrl = backend.bgSceneUrl
        Theme.bgSceneRev = backend.bgSceneRev
        Theme.accentKey = backend.accentKey
        Theme.density = backend.density
        Theme.fontScale = backend.fontScale
        Theme.reduceMotion = backend.reduceMotion
        Theme.plate = backend.plate
        Theme.uiFont = backend.uiFont
        Theme.textCustom = backend.textColor !== "" ? backend.textColor : "transparent"
        Theme.glassBlur = backend.glassBlur
    }

    Connections {
        target: backend
        function onUiThemeChanged() { if (backend) Theme.themeMode = backend.uiTheme }
        function onBgChanged() {
            if (!backend) return
            Theme.bgImage = backend.bgImage
            Theme.bgDim = backend.bgDim
            Theme.bgVideo = backend.bgVideo
        }
        function onVideoBgChanged() {
            if (!backend) return
            Theme.bgVideo = backend.bgVideo
            Theme.bgSceneUrl = backend.bgSceneUrl
            Theme.bgSceneRev = backend.bgSceneRev
            Theme.glassBlur = backend.glassBlur
            Theme.bgBlur = backend.bgBlur
            Theme.bgBrightness = backend.bgBrightness
            Theme.bgSaturation = backend.bgSaturation
            Theme.bgContrast = backend.bgContrast
            Theme.bgFit = backend.bgFit
            Theme.bgDim = backend.bgDim
            Theme.plate = backend.plate
        }
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

    Item {
        id: bgLayer
        anchors.fill: parent

        Item {
            id: bgSource
            anchors.fill: parent

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
                fillMode: Theme.bgFit === "contain" ? Image.PreserveAspectFit
                                                    : Image.PreserveAspectCrop
                asynchronous: true
            }
        }

        MultiEffect {
            anchors.fill: parent
            source: bgSource
            visible: Theme.hasAnyBg && Theme.bgNeedsEffect
            blurEnabled: Theme.bgBlur > 0
            blur: Theme.bgBlurNorm
            blurMax: Theme.bgBlurMax
            saturation: Theme.bgSaturationDelta
            brightness: Theme.bgBrightnessDelta
            contrast: Theme.bgContrastDelta
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
        height: 40
        GlassBackdrop { anchors.fill: parent; z: -1 }
        color: Theme.titlebarFill

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

            Text {
                text: root.title
                color: Theme.textOnBg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontBody
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
            width: Math.max(128, Math.min(172, 128 + (root.width - 900) / 4))
            height: parent.height
            GlassBackdrop { anchors.fill: parent; z: -1 }
            color: Theme.chromeFill

            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: Theme.borderSoft
            }

            Column {
                id: navigation
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Theme.spaceSm
                anchors.topMargin: Theme.spaceLg
                spacing: Theme.spaceXs

                Repeater {
                    model: root.navModel

                    delegate: Item {
                        id: navItem
                        readonly property bool selected: root.pageIndex === index
                        width: navigation.width
                        height: Theme.dense ? 34 : 39
                        activeFocusOnTab: true
                        Accessible.role: Accessible.Button
                        Accessible.name: modelData.label
                        Accessible.onPressAction: root.pageIndex = index
                        Keys.onReturnPressed: root.pageIndex = index
                        Keys.onSpacePressed: root.pageIndex = index

                        Rectangle {
                            anchors.fill: parent
                            radius: Theme.radiusControl
                            color: navItem.selected ? Theme.selectionFill
                                   : (navMouse.containsMouse || navItem.activeFocus ? Theme.bgHover : "transparent")
                        }

                        Row {
                            anchors.left: parent.left
                            anchors.leftMargin: Theme.spaceMd
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: Theme.spaceMd

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.icon
                                color: navItem.selected ? Theme.textOnBg : Theme.textOnBgSecondary
                                font.family: Theme.iconFamily
                                font.pixelSize: 15
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.label
                                color: navItem.selected ? Theme.textOnBg : Theme.textOnBgSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.fontBody
                                font.weight: navItem.selected ? Font.DemiBold : Font.Normal
                            }
                        }

                        MouseArea {
                            id: navMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                navItem.forceActiveFocus()
                                root.pageIndex = index
                            }
                        }
                    }
                }
            }

            Item {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: Theme.spaceMd
                height: 43

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    height: 1
                    color: Theme.borderSoft
                }

                Column {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    spacing: 3

                    Row {
                        spacing: 6
                        Rectangle {
                            width: 6; height: 6; radius: 3
                            anchors.verticalCenter: parent.verticalCenter
                            color: backend && backend.engineRunning ? Theme.success : Theme.textTertiary
                        }
                        Text {
                            text: backend && backend.engineRunning ? "引擎运行中" : "引擎未运行"
                            color: Theme.textOnBgSecondary
                            font.family: Theme.fontFamily
                            font.pixelSize: 11
                        }
                    }
                    Text {
                        width: parent.width
                        visible: backend && backend.engineRunning
                        text: backend ? "词典 " + backend.engineDictEntries + " 条" : ""
                        elide: Text.ElideRight
                        color: Theme.textSecondary
                        font.family: Theme.monoFamily
                        font.pixelSize: 10
                    }
                }
            }
        }

        StackLayout {
            width: parent.width - side.width
            height: parent.height
            currentIndex: root.pageIndex

            PageShell {
                title: "翻译"
                AiPage { anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "效果名"
                NamesPage { anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "引擎"
                EnginePage { anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "补词"
                CapturePage { anchors.fill: parent; backend: root.backend }
            }
            PageShell {
                title: "设置"
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
