import QtQuick
import ".."

Rectangle {
    id: root
    property string mode: "OBSERVE"
    property string label: mode
    property bool selected: terminalBridge.mode === mode
    signal requested(string mode)

    implicitWidth: 112
    implicitHeight: 34
    radius: 5
    color: selected ? Theme.panel : "transparent"
    border.color: selected ? Theme.crimson : Theme.line
    border.width: 1

    Row {
        anchors.centerIn: parent
        spacing: 7

        Rectangle {
            width: 8
            height: 8
            radius: 4
            color: root.selected ? Theme.crimson : "transparent"
            border.color: root.selected ? Theme.crimson : Theme.muted
        }

        Text {
            text: root.label
            color: root.selected ? Theme.crimson : Theme.ink
            font.family: Theme.sans
            font.pixelSize: 11
        }
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.requested(root.mode)
    }
}
