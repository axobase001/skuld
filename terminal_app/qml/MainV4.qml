import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

ApplicationWindow {
    id: window
    property string uiLanguage: "en"
    visible: true
    width: 1440
    height: 900
    minimumWidth: 1000
    minimumHeight: 650
    title: "SKULD V4 × Pi Terminal"
    color: Theme.canvas
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 72
            color: Theme.canvas; border.color: Theme.line
            RowLayout {
                anchors.fill: parent; anchors.margins: 18; spacing: 14
                Rectangle { width: 3; height: 32; color: Theme.crimson }
                Text { text: "S K U L D"; color: Theme.ink; font.family: Theme.serif; font.pixelSize: 23 }
                Rectangle { width: 1; height: 28; color: Theme.line }
                Text { text: "V4 × Pi  /  Research Terminal"; color: Theme.muted; font.family: Theme.sans; font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
                ComboBox {
                    id: languageSwitch
                    model: ["English", "简体中文"]
                    currentIndex: 0
                    onActivated: index => window.uiLanguage = index === 0 ? "en" : "zh-CN"
                    Layout.preferredWidth: 112
                    Accessible.name: "Interface language"
                }
                Text { text: terminalBridge.busy ? (window.uiLanguage === "en" ? "EXECUTING" : "执行中") : (window.uiLanguage === "en" ? "READY" : "就绪"); color: terminalBridge.busy ? Theme.crimson : Theme.green; font.pixelSize: 11 }
                ModeButton { mode: "PAUSED"; label: window.uiLanguage === "en" ? "PAUSED" : "暂停"; onRequested: target => terminalBridge.requestMode(target) }
                ModeButton { mode: "OBSERVE"; label: window.uiLanguage === "en" ? "OBSERVE" : "观察"; onRequested: target => terminalBridge.requestMode(target) }
                ModeButton { mode: "ACT"; label: window.uiLanguage === "en" ? "ACT" : "行动"; onRequested: target => terminalBridge.requestMode(target) }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.margins: 14; spacing: 12
            Rectangle {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 6
                color: Theme.panel; border.color: Theme.line; radius: 5
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 9
                    Text { text: window.uiLanguage === "en" ? "CHAT / WORKSPACE" : "对话 / 工作区"; color: Theme.ink; font.pixelSize: 12; font.bold: true }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                    ListView {
                        id: chatList
                        Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8
                        model: terminalBridge.dialogue
                        onCountChanged: positionViewAtEnd()
                        delegate: Rectangle {
                            width: chatList.width; height: message.implicitHeight + 18
                            color: modelData.speaker === "USER" ? Theme.canvas : Theme.panel
                            border.color: Theme.line; radius: 4
                            Text {
                                id: message
                                anchors.fill: parent; anchors.margins: 9
                                text: modelData.speaker + "  ·  " + modelData.text
                                wrapMode: Text.WrapAnywhere
                                color: modelData.speaker === "ERROR" ? Theme.crimson : Theme.ink
                                font.family: Theme.sans; font.pixelSize: 12
                            }
                        }
                    }
                    Text { text: window.uiLanguage === "en" ? "TOOLS: /read path     /write path | content     /approve action_id" : "工具：/read 路径     /write 路径 | 内容     /approve action_id"; color: Theme.muted; font.pixelSize: 11 }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField {
                            id: composer; Layout.fillWidth: true
                            placeholderText: window.uiLanguage === "en" ? "Ask Skuld to work on something…" : "向 Skuld 输入任务…"
                            color: Theme.ink; enabled: !terminalBridge.busy
                            onAccepted: sendButton.clicked()
                        }
                        Button {
                            id: sendButton; text: window.uiLanguage === "en" ? "Send" : "发送"; enabled: !terminalBridge.busy
                            onClicked: { terminalBridge.sendMessage(composer.text); composer.clear() }
                        }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 4; spacing: 12
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    color: Theme.panel; border.color: Theme.line; radius: 5
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 12
                        Text { text: window.uiLanguage === "en" ? "EXECUTION TRACE / PI EVIDENCE" : "执行轨迹 / Pi 执行证据"; color: Theme.ink; font.pixelSize: 12; font.bold: true }
                        ScrollView { Layout.fillWidth: true; Layout.fillHeight: true
                            TextArea { text: terminalBridge.traceText; readOnly: true; wrapMode: Text.WrapAnywhere; color: Theme.ink; font.pixelSize: 11 }
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    color: Theme.panel; border.color: Theme.line; radius: 5
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 12
                        Text { text: window.uiLanguage === "en" ? "SKULD STATE / PERSISTED SUBJECT" : "SKULD 状态 / 真实主体状态"; color: Theme.ink; font.pixelSize: 12; font.bold: true }
                        ScrollView { Layout.fillWidth: true; Layout.fillHeight: true
                            TextArea { text: terminalBridge.stateText; readOnly: true; wrapMode: Text.WrapAnywhere; color: Theme.ink; font.pixelSize: 11 }
                        }
                    }
                }
            }
        }
    }
}
