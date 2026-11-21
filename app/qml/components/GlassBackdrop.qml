import QtQuick
import QtQuick.Effects
import AeLocalizer

Item {
    id: glass

    anchors.fill: parent
    visible: Theme.bgImage !== "" && Theme.glassBlur > 0 && Theme.backdropItem !== null
             && !Theme.bgIsVideo && !Theme.bgIsScene
    property real radius: 0

    // 先裁剪合成背景，再对采样区域模糊。
    ShaderEffectSource {
        id: crop
        anchors.fill: parent
        sourceItem: Theme.backdropItem
        live: glass.visible
        visible: false
        sourceRect: {
            if (!glass.visible) return Qt.rect(0, 0, 1, 1)
            var p = crop.mapToItem(null, 0, 0)
            return Qt.rect(Math.round(p.x), Math.round(p.y),
                           Math.max(1, Math.round(glass.width)),
                           Math.max(1, Math.round(glass.height)))
        }
    }

    Rectangle {
        id: cornerMask
        anchors.fill: parent
        radius: glass.radius
        color: "#FFFFFF"
        visible: false
        layer.enabled: glass.radius > 0
    }

    MultiEffect {
        anchors.fill: parent
        source: crop
        blurEnabled: true
        blur: Theme.glassBlurNorm
        blurMax: Theme.glassExtraMax
        maskEnabled: glass.radius > 0
        maskSource: cornerMask
    }
}
