import QtQuick
import "../components"

Item {
    id: root
    property var backend: null

    NameEditor {
        id: nameEditor
        anchors.fill: parent
        backend: root.backend
    }

    onVisibleChanged: if (visible) nameEditor.reload()
    Component.onCompleted: if (visible) nameEditor.reload()
}
