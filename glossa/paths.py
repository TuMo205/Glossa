"""Thư mục của chương trình.

APP_DIR  - tài nguyên đi kèm (HUONG_DAN.md, install_ocr_languages.ps1), chỉ đọc.
DATA_DIR - cài đặt, nhật ký, bộ nhớ bản dịch của người dùng.

- Chạy từ mã nguồn (python app.py): cả hai là thư mục chứa app.py (như các bản trước).
- Bản cài (Glossa.exe): dữ liệu ở %APPDATA%\\Glossa, không đụng vào thư mục cài.
- Bản di động (giải nén .zip, có file portable.txt cạnh .exe): dữ liệu ở thư mục data cạnh .exe.
- Biến môi trường GLOSSA_HOME: tự chọn thư mục dữ liệu.
"""

from __future__ import annotations

import os
import sys

from . import APP_ID

FROZEN = bool(getattr(sys, "frozen", False))
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = getattr(sys, "_MEIPASS", SRC_DIR) if FROZEN else SRC_DIR
EXE_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else SRC_DIR
PORTABLE_MARK = "portable.txt"


def _data_dir() -> str:
    env = os.environ.get("GLOSSA_HOME")
    if env:
        return os.path.abspath(env)
    if not FROZEN:
        return SRC_DIR
    if os.path.exists(os.path.join(EXE_DIR, PORTABLE_MARK)):
        return os.path.join(EXE_DIR, "data")
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_ID)


DATA_DIR = _data_dir()


def data_path(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


def app_path(*parts: str) -> str:
    return os.path.join(APP_DIR, *parts)


def ensure_data_dir() -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError:
        pass


def launch_command() -> tuple:
    """(chương trình, [tham số]) để chạy ngầm Glossa - dùng cho khởi động cùng Windows và lối tắt."""
    if FROZEN:
        return os.path.abspath(sys.executable), []
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return exe, [os.path.join(SRC_DIR, "app.py")]


def launch_command_line() -> str:
    exe, args = launch_command()
    return " ".join(f'"{x}"' for x in [exe, *args])
