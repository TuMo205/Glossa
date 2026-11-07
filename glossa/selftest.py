"""Tự kiểm tra trên máy: OCR, dịch, ẩn lớp phủ khỏi ảnh chụp, phím tắt."""

from __future__ import annotations

import platform
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import winutil

OK, FAIL, WARN = "[OK]  ", "[LỖI] ", "[!]   "


def qimage_to_bgra(img) -> np.ndarray:
    from PySide6.QtGui import QImage

    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl // 4, 4)
    return arr[:, :w, :].copy()


SAMPLES = [
    ("latn", "Segoe UI", 22, "Hello world! This is a live screen translation test."),
    ("latn", "Segoe UI", 18, "Settings              Open file              Exit"),
    ("zh", "Microsoft YaHei", 24, "你好，世界。这是一个屏幕翻译测试。"),
    ("ja", "Yu Gothic UI", 24, "こんにちは、世界。これは画面翻訳のテストです。"),
    ("ko", "Malgun Gothic", 24, "안녕하세요 세계. 화면 번역 테스트입니다."),
    ("cyrl", "Segoe UI", 22, "Привет, мир! Это тест перевода экрана."),
]


def render_test_image(scripts: set) -> np.ndarray:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    rows = [s for s in SAMPLES if s[0] in scripts or s[0] == "latn"]
    h = 40 + 56 * len(rows)
    img = QImage(1000, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setPen(QColor(20, 20, 20))
    y = 24
    for _sc, family, px, text in rows:
        f = QFont(family)
        f.setPixelSize(px)
        p.setFont(f)
        p.drawText(QRectF(30, y, 960, 44), int(Qt.AlignmentFlag.AlignVCenter.value), text)
        y += 56
    p.end()
    return qimage_to_bgra(img)


def _exclusion_test(app) -> tuple:
    """Hiện một ô màu hồng 0,6 giây rồi chụp lại vùng đó để xem nó có bị ẩn khỏi ảnh chụp không."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QGuiApplication, QPainter
    from PySide6.QtWidgets import QWidget

    from .capture import Capturer

    class Probe(QWidget):
        def paintEvent(self, _e):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(255, 0, 255))
            p.end()

    w = Probe(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    g = QGuiApplication.primaryScreen().availableGeometry()
    w.setGeometry(g.x() + g.width() // 2 - 80, g.y() + g.height() // 2 - 80, 160, 160)
    w.show()

    def pump(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    pump(0.3)
    hwnd = int(w.winId())
    cap = Capturer()

    def magenta_fraction():
        r = winutil.get_window_rect(hwnd)
        frame = cap.grab(r)
        b, gch, rch = frame[..., 0], frame[..., 1], frame[..., 2]
        return float(((b > 220) & (gch < 50) & (rch > 220)).mean())

    winutil.set_exclude_from_capture(hwnd, False)
    pump(0.35)
    visible = magenta_fraction()
    applied = winutil.set_exclude_from_capture(hwnd, True)
    pump(0.35)
    hidden = magenta_fraction()
    w.close()
    cap.close()
    pump(0.1)
    return applied, visible, hidden


def run(cfg) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    ok_all = True
    print("=" * 64)
    print(" Glossa - TỰ KIỂM TRA")
    print("=" * 64)
    build = winutil.windows_build()
    print(f"Windows build {build} | Python {platform.python_version()} | {sys.executable}")
    print((OK if winutil.exclude_capture_supported() else WARN)
          + "Hỗ trợ ẩn lớp phủ khỏi ảnh chụp (cần build >= 19041)")

    pool = ThreadPoolExecutor(max_workers=1, initializer=winutil.com_init_mta)

    # --- 1. OCR ---
    print("\n[1] Nhận dạng chữ (Windows OCR)")
    texts = []
    try:
        from .layout import group_segments
        from .ocr_windows import WindowsOcr, lang_display_name, tag_script

        ocr = pool.submit(WindowsOcr, "auto", 4).result(60)
        print(OK + "Ngôn ngữ OCR đã cài: " + ", ".join(lang_display_name(t) for t in ocr.available))
        print("      Đang dùng (tự động): " + ", ".join(ocr.active_tags) + f" | ảnh tối đa {ocr.max_dim}px")
        img = render_test_image({tag_script(t) for t in ocr.active_tags})
        t0 = time.perf_counter()
        segs = pool.submit(ocr.recognize, img).result(60)
        ms = (time.perf_counter() - t0) * 1000
        blocks = group_segments(segs)
        for b in blocks:
            print(f"      • {b.text}")
            texts.append(b.text)
        big = np.full((1080, 1920, 4), 255, np.uint8)
        big[: img.shape[0], : img.shape[1]] = img
        t0 = time.perf_counter()
        pool.submit(ocr.recognize, big).result(60)
        ms_full = (time.perf_counter() - t0) * 1000
        good = any("hello" in t.lower() for t in texts)
        print((OK if good else FAIL) + f"Đọc được {len(blocks)} khối chữ ({ms:.0f} ms; ảnh 1920x1080: {ms_full:.0f} ms)")
        ok_all &= good
    except Exception as e:
        ok_all = False
        name = e.__class__.__name__
        if name == "NoOcrLanguage":
            print(FAIL + "Windows chưa có gói OCR nào. Chạy CAI_NGON_NGU_OCR.bat để cài.")
        else:
            print(FAIL + f"OCR lỗi: {name}: {e}")
            traceback.print_exc()

    # --- 2. Dịch ---
    print("\n[2] Dịch sang tiếng Việt")
    from .translator import TranslationCache, Translator

    tr = Translator(TranslationCache(), cfg.get("target_lang", "vi"))
    sample = texts[:6] or ["Hello world! This is a live screen translation test.", "Settings"]
    any_ok = False
    for name, prov in tr.providers.items():
        try:
            t0 = time.perf_counter()
            res = prov.translate(sample, "vi")
            ms = (time.perf_counter() - t0) * 1000
            print(OK + f"{name} ({ms:.0f} ms):")
            for src, (dst, _lang) in zip(sample, res):
                print(f"      {src}  →  {dst}")
            any_ok = True
        except Exception as e:
            print(WARN + f"{name}: {e.__class__.__name__}: {e}")
    if not any_ok:
        print(FAIL + "Không dịch được bằng dịch vụ nào - kiểm tra Internet/tường lửa.")
    ok_all &= any_ok

    # --- 3. Ẩn lớp phủ khỏi ảnh chụp ---
    print("\n[3] Ẩn lớp phủ khỏi ảnh chụp màn hình (một ô màu hồng sẽ nháy ~1 giây)")
    try:
        applied, visible, hidden = _exclusion_test(app)
        print(f"      Khi không ẩn: thấy {visible * 100:.0f}% ô hồng; khi ẩn: {hidden * 100:.0f}%")
        if applied and hidden < 0.05 and visible > 0.5:
            print(OK + "Lớp phủ không bị chụp lại → không tự dịch chính bản dịch")
        elif visible <= 0.5:
            print(WARN + "Không chụp được cửa sổ thử (có thể do màn hình đang khoá)")
        else:
            print(WARN + "Không ẩn được khỏi ảnh chụp → ứng dụng sẽ ẩn tạm lớp phủ mỗi lần chụp (hơi nháy)")
    except Exception as e:
        print(WARN + f"Không kiểm tra được: {e.__class__.__name__}: {e}")
        traceback.print_exc()

    # --- 4. Phím tắt ---
    print("\n[4] Phím tắt toàn cục")
    if not winutil.single_instance("Local\\Glossa.SingleInstance"):
        print(WARN + "Glossa đang chạy nên bỏ qua kiểm tra phím tắt")
    elif winutil.IS_WIN:
        import ctypes

        from .hotkeys import MOD_NOREPEAT, parse_hotkey

        for i, (name, combo) in enumerate(cfg.get("hotkeys", {}).items(), start=900):
            parsed = parse_hotkey(combo or "")
            if not parsed:
                print(WARN + f"{name}: '{combo}' không hợp lệ")
                continue
            if ctypes.windll.user32.RegisterHotKey(None, i, parsed[0] | MOD_NOREPEAT, parsed[1]):
                ctypes.windll.user32.UnregisterHotKey(None, i)
                print(OK + f"{combo:<12} ({name})")
            else:
                print(WARN + f"{combo:<12} ({name}) đang bị ứng dụng khác dùng")

    pool.shutdown(wait=False)
    print("\n" + ("TẤT CẢ ỔN - có thể dùng Glossa" if ok_all else "CÓ LỖI - xem chi tiết ở trên"))
    return 0 if ok_all else 1
