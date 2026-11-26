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
    objectName: "wallpaperLibrary"

    parent: Overlay.overlay
    modal: true
    dim: true
    padding: 0
    width: Math.min(1010, (parent ? parent.width : 800) - 36)
    height: Math.min(600, (parent ? parent.height : 600) - 60)
    anchors.centerIn: Overlay.overlay

    background: Rectangle {
        color: Theme.popupFill
        radius: Theme.radiusDialog
        border.width: 1
        border.color: Theme.border
    }

    function refilter() {
        var keyword = query.trim().toLowerCase()
        var matches = items.filter(w => (kindFilter === "all" || w.kind === kindFilter)
                                     && String(w.title).toLowerCase().includes(keyword))
        filtered = matches
        var previousId = selected ? selected.id : ""
        selected = matches.find(w => w.id === previousId)
                   || matches.find(w => w.current) || matches[0] || null
    }

    function applySelected() {
        if (!backend || backend.busy || !selected || selected.current) return
        backend.applyWallpaper(selected.id)
        close()
    }

    onOpened: {
        searchInput.text = ""
        kindFilter = "all"
        selected = null
        items = backend ? backend.weWallpapers() : []
        refilter()
        if (selected) list.positionViewAtIndex(filtered.indexOf(selected), ListView.Contain)
    }

    Connections {
        target: picker.backend
        function onBgChanged() {
            if (picker.opened) picker.items = picker.backend.weWallpapers()
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
                    text: "壁纸库"
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
                        onClicked: picker.selected = modelData
                        onDoubleClicked: {
                            picker.selected = modelData
                            picker.applySelected()
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

            WallpaperDetails {
                Layout.preferredWidth: Math.min(380, Math.max(280, picker.width * 0.38))
                backend: picker.backend
                selected: picker.selected
                onApplyRequested: picker.applySelected()
            }
        }
    }

    function checkInstall() {
        if (!backend) return ""
        if (!backend.weAvailable) return "没找到壁纸引擎安装 —— 需要 Steam 版 Wallpaper Engine"
        if (backend.weError) return backend.weError
        return "Wallpaper Engine 本地壁纸"
    }

    function kindLabel(kind) {
        if (kind === "video") return "视频"
        if (kind === "scene") return "场景"
        if (kind === "web") return "网页"
        if (kind === "image") return "图片"
        return kind || "未知"
    }
}
