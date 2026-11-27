"""Private settings view transport. Only the incumbent controller writes preferences."""
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QObject, Property, QProcess, QTimer, Signal, Slot


LOCAL_SECTIONS = (0, 1, 5)
MAX_PACKET = 8192


def _packet(message):
    return json.dumps(message, ensure_ascii=True).encode("ascii") + b"\n"


class SettingsProcess(QObject):
    openWorkbench = Signal(int)
    finished = Signal(bool)

    def __init__(self, backend, section, parent):
        super().__init__(parent)
        self._backend = backend
        self._section = section
        self._closing = False
        self._ready = False
        self._buffer = bytearray()
        self._process = QProcess(self)
        self._process.setProgram(sys.executable)
        self._process.setArguments(
            ["--settings-workspace"] if getattr(sys, "frozen", False)
            else ["-m", "app.main", "--settings-workspace"])
        if not getattr(sys, "frozen", False):
            self._process.setWorkingDirectory(str(Path(__file__).resolve().parents[1]))
        self._process.started.connect(self._publish)
        self._process.readyReadStandardOutput.connect(self._receive)
        self._process.readyReadStandardError.connect(self._process.readAllStandardError)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._error)
        self._launch_timeout = QTimer(self)
        self._launch_timeout.setSingleShot(True)
        self._launch_timeout.timeout.connect(self._process.kill)

    def start(self):
        self._launch_timeout.start(12000)
        self._process.start()

    def activate(self):
        if self._ready:
            self._process.write(_packet({"activate": True}))

    def stop(self):
        self._closing = True
        self._launch_timeout.stop()
        if self._process.state() != QProcess.NotRunning:
            self._process.closeWriteChannel()
            if not self._process.waitForFinished(1200):
                self._process.kill()
                self._process.waitForFinished(300)

    def _publish(self, notice=""):
        b = self._backend
        self._process.write(_packet({
            "section": self._section, "notice": notice,
            "preferences": {
                "uiTheme": b.uiTheme, "accentKey": b.accentKey, "density": b.density,
                "fontScale": b.fontScale, "uiFont": b.uiFont, "textColor": b.textColor,
                "reduceMotion": b.reduceMotion, "autoScan": b.autoScan,
                "startupWithAe": b.startupWithAe, "hasWallpaper": b.backgroundKind != "none",
                "appVersion": b.appVersion,
            }}))

    def _receive(self):
        self._buffer.extend(self._process.readAllStandardOutput().data())
        while b"\n" in self._buffer:
            packet, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            try:
                if len(packet) > MAX_PACKET or not self._dispatch(json.loads(packet)):
                    raise ValueError("invalid settings message")
            except (ValueError, TypeError):
                self._process.kill()
                return
        if len(self._buffer) > MAX_PACKET:
            self._process.kill()

    def _dispatch(self, command):
        if not isinstance(command, list) or len(command) != 2:
            return False
        name, value = command
        if not isinstance(name, str):
            return False
        if name == "ready" and value is True:
            self._ready = True
            self._launch_timeout.stop()
            self.activate()
            return True
        if name == "close" and type(value) is int and value in LOCAL_SECTIONS:
            self._section = value
            self._closing = True
            return True
        if name == "workbench" and type(value) is int and 0 <= value < 6:
            self._section = value
            self._closing = True
            self.openWorkbench.emit(value)
            return True
        if not self._ready or self._closing:
            return False

        b = self._backend
        choice_settings = {
            "theme": (b.setUiTheme, ("light", "dark", "system")),
            "accent": (b.setAccentKey, ("blue", "violet", "teal", "amber", "rose", "slate")),
            "density": (b.setDensity, ("compact", "normal", "relaxed")),
            "font_scale": (b.setFontScale, ("small", "normal", "large")),
        }
        boolean_settings = {
            "motion": b.setReduceMotion, "auto_scan": b.setAutoScan, "startup": b.setStartupWithAe,
        }
        notices = []

        def notification(level, message):
            if level in ("warn", "error"):
                notices.append(message)

        b.toast.connect(notification)
        try:
            if name in choice_settings and value in choice_settings[name][1]:
                choice_settings[name][0](value)
            elif name in boolean_settings and type(value) is bool:
                boolean_settings[name](value)
            elif name == "font" and isinstance(value, str) and len(value) <= 64:
                b.setUiFont(value)
            elif name == "text_color" and isinstance(value, str):
                b.setTextColor(value)
            elif name == "reset" and value is None:
                b.resetAppearance()
            else:
                return False
            self._publish(notices[-1] if notices else "")
            return True
        finally:
            b.toast.disconnect(notification)

    @property
    def section(self):
        return self._section

    def _finished(self, code, status):
        self._launch_timeout.stop()
        self.finished.emit(self._closing and code == 0 and status == QProcess.NormalExit)

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self._launch_timeout.stop()
            self.finished.emit(False)


def _pipe_stream(index):
    if os.name != "nt":
        return os.fdopen(os.dup(index), ("rb", "wb")[index], buffering=0)
    import ctypes as c
    from ctypes import wintypes
    import msvcrt
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    handle = kernel.GetStdHandle(-10 - index)
    if not handle or kernel.GetFileType(handle) != 3:
        raise OSError("settings workspace requires inherited pipes")
    mode = (os.O_RDONLY, os.O_WRONLY)[index] | os.O_BINARY
    fd = msvcrt.open_osfhandle(handle, mode)
    return os.fdopen(fd, ("rb", "wb")[index])


def run_settings_workspace(qml_dir):
    """The child loads the real SettingsPage and has no AppBackend or profile access."""
    from threading import Thread

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFontDatabase, QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from app.windows_frame import configure_workspace_renderer

    try:
        input_pipe = _pipe_stream(0)
        output_pipe = _pipe_stream(1)
    except OSError:
        return 2

    class SettingsView(QObject):
        updated = Signal()
        received = Signal(dict)
        disconnected = Signal()
        activated = Signal()

        def __init__(self):
            super().__init__()
            self._snapshot = {}
            self._window = None

        @Property("QVariantMap", notify=updated)
        def snapshot(self):
            return self._snapshot

        @Slot(str, "QVariant")
        def request(self, command, value):
            try:
                output_pipe.write(_packet([command, value]))
                output_pipe.flush()
            except OSError:
                app.quit()

        @Slot(result="QStringList")
        def systemFonts(self):
            return sorted(QFontDatabase.families())

        def read(self):
            try:
                while line := input_pipe.readline(MAX_PACKET + 1):
                    if len(line) > MAX_PACKET:
                        break
                    data = json.loads(line)
                    if isinstance(data, dict):
                        self.received.emit(data)
            except (OSError, ValueError):
                pass
            self.disconnected.emit()

        @Slot(dict)
        def update(self, data):
            if data.get("activate") is True:
                self.activated.emit()
                return
            self._snapshot = data
            self.updated.emit()
            if self._window is None:
                engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, "SettingsWorkspace.qml")))
                roots = engine.rootObjects()
                if not roots:
                    app.exit(1)
                    return
                self._window = roots[0]
                self.request("ready", True)

    app = QGuiApplication([])
    app.setApplicationName("AeLocalizer")
    configure_workspace_renderer()
    view = SettingsView()
    engine = QQmlApplicationEngine()
    engine.addImportPath(qml_dir)
    engine.rootContext().setContextProperty("settingsTransport", view)
    view.received.connect(view.update)
    view.disconnected.connect(app.quit)
    Thread(target=view.read, daemon=True).start()
    return app.exec()
