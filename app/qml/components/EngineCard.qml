import QtQuick
import QtQuick.Templates as Templates
import QtQuick.Layouts
import AeLocalizer

ColumnLayout {
    id: card

    property var backend: null
    property var warnings: []
    property var compatibilityIssues: []
    property bool effectEnabled: true
    property bool languageEnabled: true

    readonly property bool running: !!backend && backend.engineRunning
    readonly property bool busy: !!backend && backend.busy

    spacing: Theme.spaceMd

    function refreshChannels() {
        if (!backend)
            return
        const states = backend.channelState()
        if (states && states.length === 3) {
            effectEnabled = states[0]
            // 参数名和下拉值共用语言开关，后端同步更新两者。
            languageEnabled = states[1]
        }
    }

    function refreshChecks() {
        if (!backend)
            return
        const preflight = backend.preflight() || []
        warnings = preflight.filter(function(issue) {
            return issue && (!issue.ok || issue.warn)
        })
        compatibilityIssues = backend.compatIssues() || []
    }

    Component.onCompleted: {
        refreshChannels()
        refreshChecks()
    }
    onVisibleChanged: if (visible) {
        refreshChannels()
        refreshChecks()
    }

    Connections {
        target: card.backend
        function onStateChanged() { card.refreshChannels() }
        function onInventoryChanged() {
            card.refreshChannels()
            card.refreshChecks()
        }
    }

    component Panel: Rectangle {
        default property alias content: panelBody.data

        Layout.fillWidth: true
        implicitHeight: panelBody.implicitHeight + Theme.spaceMd * 2
        radius: Theme.radiusCard
        color: Theme.panelFill
        border.width: 1
        border.color: Theme.hasAnyBg ? "transparent" : Theme.border

        ColumnLayout {
            id: panelBody
            anchors.fill: parent
            anchors.leftMargin: Theme.spaceLg
            anchors.rightMargin: Theme.spaceLg
            anchors.topMargin: Theme.spaceMd
            anchors.bottomMargin: Theme.spaceMd
            spacing: Theme.spaceSm
        }
    }

    component SectionLabel: Text {
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontTitle
        font.weight: Font.DemiBold
    }

    component BodyText: Text {
        color: Theme.textSecondary
        font.family: Theme.fontFamily
        font.pixelSize: Theme.fontSmall
        wrapMode: Text.Wrap
    }

    component ChannelSwitch: Templates.Switch {
        id: control

        Layout.fillWidth: true
        padding: Theme.spaceSm
        rightPadding: switchIndicator.implicitWidth + Theme.spaceLg
        implicitHeight: Math.max(44, contentItem.implicitHeight + topPadding + bottomPadding)
        hoverEnabled: true
        activeFocusOnTab: true

        background: Rectangle {
            radius: Theme.radiusControl
            color: control.hovered ? Theme.insetFill : "transparent"
            border.width: control.visualFocus ? 1 : 0
            border.color: Theme.accent
        }

        contentItem: Text {
            text: control.text
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: Theme.fontBody
            font.weight: Font.DemiBold
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.Wrap
        }

        indicator: Row {
            id: switchIndicator

            x: control.width - width - Theme.spaceSm
            y: Math.round((control.height - height) / 2)
            width: implicitWidth
            height: implicitHeight
            spacing: Theme.spaceSm

            BodyText {
                anchors.verticalCenter: parent.verticalCenter
                text: control.checked ? "已开启" : "已关闭"
                wrapMode: Text.NoWrap
            }

            Rectangle {
                width: 36
                height: 20
                radius: height / 2
                anchors.verticalCenter: parent.verticalCenter
                color: control.checked ? Theme.actionFill : Theme.insetFill
                border.width: control.checked ? 0 : 1
                border.color: Theme.controlStroke

                Rectangle {
                    width: 12
                    height: width
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    x: control.checked ? parent.width - width - 4 : 4
                    color: control.checked ? Theme.actionText : Theme.textSecondary

                    Behavior on x {
                        NumberAnimation { duration: Theme.durFast }
                    }
                }
            }
        }
    }

    Panel {
        GridLayout {
            Layout.fillWidth: true
            columnSpacing: Theme.spaceLg
            rowSpacing: Theme.spaceMd
            columns: width >= 550 ? 2 : 1

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceXs

                Flow {
                    Layout.fillWidth: true
                    spacing: Theme.spaceSm

                    SectionLabel { text: "运行时引擎" }

                    Rectangle {
                        width: runStatus.implicitWidth + Theme.spaceSm * 2
                        height: Math.max(22, runStatus.implicitHeight + Theme.spaceXs)
                        radius: Theme.radiusControl
                        color: card.running ? Theme.selectionFill : Theme.insetFill

                        Row {
                            id: runStatus
                            anchors.centerIn: parent
                            spacing: Theme.spaceXs

                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 6
                                height: 6
                                radius: 3
                                color: card.running ? Theme.success : Theme.textTertiary
                            }

                            BodyText {
                                objectName: "engineRunningStatus"
                                text: card.running ? "运行中" : "未运行"
                                color: Theme.textPrimary
                            }
                        }
                    }
                }

            }

            RowLayout {
                Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                spacing: Theme.spaceSm

                Button {
                    objectName: "engineLogAction"
                    text: "打开日志"
                    enabledButton: !!card.backend
                    onClicked: card.backend.openEngineLog()
                }

                Button {
                    objectName: "engineStopAction"
                    text: "停止引擎"
                    danger: true
                    enabledButton: card.running
                    onClicked: card.backend.stopEngine()
                }

                Button {
                    objectName: "engineStartAction"
                    text: card.running ? "重启引擎" : "启动引擎"
                    primary: true
                    enabledButton: !!card.backend && !card.busy
                    onClicked: card.backend.startEngine()
                }
            }
        }

        BodyText {
            objectName: "engineDictionaryDiagnostic"
            Layout.fillWidth: true
            visible: text.length > 0
            text: card.backend ? card.backend.engineDictDiag : ""
            color: Theme.warn
        }
    }

    Panel {
        SectionLabel { text: "汉化通道" }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: channelRows.implicitHeight + 2
            radius: Theme.radiusControl
            color: "transparent"
            border.width: 1
            border.color: Theme.hasAnyBg ? "transparent" : Theme.border

            ColumnLayout {
                id: channelRows

                anchors.fill: parent
                anchors.margins: 1
                spacing: 0

                ChannelSwitch {
                    objectName: "engineEffectSwitch"
                    text: "效果名"
                    enabled: !!card.backend
                    checked: card.effectEnabled

                    onToggled: {
                        card.effectEnabled = checked
                        card.backend.setChannelAuto("effect", checked)
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.spaceSm
                    Layout.rightMargin: Theme.spaceSm
                    implicitHeight: 1
                    color: Theme.border
                }

                ChannelSwitch {
                    objectName: "engineLanguageSwitch"
                    text: "参数名与下拉值"
                    enabled: !!card.backend
                    checked: card.languageEnabled

                    onToggled: {
                        card.languageEnabled = checked
                        card.backend.setChannelAuto("lang", checked)
                    }
                }
            }
        }
    }

    Panel {
        objectName: "engineIssues"
        visible: card.warnings.length > 0 || card.compatibilityIssues.length > 0

        SectionLabel { text: "检查与兼容性" }

        Repeater {
            model: card.warnings

            delegate: RowLayout {
                required property var modelData

                Layout.fillWidth: true
                spacing: Theme.spaceSm

                Rectangle {
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: Theme.spaceXs
                    implicitWidth: 6
                    implicitHeight: 6
                    radius: 3
                    color: modelData.ok ? Theme.warn : Theme.danger
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    BodyText {
                        Layout.fillWidth: true
                        text: modelData.label
                        color: Theme.textPrimary
                        font.weight: Font.DemiBold
                    }

                    BodyText {
                        Layout.fillWidth: true
                        text: modelData.detail
                    }
                }

                Button {
                    objectName: "engineRetrustAction"
                    visible: modelData.label === "AE 模块校验" && !modelData.ok
                    text: "重新信任当前 AE"
                    onClicked: {
                        card.backend.retrustAe()
                        card.refreshChecks()
                    }
                }

                Button {
                    visible: modelData.label === "已勾选插件" && !modelData.ok
                    text: "启用第三方插件汉化"
                    enabledButton: !card.busy && !!card.backend && card.backend.dictCount > 0
                    onClicked: {
                        card.backend.enableRuntimeSelection()
                        card.refreshChecks()
                    }
                }
            }
        }

        Repeater {
            model: card.compatibilityIssues

            delegate: RowLayout {
                required property var modelData

                Layout.fillWidth: true
                spacing: Theme.spaceSm

                Rectangle {
                    implicitWidth: 6
                    implicitHeight: 6
                    radius: 3
                    color: modelData.disabled ? Theme.success : Theme.warn
                }

                BodyText {
                    Layout.fillWidth: true
                    text: modelData.disabled ? modelData.vendor + " · 已停用" : modelData.reason
                }

                Button {
                    objectName: "enginePluginAction"
                    text: modelData.disabled ? "恢复" : "停用"
                    danger: !modelData.disabled
                    onClicked: {
                        card.backend.setPluginEnabled(modelData.vendor, modelData.disabled)
                        card.compatibilityIssues = card.backend.compatIssues() || []
                    }
                }
            }
        }
    }
}
