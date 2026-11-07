"""Phím tắt toàn cục (RegisterHotKey) chạy trong luồng riêng có vòng lặp thông điệp."""

from __future__ import annotations

import ctypes
import logging
import threading

from .winutil import IS_WIN

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT, WM_USER = 0x0312, 0x0012, 0x0400

_NAMED_VK = {
    "SPACE": 0x20, "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B,
    "INSERT": 0x2D, "INS": 0x2D, "DELETE": 0x2E, "DEL": 0x2E, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PAGEUP": 0x21, "PGDN": 0x22, "PAGEDOWN": 0x22,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "PAUSE": 0x13,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}


def parse_hotkey(combo: str):
    """'Ctrl+Alt+T' -> (modifiers, virtual-key) hoặc None nếu không hợp lệ."""
    mods, vk = 0, None
    for part in [p.strip() for p in combo.replace(" ", "").split("+") if p.strip()]:
        u = part.upper()
        if u in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif u == "ALT":
            mods |= MOD_ALT
        elif u == "SHIFT":
            mods |= MOD_SHIFT
        elif u in ("WIN", "META", "SUPER"):
            mods |= MOD_WIN
        elif len(u) == 1 and (u.isalpha() or u.isdigit()) and u.isascii():
            vk = ord(u)
        elif u.startswith("F") and u[1:].isdigit() and 1 <= int(u[1:]) <= 24:
            vk = 0x70 + int(u[1:]) - 1
        elif u in _NAMED_VK:
            vk = _NAMED_VK[u]
        else:
            return None
    if vk is None:
        return None
    return mods, vk


class HotkeyThread(threading.Thread):
    """bindings: {tên: 'Ctrl+Alt+T'}; callback(tên) được gọi từ luồng này."""

    def __init__(self, bindings: dict, callback):
        super().__init__(name="hotkeys", daemon=True)
        self.bindings = dict(bindings)
        self.callback = callback
        self.failed: list = []
        self._tid = None
        self._ready = threading.Event()

    def run(self) -> None:
        if not IS_WIN:
            self._ready.set()
            return
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, 0)  # tạo hàng đợi
        ids = {}
        for i, (name, combo) in enumerate(self.bindings.items(), start=1):
            parsed = parse_hotkey(combo or "")
            if not parsed:
                if combo:
                    self.failed.append(f"{name} ({combo})")
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ids[i] = name
            else:
                self.failed.append(f"{name} ({combo})")
        if self.failed:
            log.warning("Không đăng ký được phím tắt: %s", ", ".join(self.failed))
        self._ready.set()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                try:
                    self.callback(ids[msg.wParam])
                except Exception:
                    log.exception("Lỗi xử lý phím tắt")
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def wait_ready(self, timeout: float = 2.0) -> None:
        self._ready.wait(timeout)

    def stop(self, join: float = 0.0) -> None:
        if IS_WIN and self.is_alive():
            self._ready.wait(1.0)  # chưa tạo xong hàng đợi tin nhắn thì WM_QUIT bị mất
            if self._tid:
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if join and self.is_alive():
            self.join(join)
