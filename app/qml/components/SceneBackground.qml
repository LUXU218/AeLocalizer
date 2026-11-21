import QtQuick
import QtWebEngine
import AeLocalizer

// 通过 Loader 隔离可选的 QtWebEngine 依赖。
Item {
    id: root

    property var backend: null
    property string status: "init"
    property string lastError: ""

    readonly property url pageUrl: Theme.bgSceneUrl
    readonly property bool resizing: Window.window && Window.window.windowResizing === true
    property size viewportSize: Qt.size(0, 0)

    // Let Qt scale the current scene frame while Chromium holds its viewport size.
    function syncViewport() {
        if (!resizing || viewportSize.width === 0 || viewportSize.height === 0)
            viewportSize = Qt.size(width, height)
    }

    onWidthChanged: syncViewport()
    onHeightChanged: syncViewport()
    onResizingChanged: syncViewport()
    Component.onCompleted: syncViewport()

    WebEngineView {
        id: view
        width: root.viewportSize.width
        height: root.viewportSize.height
        transform: Scale {
            xScale: view.width > 0 ? root.width / view.width : 1
            yScale: view.height > 0 ? root.height / view.height : 1
        }
        url: root.pageUrl
        activeFocusOnPress: false
        onLoadingChanged: function(request) {
            if (request.status === WebEngineView.LoadSucceededStatus) {
                root.status = "loaded"
                root.pushInitialProps()
                pollTimer.restart()
                mountTimer.restart()
            } else if (request.status === WebEngineView.LoadFailedStatus) {
                root.status = "load-failed"
                root.lastError = String(request.errorString || "")
                console.warn("场景渲染页加载失败:", root.lastError)
            }
        }
    }

    Timer {
        id: mountTimer
        // Allow for the renderer's own 60-second first-frame deadline and local asset loading.
        interval: 90000
        onTriggered: {
            if (root.status.indexOf("mounted") >= 0) return
            root.status = "mount-timeout"
            console.warn("场景渲染页 90 秒未就绪，退回提取画面")
            if (root.backend) root.backend.sceneRenderFailed("渲染页未就绪")
        }
    }

    Timer {
        id: pollTimer
        interval: 400
        repeat: true
        running: true
        onTriggered: {
            view.runJavaScript("window.__aeloc ? window.__aeloc.status() : 'n/a'",
                               function (res) {
                                   root.status = String(res || "")
                                   if (root.status.indexOf("mounted") >= 0 && !healthTimer.running) {
                                       mountTimer.stop()
                                       healthTimer.start()
                                   }
                                   if (root.status.indexOf('"state":"error"') >= 0
                                           || root.status.indexOf('"state":"rejected"') >= 0) {
                                       if (root.status.indexOf("fallback-sent") < 0) {
                                           root.status = root.status + " fallback-sent"
                                           console.warn("渲染页自报失败，退回提取画面")
                                           if (root.backend) root.backend.sceneRenderFailed("渲染器报错")
                                       }
                                   }
                               })
        }
    }

    property int blackSamples: 0
    Timer {
        id: healthTimer
        interval: 1200
        repeat: true
        onTriggered: {
            view.runJavaScript("window.__aeloc && window.__aeloc.sig ? window.__aeloc.sig() : ''",
                               function (res) {
                                   var txt = String(res || "")
                                   var mean = parseInt(txt.split("/")[1] || "-1", 10)
                                   root.canvasSig = txt
                                   if (mean >= 0 && mean <= 4) {
                                       root.blackSamples += 1
                                       if (root.blackSamples >= 8) {
                                           root.blackSamples = 0
                                           healthTimer.stop()
                                           root.status = "blank-fallback"
                                           if (root.backend)
                                               root.backend.sceneRenderFailed("画面全黑")
                                       }
                                   } else {
                                       root.blackSamples = 0
                                       if (mean > 4) healthTimer.stop()
                                   }
                               })
        }
    }

    property string canvasSig: ""

    property string lastJs: ""
    function evalJs(code) {
        view.runJavaScript(code, function (r) { root.lastJs = String(r) })
    }

    function probeSig() {
        view.runJavaScript("window.__aeloc && window.__aeloc.sig ? window.__aeloc.sig() : ''",
                           function (res) { root.canvasSig = String(res || "") })
    }

    function pushInitialProps() {
        if (!backend) return
        var props = backend.sceneInitialProps()
        if (props && Object.keys(props).length > 0) {
            applyProps(props)
        }
    }

    function applyProps(obj) {
        var json = JSON.stringify(obj)
        view.runJavaScript("window.__aeloc && window.__aeloc.setProps(" + json + ")")
    }

    Connections {
        target: Theme
        function onBgSceneRevChanged() { root.reloadScene() }
    }

    // 保留 WebEngine 页面，只切换原地址内的场景。
    function reloadScene() {
        var props = JSON.stringify(backend ? backend.sceneInitialProps() : {})
        view.runJavaScript("window.__aeloc && window.__aeloc.reload(" + props + ")",
                           function (res) { root.status = "reload:" + String(res || "") })
    }

    property bool pausedByHide: false

    function _syncRun() {
        var w = root.Window.window
        var hidden = !w || !w.visible || w.visibility === Window.Minimized
        if (hidden) {
            if (!root.pausedByHide) {
                root.pausedByHide = true
                view.runJavaScript("window.__aeloc && window.__aeloc.pause()")
            }
        } else if (root.pausedByHide) {
            root.pausedByHide = false
            view.runJavaScript("window.__aeloc && window.__aeloc.resume()")
        }
    }

    Connections {
        target: root.Window.window
        function onVisibleChanged() { root._syncRun() }
        function onVisibilityChanged() { root._syncRun() }
    }

    Connections {
        target: root.backend
        function onWePropApplied(name, value) {
            var one = {}
            one[String(name)] = value
            root.applyProps(one)
        }
    }
}
