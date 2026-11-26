import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import AeLocalizer
// 同目录控件在 QtQuick.Controls 之后导入。
import "."

Rectangle {
    id: details
    property var backend: null
    property var selected: null
    property var weProps: []
    signal applyRequested()
    function loadSelection() {
        propMsg = ""
        weProps = selected && backend ? backend.weProps(selected.id) : []
        tune = selected && backend ? backend.weTune(selected.id) : ({})
    }
    onSelectedChanged: loadSelection()
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
    function tuneSet(key, val) {
        if (!backend || !selected) return
        tune = backend.setWeTune(selected.id, key, val)
    }


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
            text: details.selected ? details.selected.title : "壁纸详情"
            Layout.fillWidth: true
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            text: details.selected
                  ? "画面调节按壁纸单独保存。"
                  : "从左侧列表选择壁纸。"
            color: Theme.textTertiary
            font.family: Theme.fontFamily
            font.pixelSize: 10
            wrapMode: Text.WordWrap
            lineHeight: 1.3
        }

        Text {
            Layout.fillWidth: true
            visible: details.selected !== null && details.backend && details.backend.weEngineRunning
            text: "壁纸引擎正在运行：改动会先记下来，等它关闭后本工具再写一次。"
            color: Theme.warn
            font.family: Theme.fontFamily
            font.pixelSize: 10
            wrapMode: Text.WordWrap
            lineHeight: 1.3
        }

        Flickable {
            visible: details.selected !== null
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
                    visible: details.selected !== null
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
                                    text: (details.tune && details.tune[modelData.key] !== undefined
                                           ? details.tune[modelData.key] : "-") + modelData.suffix
                                    color: Theme.textTertiary
                                    font.family: Theme.monoFamily
                                    font.pixelSize: 11
                                    Layout.preferredWidth: 52
                                    Layout.rightMargin: 4
                                    horizontalAlignment: Text.AlignRight
                                }
                            }
                            Slider {
                                Layout.fillWidth: true
                                min: modelData.min
                                max: modelData.max
                                value: details.tune && details.tune[modelData.key] !== undefined
                                       ? details.tune[modelData.key] : min
                                onMoved: function(v) {
                                    details.tuneSet(modelData.key, Math.round(v))
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
                            current: details.tune && details.tune.fit ? details.tune.fit : "cover"
                            options: [
                                { key: "cover",   label: "铺满" },
                                { key: "contain", label: "完整" }
                            ]
                            onPicked: function(key) { details.tuneSet("fit", key) }
                        }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

                Text {
                    Layout.fillWidth: true
                    text: "壁纸自带参数 " + details.weProps.length
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: details.weProps.length > 0
                          ? "实时渲染此场景时可以预览参数改动。"
                          : "这张壁纸没有声明自己的参数。"
                    color: Theme.textTertiary
                    font.family: Theme.fontFamily
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                    lineHeight: 1.3
                }

                Repeater {
                    model: details.weProps
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
                                    onClicked: details.propSet(modelData.name,
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
                                    text: details.comboLabel(modelData)
                                    color: Theme.textSecondary
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 10
                                    elide: Text.ElideRight
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: details.comboNext(modelData)
                                }
                            }

                            Rectangle {
                                visible: modelData.type === "color"
                                implicitWidth: 18
                                implicitHeight: 18
                                radius: 4
                                color: modelData.type === "color"
                                       ? details.colorSwatch(modelData.effective) : "transparent"
                                border.width: 1
                                border.color: Theme.borderSoft
                            }
                            TextInput {
                                visible: modelData.type === "color"
                                Layout.preferredWidth: 78
                                text: modelData.type === "color"
                                      ? details.colorSwatch(modelData.effective) : ""
                                color: Theme.textSecondary
                                selectionColor: Theme.accent
                                selectedTextColor: "#FFFFFF"
                                font.family: Theme.monoFamily
                                font.pixelSize: 10
                                clip: true
                                onEditingFinished: details.propSet(modelData.name, text)
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
                                onEditingFinished: details.propSet(modelData.name, text)
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
                            visible: modelData.type === "slider"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 16
                            min: modelData.min !== undefined ? modelData.min : 0
                            max: modelData.max !== undefined ? modelData.max : 1
                            value: modelData.effective !== undefined && modelData.type === "slider"
                                   ? Number(modelData.effective) : min
                            onMoved: function(v) { details.propSet(modelData.name, v) }
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

        Item { Layout.fillHeight: true; visible: details.selected === null }
        Text {
            Layout.fillWidth: true
            visible: details.propMsg !== ""
            text: details.propMsg
            color: Theme.warn
            font.family: Theme.fontFamily
            font.pixelSize: 10
            wrapMode: Text.WordWrap
            lineHeight: 1.25
        }

        Button {
            Layout.fillWidth: true
            objectName: "applySelectedWallpaper"
            text: details.selected && details.selected.current ? "正在使用" : "设为壁纸"
            primary: details.selected !== null && !(details.selected && details.selected.current)
            enabledButton: details.backend && details.selected !== null
                            && !details.selected.current && !details.backend.busy
            onClicked: details.applyRequested()
        }
    }
}
