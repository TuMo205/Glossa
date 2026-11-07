"""Nhận dạng chữ bằng Windows OCR (Windows.Media.Ocr) - có sẵn trong Windows 10/11, chạy offline.

Lưu ý: tạo và gọi đối tượng này trong luồng đã khởi tạo COM kiểu MTA (xem engine.py).
"""

from __future__ import annotations

import importlib
import logging
import threading

import numpy as np

from .layout import Word, merge_engine_results, segments_from_lines

log = logging.getLogger(__name__)

# thứ tự ưu tiên khi chọn tự động; bộ đầu tiên làm nền
SCRIPT_PRIORITY = ["latn", "zh", "ja", "ko", "cyrl", "arab", "grek", "hebr", "thai"]

LANG_NAMES = {
    "en": "Tiếng Anh", "zh-hans": "Tiếng Trung (giản thể)", "zh-hant": "Tiếng Trung (phồn thể)",
    "zh": "Tiếng Trung", "ja": "Tiếng Nhật", "ko": "Tiếng Hàn", "ru": "Tiếng Nga",
    "uk": "Tiếng Ukraina", "fr": "Tiếng Pháp", "de": "Tiếng Đức", "es": "Tiếng Tây Ban Nha",
    "it": "Tiếng Ý", "pt": "Tiếng Bồ Đào Nha", "nl": "Tiếng Hà Lan", "pl": "Tiếng Ba Lan",
    "tr": "Tiếng Thổ Nhĩ Kỳ", "ar": "Tiếng Ả Rập", "el": "Tiếng Hy Lạp", "cs": "Tiếng Séc",
    "sv": "Tiếng Thụy Điển", "da": "Tiếng Đan Mạch", "fi": "Tiếng Phần Lan", "nb": "Tiếng Na Uy",
    "hu": "Tiếng Hungary", "ro": "Tiếng Romania", "sk": "Tiếng Slovakia", "hr": "Tiếng Croatia",
    "sr": "Tiếng Serbia", "bg": "Tiếng Bulgaria", "he": "Tiếng Do Thái", "th": "Tiếng Thái",
}


class NoOcrLanguage(RuntimeError):
    pass


def tag_script(tag: str) -> str:
    t = tag.lower()
    if t.startswith("ja"):
        return "ja"
    if t.startswith("zh"):
        return "zh"
    if t.startswith("ko"):
        return "ko"
    if t.split("-")[0] in ("ru", "uk", "be", "bg", "mk", "kk") or t.startswith("sr-cyrl"):
        return "cyrl"
    if t.split("-")[0] in ("ar", "fa", "ur"):
        return "arab"
    if t.startswith("el"):
        return "grek"
    if t.startswith("he"):
        return "hebr"
    if t.startswith("th"):
        return "thai"
    return "latn"


def lang_display_name(tag: str) -> str:
    t = tag.lower()
    for key in (t, "-".join(t.split("-")[:2]), t.split("-")[0]):
        if key in LANG_NAMES:
            return f"{LANG_NAMES[key]} ({tag})"
    return tag


def _static(cls, name):
    """Đọc thuộc tính tĩnh, tương thích nhiều phiên bản pywinrt."""
    v = getattr(cls, name, None)
    if v is not None and not callable(v):
        return v
    getter = getattr(cls, "get_" + name, None)
    if getter is not None:
        return getter()
    if callable(v):
        return v()
    raise AttributeError(name)


def resize_bgra(img: np.ndarray, s: float) -> np.ndarray:
    """Đổi kích thước ảnh BGRA (song tuyến tính). Dùng Qt (C++/SIMD, nhanh gấp ~10 lần),
    không có Qt thì dùng numpy. Kênh alpha bị bỏ qua (ảnh GDI thường có alpha = 0)."""
    try:
        return _resize_qt(img, s)
    except Exception:  # pragma: no cover - chỉ khi thiếu PySide6
        return _resize_numpy(img, s)


def _resize_qt(img: np.ndarray, s: float) -> np.ndarray:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    h, w = img.shape[:2]
    nh, nw = max(1, int(round(h * s))), max(1, int(round(w * s)))
    data = np.ascontiguousarray(img).tobytes()  # giữ tham chiếu tới hết hàm (QImage không sở hữu dữ liệu)
    src = QImage(data, w, h, w * 4, QImage.Format.Format_RGB32)
    dst = src.scaled(nw, nh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if dst.format() != QImage.Format.Format_RGB32:
        dst = dst.convertToFormat(QImage.Format.Format_RGB32)
    bpl = dst.bytesPerLine()
    out = np.frombuffer(dst.constBits(), dtype=np.uint8, count=bpl * nh).reshape(nh, bpl // 4, 4)
    return out[:, :nw, :].copy()


def _resize_numpy(img: np.ndarray, s: float) -> np.ndarray:
    h, w = img.shape[:2]
    nh, nw = max(1, int(round(h * s))), max(1, int(round(w * s)))
    ys = (np.arange(nh) + 0.5) / s - 0.5
    xs = (np.arange(nw) + 0.5) / s - 0.5
    y0 = np.clip(np.floor(ys).astype(np.int64), 0, h - 1)
    x0 = np.clip(np.floor(xs).astype(np.int64), 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    wy = np.clip(ys - y0, 0, 1)[:, None, None].astype(np.float32)
    wx = np.clip(xs - x0, 0, 1)[None, :, None].astype(np.float32)
    a = img[y0][:, x0].astype(np.float32)
    b = img[y0][:, x1].astype(np.float32)
    c = img[y1][:, x0].astype(np.float32)
    d = img[y1][:, x1].astype(np.float32)
    top = a + (b - a) * wx
    bot = c + (d - c) * wx
    return np.clip(top + (bot - top) * wy + 0.5, 0, 255).astype(np.uint8)


class WindowsOcr:
    TIMEOUT = 20.0

    def __init__(self, languages="auto", max_engines: int = 3):
        importlib.import_module("winrt.windows.globalization")  # để bọc được đối tượng Language
        importlib.import_module("winrt.windows.foundation.collections")
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter

        self._SoftwareBitmap = SoftwareBitmap
        self._BGRA8 = BitmapPixelFormat.BGRA8
        self._DataWriter = DataWriter

        avail = list(_static(OcrEngine, "available_recognizer_languages"))
        self.available = [lang.language_tag for lang in avail]
        try:
            self.max_dim = int(_static(OcrEngine, "max_image_dimension"))
        except Exception:
            self.max_dim = 4096
        self.engines = []
        for lang in self._choose(avail, languages, max_engines):
            eng = OcrEngine.try_create_from_language(lang)
            if eng is not None:
                self.engines.append((lang.language_tag, tag_script(lang.language_tag), eng))
        if not self.engines:
            raise NoOcrLanguage("Windows chưa cài gói nhận dạng chữ (OCR) cho ngôn ngữ nào")
        log.info("OCR: có %s; dùng %s; ảnh tối đa %d px",
                 self.available, self.active_tags, self.max_dim)

    @property
    def active_tags(self) -> list:
        return [t for t, _, _ in self.engines]

    @staticmethod
    def _choose(avail: list, languages, max_engines: int) -> list:
        if languages and languages != "auto":
            wanted = [str(x).lower() for x in (languages if isinstance(languages, list) else [languages])]
            chosen = []
            for w in wanted:
                for lang in avail:
                    t = lang.language_tag.lower()
                    if (t == w or t.startswith(w + "-") or w.startswith(t)) and lang not in chosen:
                        chosen.append(lang)
                        break
            if chosen:
                return chosen
        by_script: dict = {}
        for lang in avail:
            sc = tag_script(lang.language_tag)
            t = lang.language_tag.lower()
            prefer = (sc == "latn" and t.startswith("en")) or (sc == "zh" and "hans" in t) or (
                sc == "zh" and t in ("zh-cn", "zh-sg"))
            if sc not in by_script or prefer:
                by_script[sc] = lang
        return [by_script[s] for s in SCRIPT_PRIORITY if s in by_script][: max(1, max_engines)]

    # -- nhận dạng --
    def _bitmap(self, img: np.ndarray):
        writer = self._DataWriter()
        writer.write_bytes(img.tobytes())
        buf = writer.detach_buffer()
        return self._SoftwareBitmap.create_copy_from_buffer(buf, self._BGRA8, img.shape[1], img.shape[0])

    def _wait(self, op):
        done = threading.Event()
        op.completed = lambda _op, _status: done.set()
        if not done.wait(self.TIMEOUT):
            try:
                op.cancel()
            except Exception:
                pass
            raise TimeoutError("Windows OCR không phản hồi")
        return op.get_results()

    supports_scripts = True  # recognize() nhận tham số scripts (chỉ chạy một số bộ OCR)

    @property
    def scripts(self) -> list:
        return [sc for _t, sc, _e in self.engines]

    def recognize(self, bgra: np.ndarray, scale: float = 1.0, scripts=None) -> list:
        """bgra: ảnh (H, W, 4). Trả về danh sách Segment theo toạ độ ảnh gốc.
        scripts: chỉ chạy các bộ OCR của những hệ chữ này (bộ nền đầu tiên luôn chạy); None = tất cả."""
        h, w = bgra.shape[:2]
        s = float(scale)
        if max(w, h) * s > self.max_dim:
            s = self.max_dim / max(w, h)
        img = resize_bgra(bgra, s) if abs(s - 1.0) > 1e-3 else np.array(bgra, copy=True)
        img[..., 3] = 255
        ih, iw = img.shape[:2]
        if iw < 48 or ih < 48:  # OCR không thích ảnh quá nhỏ: đệm thêm viền
            ph, pw = max(0, 48 - ih), max(0, 48 - iw)
            img = np.pad(img, ((0, ph), (0, pw), (0, 0)), mode="edge")
        img = np.ascontiguousarray(img)
        # khởi động mọi bộ OCR cùng lúc (mỗi bộ chạy trên luồng riêng của Windows) rồi mới chờ:
        # thời gian = bộ chậm nhất thay vì tổng các bộ
        engines = [e for i, e in enumerate(self.engines) if scripts is None or i == 0 or e[1] in scripts]
        ops = [(script, eng.recognize_async(self._bitmap(img))) for _tag, script, eng in engines]
        results = []
        for script, op in ops:
            res = self._wait(op)
            lines = []
            for line in res.lines:
                words = []
                for wd in line.words:
                    r = wd.bounding_rect
                    words.append(Word(wd.text, r.x / s, r.y / s, r.width / s, r.height / s))
                lines.append(words)
            results.append((script, segments_from_lines(lines)))
        return merge_engine_results(results)
