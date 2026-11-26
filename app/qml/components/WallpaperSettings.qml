import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import AeLocalizer

SettingCard {
    id: card
    objectName: "wallpaperSettings"
    property var backend: null
    readonly property string kind: backend ? backend.backgroundKind : "none"
    readonly property bool adjustable: ["image", "library-image", "scene", "video"].includes(kind)
    property bool expanded: false
    onAdjustableChanged: if (!adjustable) expanded = false
    title: "工作台壁纸"

    FileDialog {
        id: imagePicker
        title: "选择本地图片"
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.bmp)"]
        onAccepted: if (card.backend) card.backend.setBgImage(selectedFile.toString())
    }
    WallpaperPicker { id: library; backend: card.backend }

    RowLayout {
        Layout.fillWidth: true
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3
            Text {
                text: "当前背景"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontSmall
                color: Theme.textSecondary
            }
            Text {
                objectName: "wallpaperStatus"
                text: ({
                    "none": "使用主题底色", "image": "本地图片", "library-image": "壁纸库图片",
                    "video": "视频壁纸", "scene": "实时场景壁纸", "saved-scene": "已保存的场景壁纸",
                    "preparing": "正在准备壁纸…"
                })[card.kind]
                font.family: Theme.fontFamily
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
                color: Theme.textPrimary
            }
        }
        Button {
            objectName: "clearWallpaperButton"
            text: "清除壁纸"
            enabledButton: card.kind !== "none"
            onClicked: card.backend.clearWallpaperBackground()
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceSm
        Button {
            objectName: "wallpaperLibraryButton"
            text: "打开壁纸库"
            primary: true
            enabledButton: !!card.backend && card.backend.weAvailable
            onClicked: library.open()
        }
        Button {
            text: "选择本地图片"
            enabledButton: !!card.backend
            onClicked: imagePicker.open()
        }
        Item { Layout.fillWidth: true }
        Button {
            objectName: "wallpaperAdjustButton"
            visible: card.adjustable
            text: card.expanded ? "收起画面调节" : "画面调节"
            onClicked: card.expanded = !card.expanded
        }
    }
    Text {
        visible: !!card.backend && !card.backend.weAvailable
        text: "未检测到 Wallpaper Engine，可使用本地图片。"
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        color: Theme.textSecondary
    }

    ColumnLayout {
        visible: card.adjustable && card.expanded
        Layout.fillWidth: true
        spacing: Theme.spaceSm
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.borderSoft }
        SettingRow {
            title: "画面风格"
            Segmented {
                current: card.backend ? card.backend.currentBgPreset : ""
                options: [{key: "clear", label: "清晰"}, {key: "soft", label: "柔和"},
                          {key: "frosted", label: "磨砂"}, {key: "reading", label: "阅读优先"}]
                onPicked: function(key) { card.backend.applyBgPreset(key) }
            }
            Item { Layout.fillWidth: true }
        }

        component Adjustment: SettingRow {
            id: row
            required property real setting
            required property real low
            required property real high
            property real displayScale: 1
            property string unit: ""
            signal adjusted(real next)
            Slider {
                Layout.preferredWidth: 180
                value: row.setting
                min: row.low
                max: row.high
                onMoved: function(v) { row.adjusted(v) }
            }
            Text {
                Layout.preferredWidth: 48
                text: Math.round(row.setting * row.displayScale) + row.unit
                color: Theme.textSecondary
                font.family: Theme.monoFamily
                font.pixelSize: Theme.fontSmall
            }
        }
        Adjustment {
            title: "磨砂强度"; low: 0; high: 60
            setting: card.backend ? card.backend.bgBlur : 24
            onAdjusted: v => card.backend.setBgBlur(Math.round(v))
        }
        Adjustment {
            title: "亮度"; low: 50; high: 160; unit: "%"
            setting: card.backend ? card.backend.bgBrightness : 94
            onAdjusted: v => card.backend.setBgBrightness(Math.round(v))
        }
        Adjustment {
            title: "饱和度"; low: 0; high: 200; unit: "%"
            setting: card.backend ? card.backend.bgSaturation : 92
            onAdjusted: v => card.backend.setBgSaturation(Math.round(v))
        }
        Adjustment {
            title: "遮罩浓度"; low: 0.05; high: 0.95; unit: "%"; displayScale: 100
            setting: card.backend ? card.backend.bgDim : 0.22
            onAdjusted: v => card.backend.setBgDim(v)
        }
        Adjustment {
            title: "玻璃不透明度"; low: 0; high: 1; unit: "%"; displayScale: 100
            setting: card.backend ? card.backend.plate : 0.6
            onAdjusted: v => card.backend.setPlate(v)
        }
        Adjustment {
            title: "面板磨砂"; low: 0; high: 60
            visible: card.kind === "image" || card.kind === "library-image"
            setting: card.backend ? card.backend.glassBlur : 0
            onAdjusted: v => card.backend.setGlassBlur(Math.round(v))
        }
        SettingRow {
            title: "填充方式"
            visible: card.kind !== "scene"
            Segmented {
                current: card.backend ? card.backend.bgFit : "cover"
                options: [{key: "cover", label: "铺满裁切"}, {key: "contain", label: "完整显示"}]
                onPicked: function(key) { card.backend.setBgFit(key) }
            }
            Item { Layout.fillWidth: true }
        }
    }
}
