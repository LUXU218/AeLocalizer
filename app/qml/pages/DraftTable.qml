import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import AeLocalizer
import "../components"

FocusScope {
    id: table
    property var backend

    readonly property int statusWidth: Math.max(74, Theme.fontSmall * 5 + 14)
    readonly property int rowHeight: Math.max(36, Theme.fontBody + Theme.spaceMd * 2)
    readonly property int cellSpace: Theme.spaceMd
    readonly property int sourceWidth: Math.max(72, Math.round((width - statusWidth - 4 * cellSpace) * 0.44))
    property Item activeEditor: null

    function commitActiveEdit() {
        if (activeEditor)
            activeEditor.commit()
    }

    function syncDrafts() {
        const drafts = backend ? backend.aiDraft : []
        if (rows.count > drafts.length)
            rows.remove(drafts.length, rows.count - drafts.length)
        for (let i = 0; i < drafts.length; ++i) {
            const draft = drafts[i]
            const entry = {
                original: draft.en,
                translation: draft.zh || "",
                result: draft.status || "",
                reason: draft.reason || ""
            }
            if (i === rows.count) {
                rows.append(entry)
            } else {
                const old = rows.get(i)
                if (old.original !== entry.original || old.translation !== entry.translation
                        || old.result !== entry.result || old.reason !== entry.reason)
                    rows.set(i, entry)
            }
        }
    }

    ListModel { id: rows }
    onBackendChanged: syncDrafts()

    Connections {
        target: table.backend
        function onAiDraftChanged() { table.syncDrafts() }
    }

    component TableText: Text {
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontBody
        elide: Text.ElideRight
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.spaceXs

        RowLayout {
            Layout.fillWidth: true

            TableText {
                text: "翻译草稿"
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            TableText {
                visible: rows.count > 0
                text: table.backend && table.backend.aiDraftCount > rows.count
                      ? "显示前 " + rows.count + " 条 · 共 " + table.backend.aiDraftCount + " 条"
                      : rows.count + " 条"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontSmall
            }
        }

        TableText {
            Layout.fillWidth: true
            visible: rows.count > 0 && table.backend && table.backend.aiDraftStale
            text: "插件勾选已变化，可重新翻译生成当前草稿。"
            color: Theme.warn
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            radius: Theme.radiusControl
            color: Theme.insetFill

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: table.cellSpace
                anchors.rightMargin: table.cellSpace
                spacing: table.cellSpace

                TableText {
                    Layout.preferredWidth: table.sourceWidth
                    text: "原文"
                    color: Theme.textSecondary
                    font.weight: Font.DemiBold
                }
                TableText {
                    Layout.fillWidth: true
                    text: "译文"
                    color: Theme.textSecondary
                    font.weight: Font.DemiBold
                }
                TableText {
                    Layout.preferredWidth: table.statusWidth
                    text: "状态"
                    color: Theme.textSecondary
                    font.weight: Font.DemiBold
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: list
                objectName: "translationDraftList"
                anchors.fill: parent
                clip: true
                model: rows
                boundsBehavior: Flickable.StopAtBounds
                Basic.ScrollBar.vertical: Basic.ScrollBar { policy: Basic.ScrollBar.AsNeeded }

                delegate: Rectangle {
                    id: row
                    required property int index
                    required property string original
                    required property string translation
                    required property string result
                    required property string reason

                    width: list.width
                    height: table.rowHeight
                    radius: Theme.radiusControl
                    color: editor.activeFocus ? Theme.selectionFill
                                              : (hover.hovered ? Theme.bgHover : "transparent")

                    HoverHandler { id: hover; cursorShape: Qt.ArrowCursor }

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 1
                        color: Theme.borderSoft
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: table.cellSpace
                        anchors.rightMargin: table.cellSpace
                        spacing: table.cellSpace

                        TableText {
                            Layout.preferredWidth: table.sourceWidth
                            text: row.original
                            Basic.ToolTip.visible: truncated && hover.hovered
                            Basic.ToolTip.delay: 600
                            Basic.ToolTip.text: text
                        }

                        TextInput {
                            id: editor
                            objectName: "draftTranslationInput"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Accessible.name: "译文 " + row.original
                            activeFocusOnTab: true
                            verticalAlignment: TextInput.AlignVCenter
                            color: Theme.textPrimary
                            selectionColor: Theme.accent
                            selectedTextColor: "#FFFFFF"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.fontBody
                            clip: true
                            selectByMouse: true
                            text: row.translation

                            function commit() {
                                if (!table.backend || text.trim() === row.translation || row.index < 0)
                                    return
                                table.backend.setDraftZh(row.index, text)
                            }

                            onEditingFinished: commit()
                            onActiveFocusChanged: {
                                if (activeFocus)
                                    table.activeEditor = editor
                                else if (table.activeEditor === editor)
                                    table.activeEditor = null
                            }

                            TableText {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width
                                visible: !editor.activeFocus && editor.text.length === 0
                                text: row.reason || "填写译文"
                                color: row.reason ? Theme.danger : Theme.textSecondary
                                font.pixelSize: Theme.fontSmall
                            }
                        }

                        Item {
                            Layout.preferredWidth: table.statusWidth
                            Layout.preferredHeight: status.implicitHeight

                            Badge {
                                id: status
                                text: ({
                                    ok: "通过",
                                    glossary_fixed: "术语修正",
                                    rejected: "被拒",
                                    untranslated: "未译"
                                })[row.result] || row.result
                                tone: row.result === "ok" || row.result === "glossary_fixed"
                                      ? Theme.success : Theme.danger
                            }
                        }
                    }
                }
            }

            Column {
                anchors.centerIn: parent
                width: parent.width
                visible: rows.count === 0
                spacing: Theme.spaceSm

                Item {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 60
                    height: 49
                    visible: table.height > 216
                    Rectangle {
                        x: 2; y: 5
                        width: 31; height: 32; radius: 6
                        rotation: -9
                        color: Theme.insetFill
                        border.color: Theme.controlStroke
                        Text {
                            anchors.centerIn: parent
                            text: "A"
                            font.family: "Segoe UI"
                            font.pixelSize: 18
                            color: Theme.textSecondary
                        }
                    }
                    Rectangle {
                        x: 25; y: 14
                        width: 32; height: 32; radius: 6
                        rotation: 5
                        color: Theme.actionFill
                        Text {
                            anchors.centerIn: parent
                            text: "译"
                            font.family: Theme.fontFamily
                            font.pixelSize: 16
                            color: Theme.actionText
                        }
                    }
                }

                TableText {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: "还没有翻译草稿"
                    font.pixelSize: Theme.fontTitle
                    font.weight: Font.DemiBold
                }
                TableText {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    text: "勾选插件后，点击「开始翻译」。"
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
