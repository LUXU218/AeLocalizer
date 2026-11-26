import QtQuick
import QtQuick.Effects
import AeLocalizer

Loader {
    id: glass
    anchors.fill: parent
    property real radius: 0

    // Video and live scenes show through the tint directly, with no panel texture.
    active: visible && Theme.bgImage !== "" && !Theme.bgIsVideo && !Theme.bgIsScene
            && Theme.glassBlur > 0 && Theme.backdropItem !== null
            && GraphicsInfo.api !== GraphicsInfo.Software

    // Panel and wallpaper ancestors use layout translations. Reading each offset also
    // subscribes the crop to layout moves and Flickable.contentItem scrolling.
    function windowOrigin(item: Item): point {
        let x = 0, y = 0
        for (let ancestor = item; ancestor; ancestor = ancestor.parent) {
            x += ancestor.x
            y += ancestor.y
        }
        return Qt.point(x, y)
    }

    sourceComponent: Item {
        ShaderEffectSource {
            id: crop
            anchors.fill: parent
            sourceItem: Theme.backdropItem
            visible: false
            sourceRect: {
                const panel = glass.windowOrigin(glass)
                const wallpaper = glass.windowOrigin(sourceItem)
                return Qt.rect(panel.x - wallpaper.x, panel.y - wallpaper.y,
                               Math.max(1, width), Math.max(1, height))
            }
        }

        Rectangle {
            id: cornerMask
            anchors.fill: parent
            radius: glass.radius
            color: "white"
            visible: false
            layer.enabled: glass.radius > 0
        }
        MultiEffect {
            anchors.fill: parent
            source: crop
            autoPaddingEnabled: false
            blurEnabled: true
            blur: Theme.glassBlurNorm
            blurMax: Theme.glassExtraMax
            maskEnabled: glass.radius > 0
            maskSource: cornerMask
        }
    }
}
