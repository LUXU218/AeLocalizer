"""Native resize and title bar handling for the Windows QML window."""

from PySide6.QtCore import QAbstractNativeEventFilter, Qt


class WindowsSizeMoveFilter(QAbstractNativeEventFilter):
    def __init__(self, window):
        super().__init__()
        from ctypes import POINTER, WinDLL, c_int
        from ctypes.wintypes import HWND, MSG, POINT, RECT, UINT

        self.window = window
        self.hwnd = window.winId()
        self.message_type = MSG
        self.point_type = POINT
        self.rect_type = RECT
        self.user32 = WinDLL("user32")
        self.user32.ScreenToClient.argtypes = [HWND, POINTER(POINT)]
        self.user32.GetWindowRect.argtypes = [HWND, POINTER(RECT)]
        self.user32.GetDpiForWindow.argtypes = [HWND]
        self.user32.GetDpiForWindow.restype = UINT
        self.user32.GetSystemMetricsForDpi.argtypes = [c_int, UINT]

    def client_hit(self, lparam):
        from ctypes import byref, c_short

        x = c_short(lparam & 0xffff).value
        y = c_short((lparam >> 16) & 0xffff).value
        point = self.point_type(x, y)
        if not self.user32.ScreenToClient(self.hwnd, byref(point)):
            return None
        scale = self.window.devicePixelRatio()
        local_x, local_y = point.x / scale, point.y / scale
        if not (0 <= local_x < self.window.width()
                and 0 <= local_y < self.window.height()):
            return None

        if self.window.windowState() == Qt.WindowNoState:
            outer = self.rect_type()
            if not self.user32.GetWindowRect(self.hwnd, byref(outer)):
                return None
            dpi = self.user32.GetDpiForWindow(self.hwnd)
            padding = self.user32.GetSystemMetricsForDpi(92, dpi)  # SM_CXPADDEDBORDER
            horizontal = self.user32.GetSystemMetricsForDpi(32, dpi) + padding  # SM_CXSIZEFRAME
            vertical = self.user32.GetSystemMetricsForDpi(33, dpi) + padding  # SM_CYSIZEFRAME
            if not (outer.left + horizontal <= x < outer.right - horizontal
                    and outer.top + vertical <= y < outer.bottom - vertical):
                return None

        if (local_y < self.window.property("titleBarHeight")
                and local_x < self.window.property("titleBarButtonsLeft")):
            return 2  # HTCAPTION
        # Skip Qt's synthesized mouse events for the custom frame inside the client area.
        return 1  # HTCLIENT

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            event = self.message_type.from_address(int(message))
            if event.hWnd == self.hwnd:
                if event.message == 0x0084:  # WM_NCHITTEST
                    hit = self.client_hit(event.lParam)
                    if hit is not None:
                        return True, hit
                elif event.message == 0x0231:  # WM_ENTERSIZEMOVE
                    self.window.setProperty("windowResizing", True)
                elif event.message == 0x0232:  # WM_EXITSIZEMOVE
                    self.window.setProperty("windowResizing", False)
        return False, 0
