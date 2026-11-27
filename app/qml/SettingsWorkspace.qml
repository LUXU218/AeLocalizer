import QtQuick
import QtQuick.Layouts
import AeLocalizer
import "pages"
import "components"

Window {
    id: root
    objectName: "detachedSettingsWindow"
    title: "AeLocalizer · 设置"
    minimumWidth: 660
    minimumHeight: 460
    width: 780
    height: 620
    visible: true
    color: Theme.bgBase
    property bool handingOff: false

    function openWorkbench(section) {
        handingOff = true
        settingsTransport.request("workbench", section)
        close()
    }

    onClosing: if (!handingOff) settingsTransport.request("close", page.section)

    QtObject {
        id: preferences
        readonly property var state: settingsTransport.snapshot.preferences || ({})
        readonly property string uiTheme: state.uiTheme || "light"
        readonly property string accentKey: state.accentKey || "blue"
        readonly property string density: state.density || "normal"
        readonly property string fontScale: state.fontScale || "normal"
        readonly property string uiFont: state.uiFont || ""
        readonly property string textColor: state.textColor || ""
        readonly property bool reduceMotion: state.reduceMotion === true
        readonly property bool autoScan: state.autoScan === true
        readonly property bool startupWithAe: state.startupWithAe === true
        readonly property bool hasWallpaper: state.hasWallpaper === true
        readonly property string appVersion: state.appVersion || ""

        function setUiTheme(value) { settingsTransport.request("theme", value) }
        function setAccentKey(value) { settingsTransport.request("accent", value) }
        function setDensity(value) { settingsTransport.request("density", value) }
        function setFontScale(value) { settingsTransport.request("font_scale", value) }
        function setUiFont(value) { settingsTransport.request("font", value) }
        function setTextColor(value) { settingsTransport.request("text_color", value) }
        function setReduceMotion(value) { settingsTransport.request("motion", value) }
        function resetAppearance() { settingsTransport.request("reset", null) }
        function setStartupWithAe(value) { settingsTransport.request("startup", value) }
        function setAutoScan(value) { settingsTransport.request("auto_scan", value) }
        function systemFonts() { return settingsTransport.systemFonts() }
    }

    function syncTheme() {
        Theme.themeMode = preferences.uiTheme
        Theme.accentKey = preferences.accentKey
        Theme.density = preferences.density
        Theme.fontScale = preferences.fontScale
        Theme.uiFont = preferences.uiFont
        Theme.textCustom = preferences.textColor || "transparent"
        Theme.reduceMotion = preferences.reduceMotion
    }

    Component.onCompleted: syncTheme()
    Connections {
        target: settingsTransport
        function onUpdated() {
            root.syncTheme()
            page.syncDetachedColor()
        }
        function onActivated() {
            if (root.visibility === Window.Minimized)
                root.showNormal()
            root.raise()
            root.requestActivate()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceMd
        spacing: Theme.spaceSm

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                color: Theme.textSecondary
                text: "壁纸、数据、更新和诊断将在完整工作台中打开。"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.Wrap
            }
            Button {
                text: "打开完整工作台"
                onClicked: root.openWorkbench(page.section)
            }
        }

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: settingsTransport.snapshot.notice || ""
            color: Theme.warn
            font.family: Theme.fontFamily
            wrapMode: Text.Wrap
        }
        SettingsPage {
            id: page
            objectName: "detachedSettingsPage"
            Layout.fillWidth: true
            Layout.fillHeight: true
            detached: true
            backend: preferences
            Component.onCompleted: section = settingsTransport.snapshot.section || 0
            onOpenWorkbench: section => root.openWorkbench(section)
        }
    }
}
