"""Hiệu chỉnh ước lượng cỡ chữ: vẽ chữ với cỡ biết trước rồi OCR (chạy trên Windows).

    .venv\\Scripts\\python tools\\calibrate_font.py > calib.txt

In ra từng dòng: phông|cỡ thật|mã câu|tỉ lệ OCR|chiều cao khung|trung vị cao từ|ước lượng|chữ OCR
và cuối cùng là số đo của phông vẽ bản dịch (Segoe UI) tại 100 px.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter  # noqa: E402

TEXTS = {
    "mix": "Your download is complete",
    "asc": "This window has details",
    "xh": "summer cocoa season",
    "desc": "a quiet morning gym",
    "caps": "HELLO WORLD",
    "set": "Settings",
    "cjk": "你好世界欢迎使用",
}
FONTS = ["Segoe UI", "Arial", "Calibri", "Times New Roman", "Tahoma", "Verdana"]
SIZES = [11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40]


def render(family: str):
    rows = []
    y = 10.0
    for px in SIZES:
        for tid, text in TEXTS.items():
            fam = "Microsoft YaHei" if tid == "cjk" else family
            rows.append((px, tid, fam, text, y))
            y += px * 2.4
    img = QImage(1100, int(y + 20), QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setPen(QColor(25, 25, 25))
    for px, tid, fam, text, y0 in rows:
        f = QFont(fam)
        f.setPixelSize(px)
        p.setFont(f)
        p.drawText(QPointF(20, y0 + px * 1.2), text)  # baseline
    p.end()
    import numpy as np

    bpl = img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), np.uint8, count=bpl * img.height()).reshape(img.height(), bpl // 4, 4)
    return rows, arr[:, :1100].copy()


def main() -> None:  # noqa: C901
    qapp = QGuiApplication(sys.argv)  # cần cho phông chữ
    from glossa import winutil
    from glossa.layout import estimate_font_px

    pool = ThreadPoolExecutor(1, initializer=winutil.com_init_mta)
    ocr = pool.submit(lambda: __import__("glossa.ocr_windows", fromlist=["x"]).WindowsOcr("auto", 3)).result(60)
    for family in FONTS:
        rows, arr = render(family)
        for scale in (1.0, 2.0):
            segs = pool.submit(ocr.recognize, arr, scale).result(120)
            for px, tid, fam, text, y0 in rows:
                base = y0 + px * 1.2
                cand = [s for s in segs if s.y < base + px * 0.4 and s.bottom > base - px * 0.9]
                if not cand:
                    print(f"{family}|{px}|{tid}|{scale}|-|-|-|-|(không đọc được)")
                    continue
                s = max(cand, key=lambda s: s.w)
                est = estimate_font_px(s.text, s.h, s.med_h)
                print(f"{family}|{px}|{tid}|{scale}|{s.h:.1f}|{s.med_h:.1f}|{est:.1f}|{s.y - base:.1f}|{s.text}")
    f = QFont("Segoe UI")
    f.setPixelSize(100)
    fm = QFontMetricsF(f)
    print(f"METRICS Segoe UI @100px: ascent={fm.ascent():.1f} descent={fm.descent():.1f} cap={fm.capHeight():.1f} "
          f"xh={fm.xHeight():.1f} height={fm.height():.1f} lineSpacing={fm.lineSpacing():.1f}")
    pool.shutdown()
    qapp.quit()


if __name__ == "__main__":
    main()
