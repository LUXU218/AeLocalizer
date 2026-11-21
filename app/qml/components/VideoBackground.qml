import QtQuick
import QtQuick.Window
import QtMultimedia
import AeLocalizer

// 通过 Loader 隔离可选的 QtMultimedia 依赖。
Item {
    id: root

    AudioOutput {
        id: audio
        muted: true
        volume: 0
    }

    MediaPlayer {
        id: player
        source: Theme.bgVideo
        audioOutput: audio
        videoOutput: out
        loops: MediaPlayer.Infinite
        onSourceChanged: if (source.toString() !== "") play()
        onErrorOccurred: function(error, message) {
            if (error !== MediaPlayer.NoError)
                console.warn("视频背景播放失败:", message)
        }
    }

    VideoOutput {
        id: out
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectCrop
    }

    property bool pausedByHide: false

    function _syncRun() {
        var vis = root.Window.window ? root.Window.window.visible : true
        var mini = root.Window.window ? (root.Window.window.visibility === Window.Minimized) : false
        if (!vis || mini) {
            if (player.playbackState === MediaPlayer.PlayingState) {
                player.pause()
                pausedByHide = true
            }
        } else if (pausedByHide) {
            pausedByHide = false
            player.play()
        }
    }

    Connections {
        target: root.Window.window
        function onVisibleChanged() { root._syncRun() }
        function onVisibilityChanged() { root._syncRun() }
    }

    readonly property bool playing: player.playbackState === MediaPlayer.PlayingState
    readonly property bool hasVideo: player.hasVideo
}
