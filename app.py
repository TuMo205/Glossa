"""Glossa - dịch mọi chữ trên màn hình sang tiếng Việt, liên tục, ngay tại chỗ.

Chạy ngầm:      pythonw app.py          (bản cài: Glossa.exe)
Tự kiểm tra:    python app.py --selftest (bản cài: Glossa.exe --selftest)
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from glossa import __version__, paths, winutil  # noqa: E402


def _fix_std_streams() -> None:
    # pythonw không có console: sys.stdout/stderr = None
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logging(level: str = "INFO", console: bool = False) -> None:
    log_dir = paths.data_path("logs")
    os.makedirs(log_dir, exist_ok=True)
    handlers: list = [RotatingFileHandler(os.path.join(log_dir, "glossa.log"),
                                          maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=getattr(logging, str(level).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
                        handlers=handlers)
    # không bao giờ ghi URL yêu cầu dịch (chứa chữ trên màn hình) vào log, kể cả khi --debug
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    crash = logging.getLogger("crash")
    sys.excepthook = lambda t, v, tb: crash.critical("Lỗi không mong muốn", exc_info=(t, v, tb))
    threading.excepthook = lambda a: crash.critical(
        "Lỗi trong luồng %s", getattr(a.thread, "name", "?"),
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def cmd_check_ocr() -> int:
    from concurrent.futures import ThreadPoolExecutor

    def job():
        from winrt.windows.media.ocr import OcrEngine

        from glossa.ocr_windows import _static

        return [lang.language_tag for lang in _static(OcrEngine, "available_recognizer_languages")]

    try:
        langs = ThreadPoolExecutor(1, initializer=winutil.com_init_mta).submit(job).result(60)
    except Exception as e:
        print("Không đọc được danh sách ngôn ngữ OCR:", e)
        logging.getLogger("app").error("check-ocr: %r", e)
        return 3
    logging.getLogger("app").info("check-ocr: %s", langs)
    if langs:
        print("Ngôn ngữ OCR đã cài:", ", ".join(langs))
        return 0
    print("Windows chưa cài ngôn ngữ OCR nào.")
    return 2


def cmd_check_imports() -> int:
    """Kiểm tra bản đóng gói: nạp đủ mọi thành phần (Qt, Windows OCR, chứng chỉ HTTPS, tài liệu)."""
    import importlib

    mods = ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtNetwork", "numpy", "mss",
            "requests", "certifi", "glossa.controller", "glossa.selftest", "glossa.legacy"]
    if sys.platform == "win32":
        mods += ["winrt.windows.foundation", "winrt.windows.foundation.collections",
                 "winrt.windows.globalization", "winrt.windows.graphics.imaging", "winrt.windows.media.ocr",
                 "winrt.windows.storage.streams"]
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            bad.append(f"thiếu {m}: {e!r}")
    for f in ("HUONG_DAN.md", "install_ocr_languages.ps1"):
        if not os.path.isfile(paths.app_path(f)):
            bad.append(f"thiếu file {f}")
    try:
        import certifi

        if not os.path.isfile(certifi.where()):
            bad.append("thiếu chứng chỉ HTTPS (certifi)")
    except Exception:
        pass
    log = logging.getLogger("app")
    log.info("check-imports: %s", "OK" if not bad else "; ".join(bad))
    print("\n".join(bad) if bad else f"OK - Glossa {__version__}, dữ liệu ở {paths.DATA_DIR}")
    return 1 if bad else 0


def cmd_shortcut() -> int:
    """Tạo lối tắt Glossa trên Desktop."""
    exe, args = paths.launch_command()
    qapp = None
    if paths.FROZEN:
        ico = exe + ",0"  # biểu tượng có sẵn trong Glossa.exe
    else:
        from PySide6.QtGui import QGuiApplication

        from glossa.icons import save_ico

        qapp = QGuiApplication(sys.argv[:1])
        ico = paths.app_path("glossa.ico")
        if not save_ico(ico):
            ico = ""

    def q(s: str) -> str:
        return s.replace("'", "''")

    ps = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        "$lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\\Glossa.lnk')\n"
        f"$lnk.TargetPath = '{q(exe)}'\n"
        f"$lnk.Arguments = '{q(' '.join(chr(34) + a + chr(34) for a in args))}'\n"
        f"$lnk.WorkingDirectory = '{q(paths.EXE_DIR)}'\n"
        + (f"$lnk.IconLocation = '{q(ico)}'\n" if ico else "")
        + "$lnk.Description = 'Glossa - dich man hinh sang tieng Viet'\n"
        "$lnk.Save()\n"
    )
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode == 0:
        print("Đã tạo lối tắt 'Glossa' trên Desktop.")
        if qapp is not None:
            qapp.quit()
        return 0
    print("Không tạo được lối tắt:", (r.stderr or r.stdout).strip()[:300])
    return 1


def ask_running_instance_to_show_panel() -> bool:
    """Mở lối tắt khi chương trình đang chạy: nhờ bản đang chạy hiện bảng điều khiển (như Unikey)."""
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QLocalSocket

        qapp = QCoreApplication.instance() or QCoreApplication(sys.argv)  # QLocalSocket cần có app
        winutil.allow_foreground()
        sock = QLocalSocket()
        sock.connectToServer(winutil.ipc_name())
        if not sock.waitForConnected(1500):
            return False
        sock.write(b"show\n")
        sock.flush()
        sock.waitForBytesWritten(1000)
        sock.waitForDisconnected(1000)
        del qapp
        return True
    except Exception:
        logging.getLogger("app").exception("Không liên lạc được với bản đang chạy")
        return False


CLI_FLAGS = ("--selftest", "--check-ocr", "--check-imports", "--autostart", "--shortcut", "--version",
             "-h", "--help")


def main() -> int:
    cli = any(a.split("=")[0] in CLI_FLAGS for a in sys.argv[1:])
    new_console = False
    if paths.FROZEN and cli:  # Glossa.exe không có cửa sổ dòng lệnh riêng
        new_console = winutil.use_console(allow_new="--selftest" in sys.argv)
    _fix_std_streams()
    try:
        return _main(cli)
    finally:
        if new_console:
            try:
                input("\nNhấn Enter để đóng...")
            except Exception:
                pass


def _main(cli: bool) -> int:
    ap = argparse.ArgumentParser(prog="Glossa", description="Glossa - dịch màn hình sang tiếng Việt")
    ap.add_argument("--selftest", action="store_true", help="tự kiểm tra OCR, dịch, lớp phủ")
    ap.add_argument("--check-ocr", action="store_true", help="liệt kê ngôn ngữ OCR đã cài")
    ap.add_argument("--autostart", choices=["on", "off"], help="bật/tắt khởi động cùng Windows")
    ap.add_argument("--shortcut", action="store_true", help="tạo lối tắt trên Desktop")
    ap.add_argument("--check-imports", action="store_true", help="kiểm tra bản đóng gói có đủ thành phần")
    ap.add_argument("--version", action="version", version=f"Glossa {__version__}")
    ap.add_argument("--debug", action="store_true", help="ghi log chi tiết")
    args = ap.parse_args()

    from glossa import legacy
    from glossa.config import Config

    paths.ensure_data_dir()
    migrated = [] if cli else legacy.migrate()
    cfg = Config()
    setup_logging("DEBUG" if args.debug else cfg.get("log_level", "INFO"), console=args.debug)
    log = logging.getLogger("app")
    for item in migrated:
        log.info("Bản cũ: %s", item)

    if args.check_ocr:
        return cmd_check_ocr()
    if args.check_imports:
        return cmd_check_imports()
    if args.autostart:
        ok = winutil.set_autostart(args.autostart == "on")
        print(("Đã bật" if args.autostart == "on" else "Đã tắt") + " khởi động cùng Windows." if ok else "Lỗi!")
        return 0 if ok else 1
    if args.shortcut:
        return cmd_shortcut()
    if args.selftest:
        from glossa.selftest import run

        return run(cfg)

    if not winutil.single_instance():
        if ask_running_instance_to_show_panel():
            return 0
        winutil.message_box("Glossa đang chạy rồi.\n"
                            "Tìm biểu tượng chữ V (đỏ) / E (xanh) ở khay hệ thống, góc phải thanh tác vụ.")
        return 0
    if legacy.old_version_running():
        winutil.message_box("Bản cũ (ViTranslator) đang chạy - hai bản cùng chạy sẽ trùng phím tắt và hiện hai "
                            "lớp dịch.\n\nHãy tắt bản cũ: chuột phải biểu tượng V/E ở khay hệ thống → Kết thúc "
                            "(hoặc bấm Ctrl+Alt+Q), rồi bấm OK.", icon=0x30)
        if legacy.old_version_running():
            winutil.message_box("Bản cũ vẫn đang chạy nên Glossa chưa mở. Tắt bản cũ rồi mở lại Glossa.")
            return 0

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Glossa")

    from glossa.controller import Controller

    log.info("Khởi động Glossa %s (%s), dữ liệu: %s", __version__, sys.executable, paths.DATA_DIR)
    app.controller = Controller(app, cfg, paths.launch_command_line())  # giữ tham chiếu
    code = app.exec()
    log.info("Thoát (%s)", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
