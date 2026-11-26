pragma Singleton
import QtQuick

QtObject {
    id: theme

    property string themeMode: "light"
    readonly property bool systemDark: Qt.styleHints.colorScheme === Qt.Dark
    property bool dark: themeMode === "dark" || (themeMode === "system" && systemDark)

    property string bgImage: ""
    property real bgDim: 0.85
    property string bgVideo: ""
    property string bgSceneUrl: ""
    property int bgSceneRev: 0

    property int bgBlur: 24
    property int bgBrightness: 100
    property int bgContrast: 100
    property int bgSaturation: 100
    property string bgFit: "cover"

    readonly property real bgBlurNorm: Math.max(0, Math.min(60, bgBlur)) / 60.0
    readonly property real bgBrightnessDelta: (bgBrightness - 100) / 100.0
    readonly property real bgContrastDelta: (bgContrast - 100) / 100.0
    readonly property real bgSaturationDelta: (bgSaturation - 100) / 100.0

    readonly property bool bgNeedsEffect: bgBlur > 0 || bgBrightness !== 100
                                        || bgSaturation !== 100 || bgContrast !== 100

    property string accentKey: "blue"
    property string density: "normal"
    property string fontScale: "normal"
    property bool reduceMotion: false
    property real plate: 0.85

    readonly property var accentPalettes: ({
        "blue":   { light: "#2563EB", lightHover: "#1D4ED8", lightSoft: "#EFF4FF",
                    dark:  "#5B93FF", darkHover:  "#7CA9FF", darkSoft:  "#1D2E4E" },
        "violet": { light: "#7C3AED", lightHover: "#6D28D9", lightSoft: "#F3EEFF",
                    dark:  "#A78BFA", darkHover:  "#C4B5FD", darkSoft:  "#2A2143" },
        "teal":   { light: "#0D9488", lightHover: "#0F766E", lightSoft: "#E6FAF7",
                    dark:  "#2DD4BF", darkHover:  "#5EEAD4", darkSoft:  "#123B38" },
        "amber":  { light: "#B45309", lightHover: "#92400E", lightSoft: "#FEF3E2",
                    dark:  "#F5AE31", darkHover:  "#FBBF24", darkSoft:  "#3B2E17" },
        "rose":   { light: "#DB2777", lightHover: "#BE185D", lightSoft: "#FDECF5",
                    dark:  "#F472B6", darkHover:  "#FB7185", darkSoft:  "#3C1B2B" },
        "slate":  { light: "#475569", lightHover: "#334155", lightSoft: "#EEF2F6",
                    dark:  "#94A3B8", darkHover:  "#CBD5E1", darkSoft:  "#232C38" }
    })
    readonly property var accentPal: accentPalettes[accentKey] !== undefined
                                     ? accentPalettes[accentKey]
                                     : accentPalettes["blue"]

    // Native equivalents of the coss ui semantic palette (coss.com/ui/docs/styling).
    readonly property color bgBase:     dark ? "#161616" : "#ffffff"
    readonly property color bgSurface:  dark ? "#1b1b1b" : "#ffffff"
    readonly property color bgElevated: dark ? "#242424" : "#ffffff"
    readonly property color bgSide:     dark ? "#111111" : "#fafafa"
    readonly property color workspaceFill: bgBase
    readonly property color titlebarFill: hasAnyBg ? chromeFill : bgSide
    readonly property color insetFill: hasAnyBg ? cardFill : (dark ? "#262626" : "#f5f5f5")
    readonly property color popupFill: hasCustomText ? (darkGlass ? "#242424" : "#ffffff") : bgElevated
    readonly property color selectedControlFill: hasAnyBg ? wallpaperTint(0.50 + 0.38 * plate) : popupFill
    readonly property color selectionFill: hasAnyBg ? selectedControlFill : accentKey !== "blue" ? accentSoft : (dark ? "#303030" : "#ebebeb")
    readonly property color controlStroke: dark ? "#454545" : "#d9d9d9"
    readonly property color actionFill: dark ? "#f5f5f5" : "#262626"
    readonly property color actionHover: dark ? "#dedede" : "#414141"
    readonly property color actionText: dark ? "#262626" : "#fafafa"
    readonly property color focusRing: accentKey !== "blue" ? accent : (dark ? "#a3a3a3" : "#737373")
    readonly property string iconFamily: "Segoe MDL2 Assets"
    readonly property color bgHover:    hasAnyBg ? wallpaperTint(0.30 + 0.40 * plate)
                                       : (dark ? "#292929" : "#f4f4f4")
    readonly property color bgActive:   hasAnyBg ? wallpaperTint(0.40 + 0.43 * plate)
                                       : (dark ? "#333333" : "#ebebeb")
    readonly property color border:     dark ? "#363636" : "#e4e4e4"
    readonly property color borderSoft: dark ? "#2b2b2b" : "#ededed"
    readonly property color panelStroke: hasAnyBg ? "transparent" : border

    property color textCustom: "transparent"
    readonly property bool hasCustomText: hasAnyBg && textCustom.a > 0.0

    readonly property real textLum: 0.2126 * textCustom.r
                                   + 0.7152 * textCustom.g
                                   + 0.0722 * textCustom.b
    // 壁纸上的浅色自定义字体使用暗色玻璃底。
    readonly property bool darkGlass: hasCustomText && textLum > 0.55

    readonly property color textPrimary:   hasCustomText ? textCustom
                                           : (dark ? "#f5f5f5" : "#262626")
    readonly property color textSecondary: hasCustomText
            ? Qt.rgba(textCustom.r, textCustom.g, textCustom.b, 0.80)
            : (dark ? "#b4b4b4" : "#626262")
    readonly property color textTertiary: hasCustomText
            ? Qt.rgba(textCustom.r, textCustom.g, textCustom.b, 0.62)
            : (dark ? "#999999" : "#707070")

    readonly property color textOnBg: textPrimary
    readonly property color textOnBgSecondary: textSecondary
    readonly property color textOnBgTertiary: textTertiary

    // 壁纸模式下，填充浓度跟随玻璃不透明度设置。
    function wallpaperTint(alpha: real): color {
        return Qt.rgba(popupFill.r, popupFill.g, popupFill.b, alpha)
    }
    readonly property color fieldFill: hasAnyBg ? wallpaperTint(0.18 + 0.35 * plate) : bgBase
    readonly property color panelFill: hasAnyBg ? wallpaperTint(0.22 + 0.48 * plate) : bgSurface
    readonly property color cardFill: hasAnyBg ? panelFill : "transparent"
    readonly property color chromeFill: hasAnyBg ? wallpaperTint(0.16 + 0.46 * plate) : bgSide
    property int glassBlur: 0
    readonly property real glassBlurNorm: Math.max(0, Math.min(60, glassBlur)) / 60.0
    readonly property int glassExtraMax: 12
    readonly property int bgBlurMax: 96
    property var backdropItem: null

    readonly property color bgElevatedGlass: !hasAnyBg ? bgElevated
            : (darkGlass ? Qt.rgba(0.03, 0.04, 0.06, 0.88)
                         : Qt.rgba(bgElevated.r, bgElevated.g, bgElevated.b, 0.72))

    readonly property color accent:      dark ? accentPal.dark      : accentPal.light
    readonly property color accentHover: dark ? accentPal.darkHover : accentPal.lightHover
    readonly property color accentSoft:  dark ? accentPal.darkSoft  : accentPal.lightSoft
    readonly property color success:     dark ? "#6ee7b7" : "#047857"
    readonly property color warn:        dark ? "#fcd34d" : "#92400e"
    readonly property color danger:      dark ? "#fca5a5" : "#b91c1c"

    property string uiFont: ""
    readonly property string fontDefault: "Microsoft YaHei UI"
    readonly property string fontFamily: uiFont !== "" ? uiFont : fontDefault
    readonly property string monoFamily: "Cascadia Mono"
    readonly property int fontSmall: fontScale === "small" ? 11
                                   : fontScale === "large" ? 13 : 12
    readonly property int fontBody:  fontScale === "small" ? 12
                                   : fontScale === "large" ? 15 : 13
    readonly property int fontTitle: fontScale === "small" ? 14
                                   : fontScale === "large" ? 17 : 15
    readonly property int fontHeading: fontScale === "small" ? 18
                                     : fontScale === "large" ? 22 : 20
    readonly property int fontMetric: fontScale === "small" ? 24
                                    : fontScale === "large" ? 28 : 26

    readonly property bool dense: density === "compact"
    readonly property bool roomy: density === "relaxed"
    readonly property int radiusControl: 7
    readonly property int radiusCard: 10
    readonly property int radiusDialog: 14
    readonly property int spaceXs: dense ? 3 : roomy ? 5 : 4
    readonly property int spaceSm: dense ? 6 : roomy ? 10 : 8
    readonly property int spaceMd: dense ? 9 : roomy ? 15 : 12
    readonly property int spaceLg: dense ? 12 : roomy ? 20 : 16
    readonly property int spaceXl: dense ? 18 : roomy ? 30 : 24
    readonly property int spaceXxl: dense ? 24 : roomy ? 40 : 32

    readonly property int durFast: reduceMotion ? 0 : 120
    readonly property int durNormal: reduceMotion ? 0 : 180
    readonly property int durSlow: reduceMotion ? 0 : 240
    readonly property int durPage: reduceMotion ? 0 : 320

    readonly property bool hasAnyBg: bgImage !== "" || bgVideo !== "" || bgSceneUrl !== ""
    readonly property bool bgIsVideo: bgVideo !== ""
    readonly property bool bgIsScene: bgSceneUrl !== ""
}
