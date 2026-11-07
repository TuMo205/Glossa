"""Các lời gọi Win32 qua ctypes. Trên hệ điều hành khác, các hàm này không làm gì."""

from __future__ import annotations

import ctypes
import logging
import os
import sys

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"

WDA_NONE = 0x0
WDA_MONITOR = 0x1
WDA_EXCLUDEFROMCAPTURE = 0x11

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000

HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x1, 0x2, 0x10, 0x200

ERROR_ALREADY_EXISTS = 183

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Glossa"

if IS_WIN:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LONG_PTR = ctypes.c_ssize_t
    _GetWindowLong = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    _SetWindowLong = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
    _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetWindowLong.restype = LONG_PTR
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
    _SetWindowLong.restype = LONG_PTR

    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE


def windows_build() -> int:
    if not IS_WIN:
        return 0
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0


def exclude_capture_supported() -> bool:
    """WDA_EXCLUDEFROMCAPTURE có từ Windows 10 bản 2004 (build 19041)."""
    return windows_build() >= 19041


def set_exclude_from_capture(hwnd: int, enable: bool = True) -> bool:
    """Ẩn cửa sổ khỏi mọi ảnh chụp màn hình (để không tự OCR bản dịch của chính mình)."""
    if not IS_WIN or not hwnd:
        return False
    aff = WDA_EXCLUDEFROMCAPTURE if enable else WDA_NONE
    if enable and not exclude_capture_supported():
        return False
    if not user32.SetWindowDisplayAffinity(hwnd, aff):
        log.warning("SetWindowDisplayAffinity lỗi %d", ctypes.get_last_error())
        return False
    cur = wintypes.DWORD(0)
    if user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(cur)):
        return cur.value == aff
    return True


def add_ex_style(hwnd: int, bits: int, remove: int = 0) -> None:
    if not IS_WIN or not hwnd:
        return
    style = _GetWindowLong(hwnd, GWL_EXSTYLE)
    _SetWindowLong(hwnd, GWL_EXSTYLE, (style | bits) & ~remove)


def set_topmost(hwnd: int) -> None:
    if IS_WIN and hwnd:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)


def get_window_rect(hwnd: int):
    """(x, y, w, h) theo pixel thật, hoặc None."""
    if not IS_WIN or not hwnd:
        return None
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


_mta_cookie = None


def keep_mta_alive() -> None:
    """Giữ "căn hộ" COM MTA sống suốt đời tiến trình. Nếu không, khi luồng OCR cuối cùng kết thúc,
    Windows gỡ thư viện OCR khỏi bộ nhớ trong khi lớp WinRT vẫn giữ con trỏ tới nó => luồng OCR mở
    sau đó (tải lại OCR, mở lại chương trình trong cùng tiến trình) bị sập (access violation)."""
    global _mta_cookie
    if not IS_WIN or _mta_cookie is not None:
        return
    try:
        cookie = ctypes.c_void_p()
        if ctypes.windll.ole32.CoIncrementMTAUsage(ctypes.byref(cookie)) == 0:  # S_OK
            _mta_cookie = cookie
    except Exception:
        pass


def com_init_mta() -> None:
    """Khởi tạo COM kiểu MTA cho luồng hiện tại (cần cho Windows OCR)."""
    if not IS_WIN:
        return
    keep_mta_alive()
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    except Exception:
        pass


_mutex = None


def single_instance(name: str = "Local\\Glossa.SingleInstance") -> bool:
    """True nếu đây là phiên bản duy nhất đang chạy."""
    global _mutex
    if not IS_WIN:
        return True
    _mutex = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def mutex_exists(name: str) -> bool:
    """Có chương trình nào đang giữ mutex này không (vd bản cũ đang chạy)."""
    if not IS_WIN:
        return False
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    h = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


def message_box(text: str, title: str = "Glossa", icon: int = 0x40) -> None:
    if IS_WIN:
        user32.MessageBoxW(None, text, title, icon)
    else:
        print(f"[{title}] {text}")


# --- Khởi động cùng Windows (HKCU\...\Run, không cần quyền Admin) -------------

def autostart_command() -> str:
    from .paths import launch_command_line

    return launch_command_line()


def get_autostart() -> bool:
    if not IS_WIN:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def get_run_value(name: str):
    """Lệnh khởi động cùng Windows đang ghi dưới tên 'name' (None nếu không có)."""
    if not IS_WIN:
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return None


def delete_run_value(name: str) -> bool:
    if not IS_WIN:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
        return True
    except OSError:
        return False


def use_console(allow_new: bool) -> bool:
    """Bản .exe không có cửa sổ dòng lệnh: mượn cửa sổ của cmd đã mở nó, hoặc (allow_new) mở cửa sổ mới
    để in kết quả kiểm tra. Trả về True nếu đã mở cửa sổ mới (nên chờ Enter trước khi đóng)."""
    if not IS_WIN or sys.stdout is not None:
        return False
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.SetConsoleOutputCP.argtypes = [wintypes.UINT]
    created = False
    if not kernel32.AttachConsole(0xFFFFFFFF):  # ATTACH_PARENT_PROCESS
        if not allow_new or not kernel32.AllocConsole():
            return False
        created = True
    try:
        kernel32.SetConsoleOutputCP(65001)
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = sys.stdout
        sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")
    except OSError:
        return False
    return created


def set_autostart(enable: bool, app_py: str | None = None) -> bool:
    if not IS_WIN:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log.error("Không ghi được khởi động cùng Windows: %s", e)
        return False


def ipc_name() -> str:
    """Tên kênh để lần mở thứ hai báo cho bản đang chạy hiện bảng điều khiển."""
    user = "".join(c for c in (os.environ.get("USERNAME") or os.environ.get("USER") or "user") if c.isalnum())
    return f"Glossa.Panel.{user or 'user'}"


def allow_foreground() -> None:
    """Cho bản đang chạy được phép đưa cửa sổ lên trước (khi lần mở thứ hai nhờ nó)."""
    if IS_WIN:
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass


def run_elevated(file: str, params: str, cwd: str | None = None, show: int = 0):
    """Chạy chương trình với quyền Administrator (Windows hiện hộp hỏi quyền - UAC).
    Trả về handle tiến trình, hoặc None nếu người dùng bấm No / không chạy được."""
    if not IS_WIN:
        return None
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = file
    info.lpParameters = params
    info.lpDirectory = cwd
    info.nShow = show
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        log.info("Không chạy được với quyền Admin (lỗi %d)", ctypes.get_last_error())  # 1223 = bấm No
        return None
    return info.hProcess


def wait_process(handle, timeout_s: float | None = None):
    """Chờ tiến trình kết thúc; trả về mã thoát (None nếu hết giờ)."""
    if not IS_WIN or not handle:
        return None
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    ms = 0xFFFFFFFF if timeout_s is None else int(timeout_s * 1000)
    if k.WaitForSingleObject(handle, ms) != 0:
        return None
    code = wintypes.DWORD(0)
    k.GetExitCodeProcess(handle, ctypes.byref(code))
    k.CloseHandle(handle)
    return int(code.value)
