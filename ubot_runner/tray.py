"""An icon by the clock (Windows notification area), with no package to install.

tkinter has no tray icon of its own. This talks to Shell_NotifyIconW through
ctypes on a thread of its own (the icon needs a window and a message loop).
Clicking the icon calls `on_click` from that thread - the caller must hand it
to tkinter's thread (the window polls a queue). Anywhere else than Windows,
`Tray.available()` is False and the window minimises to the taskbar instead.
"""
import os
import threading

WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
IDI_APPLICATION = 32512


class Tray:
    def __init__(self, tip, on_click):
        self.tip = tip[:120]
        self.on_click = on_click
        self.hwnd = None
        self.thread = None
        self.ready = threading.Event()

    @staticmethod
    def available():
        return os.name == "nt"

    def show(self, balloon=None):
        if not self.available():
            return False
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, args=(balloon,), name="ubot-tray", daemon=True)
            self.thread.start()
            self.ready.wait(5)
        return self.hwnd is not None

    def hide(self):
        if self.hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self.thread:
            self.thread.join(timeout=5)
        self.thread = None
        self.hwnd = None
        self.ready.clear()

    def _run(self, balloon):
        import ctypes
        from ctypes import wintypes as W
        u32, s32, k32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        u32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u32.DefWindowProcW.restype = LRESULT

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                        ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
                        ("lpszClassName", W.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                        ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                        ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                        ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD)]

        def proc(hwnd, msg, wp, lp):
            if msg == WM_TRAY and lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP):
                try:
                    self.on_click()
                except Exception:
                    pass
                return 0
            if msg == WM_CLOSE:
                s32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                u32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                u32.PostQuitMessage(0)
                return 0
            return u32.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc = WNDPROC(proc)        # keep a reference: the callback must outlive this frame
        hinst = k32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = self._proc
        cls.hInstance = hinst
        cls.lpszClassName = "uBotRunnerTray%d" % id(self)
        u32.RegisterClassW(ctypes.byref(cls))
        u32.CreateWindowExW.restype = W.HWND
        hwnd = u32.CreateWindowExW(0, cls.lpszClassName, "uBot tray", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            self.ready.set()
            return
        u32.LoadIconW.restype = W.HICON
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | (NIF_INFO if balloon else 0)
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = u32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        nid.szTip = self.tip
        if balloon:
            nid.szInfoTitle = balloon[0][:63]
            nid.szInfo = balloon[1][:255]
        if not s32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            u32.DestroyWindow(hwnd)
            self.ready.set()
            return
        self.hwnd = hwnd
        self.ready.set()
        msg = W.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        u32.UnregisterClassW(cls.lpszClassName, hinst)
