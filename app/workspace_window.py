"""Keep the existing controller alive while its heavy QML workspace is closed."""
import os

from PySide6.QtCore import QDeadlineTimer, QEvent, QObject, Property, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QWindow

from app.background_session import AutomaticClose


def _optimize_free_heap():
    """Ask Windows to decommit unused pages in this process's low-fragmentation heaps."""
    from ctypes import WinDLL, byref, c_int, c_size_t, c_void_p, sizeof, wintypes

    try:
        set_information = WinDLL("kernel32").HeapSetInformation
    except (AttributeError, OSError):
        return
    set_information.argtypes = (wintypes.HANDLE, c_int, c_void_p, c_size_t)
    set_information.restype = wintypes.BOOL
    heap_optimize_resources = 3
    version_and_flags = (wintypes.DWORD * 2)(1, 0)
    set_information(None, heap_optimize_resources, byref(version_and_flags), sizeof(version_and_flags))


class WorkspaceApplication(QGuiApplication):
    exiting = False

    def event(self, event):
        if event.type() != QEvent.Quit:
            return super().event(event)
        # Qt closes its top-level windows while delivering an application quit.
        self.exiting = True
        try:
            return super().event(event)
        finally:
            self.exiting = False


class BackgroundEntry(QWindow):
    """A minimized native window. Its taskbar entry also works without a system tray."""

    reopen = Signal()
    showEngine = Signal()
    showSettings = Signal()
    showWorkbench = Signal()
    background_title = "AeLocalizer · 后台汉化"
    _settings_menu_id = 0x1E50
    _workbench_menu_id = 0x1E60

    def __init__(self):
        super().__init__()
        self.setTitle(self.background_title)
        self.setFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
                      | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        self.resize(360, 120)
        self.visibilityChanged.connect(self._visibility_changed)

    def _visibility_changed(self, state):
        if state in (QWindow.Windowed, QWindow.Maximized, QWindow.FullScreen):
            self.reopen.emit()

    def add_workspace_menu(self, hwnd):
        import ctypes as c
        from ctypes import wintypes as w

        user = c.WinDLL("user32")
        get_menu = user.GetSystemMenu
        get_menu.argtypes, get_menu.restype = [w.HWND, w.BOOL], w.HMENU
        append = user.AppendMenuW
        append.argtypes, append.restype = [w.HMENU, w.UINT, c.c_size_t, w.LPCWSTR], w.BOOL
        menu = get_menu(hwnd, False)
        if menu:
            append(menu, 0, self._settings_menu_id, "设置")
            append(menu, 0, self._workbench_menu_id, "打开完整工作台")

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            from ctypes import wintypes

            event = wintypes.MSG.from_address(int(message))
            if event.message == 0x0112:
                command = event.wParam & 0xFFF0
                if command == self._settings_menu_id:
                    self.showSettings.emit()
                    return True, 0
                if command == self._workbench_menu_id:
                    self.showWorkbench.emit()
                    return True, 0
        return False, 0

    def closeEvent(self, event):
        app = QGuiApplication.instance()
        if app.isSavingSession() or getattr(app, "exiting", False):
            event.accept()
            return
        event.ignore()
        self.showEngine.emit()


class WorkspaceWindow(QObject):
    availabilityChanged = Signal()

    def __init__(self, app, backend, lease, qml_dir):
        super().__init__(app)
        self.app = app
        self.backend = backend
        self.session = backend.background_session
        self.lease = lease
        self.qml_dir = qml_dir
        self.engine = self.window = self.frame = self.entry = None
        self.page = self.section = 0
        self.geometry = None
        self.maximized = False
        self.pending = False
        self._startup_timer = None
        self._paused_startup_ms = None
        self._settings_process = None
        backend.engineSessionChanged.connect(self.availabilityChanged)

    @Property(bool, notify=availabilityChanged)
    def effectsCanStay(self):
        return self.app.platformName() == "windows" and self.session.effects_can_stay()

    def open(self):
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickWindow

        engine = QQmlApplicationEngine()
        engine.addImportPath(self.qml_dir)
        ctx = engine.rootContext()
        ctx.setContextProperty("appBackend", self.backend)
        ctx.setContextProperty("workspaceWindow", self)
        ctx.setContextProperty("useWindowsFrame", self.app.platformName() == "windows")
        ctx.setContextProperty("deferWorkspaceShow", True)
        engine.setInitialProperties({"pageIndex": self.page, "settingsSection": self.section})
        screen = self.app.primaryScreen()
        if screen:
            ctx.setContextProperty("initialWindowWorkArea", screen.availableGeometry())
        engine.load(QUrl.fromLocalFile(os.path.join(self.qml_dir, "main.qml")))
        if not engine.rootObjects() or not isinstance(engine.rootObjects()[0], QQuickWindow):
            engine.deleteLater()
            return False
        window = engine.rootObjects()[0]
        if self.geometry is not None:
            # Windows needs the native frame to interpret saved client coordinates.
            window.create()
            window.setGeometry(self.geometry)
        if self.lease.publish_window(int(window.winId())) is False and self.entry:
            engine.deleteLater()
            return False
        self.engine, self.window = engine, window
        if window.property("systemWindowFrame"):
            from app.windows_frame import WindowsSizeMoveFilter
            self.frame = WindowsSizeMoveFilter(window)
            self.app.installNativeEventFilter(self.frame)
        if self.maximized:
            window.showMaximized()
        else:
            window.show()
        return True

    def start_background(self):
        """Offer the existing native entry before loading the Qt Quick workspace."""
        entry = self._publish_entry()
        if entry is None:
            return False
        entry.setTitle("AeLocalizer · 正在恢复汉化")
        self._show_entry(entry)
        self._startup_deadline = QDeadlineTimer(120000)
        self._startup_timer = QTimer(self)
        self._startup_timer.setInterval(1000)
        self._startup_timer.timeout.connect(self._check_background_startup)
        self._startup_timer.start()
        return True

    def _check_background_startup(self):
        if not self.entry or self.entry.visibility() != QWindow.Minimized:
            self._startup_timer.stop()
            return
        action = self.session.automatic_close(supports_effects_background=True)
        if action is AutomaticClose.WAIT:
            if self._startup_deadline.hasExpired():
                self._startup_timer.stop()
                self.page = 2
                self.entry.setTitle("AeLocalizer · 自动恢复超时，点击查看")
            return
        self._startup_timer.stop()
        if action is AutomaticClose.KEEP_OPEN:
            self._finish_restore(activate=False)
        elif action is AutomaticClose.KEEP_BACKGROUND:
            self.entry.setTitle(self.entry.background_title)
            if self.app.platformName() == "windows":
                self.page = 4
        else:
            self.app.quit()

    def _publish_entry(self):
        entry = BackgroundEntry()
        hwnd = int(entry.winId())
        if self.lease.publish_window(hwnd) is False:
            entry.deleteLater()
            return None
        if self.app.platformName() == "windows":
            entry.add_workspace_menu(hwnd)
        return entry

    def _show_entry(self, entry):
        self.entry = entry
        entry.reopen.connect(self._restore, Qt.QueuedConnection)
        entry.showEngine.connect(self._restore_for_stop, Qt.QueuedConnection)
        entry.showSettings.connect(self._show_settings, Qt.QueuedConnection)
        entry.showWorkbench.connect(self._open_full_workbench, Qt.QueuedConnection)
        entry.showMinimized()

    @Slot(result=bool)
    def requestClose(self):
        if self.app.isSavingSession() or getattr(self.app, "exiting", False):
            return True
        if not self.effectsCanStay:
            accepted = self.session.request_close()
            if accepted:
                QTimer.singleShot(0, self.app.quit)
            return accepted
        if not self.pending:
            if self.session.workspace_busy():
                self.backend.toast.emit("info", "当前操作完成后即可转入后台。")
            else:
                self.pending = True
                QTimer.singleShot(0, self._suspend)
        # Unwind the active QML close handler before disposing of the engine.
        return False

    def _suspend(self):
        self.pending = False
        if self.window is None:
            return
        if not self.effectsCanStay or self.session.workspace_busy():
            self.backend.toast.emit("info", "当前状态已变化，工作台已保留。")
            return
        entry = self._publish_entry()
        if entry is None:
            self.backend.toast.emit("warn", "未能创建后台入口，工作台已保留。")
            return
        if not self.session.remember_background_on_startup():
            self.lease.publish_window(int(self.window.winId()))
            entry.deleteLater()
            self.backend.toast.emit("warn", "无法保存后台设置，工作台已保留。")
            return
        self._show_entry(entry)
        self.page = self.window.property("pageIndex")
        self.section = self.window.property("settingsSection")
        self.maximized = self.window.visibility() == QWindow.Maximized
        self.geometry = None if self.maximized else self.window.geometry()
        if self.frame:
            self.app.removeNativeEventFilter(self.frame)
            self.frame = None
        self.window.setPersistentGraphics(False)
        self.window.setPersistentSceneGraph(False)
        self.window.hide()
        self.window.releaseResources()
        self.engine.destroyed.connect(self._release_workspace_data, Qt.QueuedConnection)
        self.engine.deleteLater()
        self.window = self.engine = None

    @Slot()
    def _release_workspace_data(self):
        # A reopened workbench may already be using these models again.
        if self.window is None:
            self.backend.release_workspace_dictionary()
            if self.app.platformName() == "windows":
                _optimize_free_heap()

    @Slot()
    def _restore(self):
        if self.app.platformName() == "windows" and self.page == 4 and self.section in (0, 1, 5):
            self._show_settings()
            return
        self._finish_restore(activate=True)

    @Slot()
    def _show_settings(self):
        if not self.entry:
            return
        if self._settings_process is not None:
            self.entry.showMinimized()
            self._settings_process.activate()
            return
        from app.settings_workspace import LOCAL_SECTIONS, SettingsProcess

        if self._startup_timer is not None and self._startup_timer.isActive():
            self._paused_startup_ms = max(0, self._startup_deadline.remainingTime())
            self._startup_timer.stop()
        self.page = 4
        if self.section not in LOCAL_SECTIONS:
            self.section = 0
        process = SettingsProcess(self.backend, self.section, self)
        self._settings_process = process
        self.entry.showMinimized()
        self.app.aboutToQuit.connect(process.stop)
        process.openWorkbench.connect(self._show_workbench_settings, Qt.QueuedConnection)
        process.finished.connect(self._settings_finished, Qt.QueuedConnection)
        process.start()

    @Slot(int)
    def _show_workbench_settings(self, section):
        self.page = 4
        self.section = section
        self._finish_restore(activate=True)

    @Slot()
    def _open_full_workbench(self):
        self._finish_restore(activate=True)

    @Slot(bool)
    def _settings_finished(self, clean_exit):
        process = self._settings_process
        if process is None:
            return
        self._settings_process = None
        self.app.aboutToQuit.disconnect(process.stop)
        if self.entry:
            if clean_exit:
                self.section = process.section
                if self._paused_startup_ms is not None:
                    self._startup_deadline = QDeadlineTimer(self._paused_startup_ms)
                    self._paused_startup_ms = None
                    self._startup_timer.start()
            else:
                self._finish_restore(activate=True)
        process.deleteLater()

    def _finish_restore(self, *, activate):
        if not self.entry:
            return
        self._paused_startup_ms = None
        if self._settings_process is not None:
            self._settings_process.stop()
        if self._startup_timer is not None:
            self._startup_timer.stop()
        if not self.open():
            self.entry.setTitle("AeLocalizer · 工作台打开失败，点击重试")
            self.entry.showMinimized()
            return
        self.entry.hide()
        self.entry.deleteLater()
        self.entry = None
        if activate:
            self.window.raise_()
            self.window.requestActivate()

    @Slot()
    def _restore_for_stop(self):
        self.page = 2
        self._finish_restore(activate=True)
        if self.window:
            self.backend.toast.emit("info", "要退出汉化，请点击「停止引擎」，完成后关闭工作台。")
