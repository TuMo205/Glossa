"""Gom kết quả OCR (từ -> đoạn trên một dòng -> khối văn bản) để dịch theo ngữ cảnh.

Toạ độ đều là pixel thật của ảnh chụp (x, y = góc trên trái).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from .textutil import (
    clean_text,
    is_nospace_char,
    join_lines,
    join_words,
    script_counts,
)


@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class Segment:
    """Một cụm từ liền nhau trên cùng một dòng."""

    text: str
    x: float
    y: float
    w: float
    h: float
    line_h: float
    med_h: float = 0.0  # trung vị chiều cao các từ (bền với khung OCR lỗi)
    hi: bool = False    # đã đọc ở độ phân giải cao (2×) - không cần đọc lại
    script: str = ""    # hệ chữ của bộ OCR đã đọc ra dòng này (latn, zh, ja, ko, cyrl...)

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def offset(self, dx: float, dy: float) -> "Segment":
        return Segment(self.text, self.x + dx, self.y + dy, self.w, self.h, self.line_h, self.med_h, self.hi,
                       self.script)

    @property
    def font_px(self) -> float:
        return estimate_font_px(self.text, self.h, self.med_h)

    def rect(self) -> tuple:
        return (self.x, self.y, self.right, self.bottom)


@dataclass
class Block:
    """Một khối văn bản (một hoặc nhiều dòng) sẽ được dịch chung."""

    lines: list = field(default_factory=list)

    @property
    def x(self) -> float:
        return min(s.x for s in self.lines)

    @property
    def y(self) -> float:
        return min(s.y for s in self.lines)

    @property
    def right(self) -> float:
        return max(s.right for s in self.lines)

    @property
    def bottom(self) -> float:
        return max(s.bottom for s in self.lines)

    @property
    def w(self) -> float:
        return self.right - self.x

    @property
    def h(self) -> float:
        return self.bottom - self.y

    @property
    def line_h(self) -> float:
        return median(s.line_h for s in self.lines)

    @property
    def n_lines(self) -> int:
        return len(self.lines)

    @property
    def font_px(self) -> float:
        """Cỡ chữ gốc ước lượng (pixel)."""
        return median(estimate_font_px(s.text, s.h, s.med_h) for s in self.lines)

    @property
    def text(self) -> str:
        return join_lines([s.text for s in self.lines])

    def baselines(self, font_px: float | None = None) -> list:
        px = font_px or self.font_px
        return [estimate_baseline(s.text, s.bottom, px) for s in self.lines]

    @property
    def pitch(self) -> float:
        """Khoảng cách giữa hai đường chân chữ liên tiếp (0 nếu chỉ một dòng)."""
        if len(self.lines) < 2:
            return 0.0
        bl = self.baselines()
        return median(b - a for a, b in zip(bl, bl[1:]))


_ASC = set("bdfhijklt0123456789!?%&#@/\\|'\"")  # i, j: dấu chấm cao gần bằng nét lên
_DESC = set("gjpqy")
_BOTH = set("()[]{}$")

# Tỉ lệ (chiều cao khung OCR / cỡ chữ) đo trên chính phông vẽ bản dịch (Segoe UI) bằng
# tools/calibrate_font.py. Ước lượng ra "cỡ Segoe UI tương đương": vẽ bản dịch bằng cỡ đó thì
# chữ cao đúng bằng chữ gốc, kể cả khi bản gốc dùng Arial, Calibri, Times...
RATIO_ASC_DESC = 0.99   # có nét lên và nét xuống: "Settings", "download"
RATIO_ASC = 0.775       # chỉ nét lên: "This window"
RATIO_CAPS = 0.725      # chỉ chữ hoa / số: "HELLO WORLD"
RATIO_DESC = 0.745      # chỉ nét xuống: "gray yoga"
RATIO_XH = 0.512        # toàn nét thấp: "summer cocoa"
RATIO_CJK = 0.975       # chữ Hán/Kana/Hangul
DESC_DEPTH = 0.235      # nét xuống thò dưới đường chân chữ (phần của cỡ chữ)
CJK_DEPTH = 0.14


def text_profile(text: str) -> tuple:
    """(có nét lên, có nét xuống, chỉ chữ hoa/số, là chữ Hán/Kana/Hangul)."""
    asc = desc = lower_asc = False
    for c in text:
        if is_nospace_char(c) or "가" <= c <= "힯":
            return False, False, False, True
        if c in _BOTH:
            asc = desc = lower_asc = True
        elif c.isupper() or c in _ASC or (0xC0 <= ord(c) <= 0x24F or 0x1E00 <= ord(c) <= 0x1EFF):
            asc = True  # chữ hoa, chữ có nét lên, chữ Latin có dấu
            if not (c.isupper() or c.isdigit()):
                lower_asc = True
        if c in _DESC:
            desc = True
    caps_only = asc and not lower_asc and not desc and not any(c.islower() for c in text)
    return asc, desc, caps_only, False


def estimate_font_px(text: str, box_h: float, med_h: float = 0.0) -> float:
    """Khung OCR ôm sát nét chữ, nên chiều cao khung phụ thuộc chữ có nét lên/xuống hay không.
    Suy ra cỡ chữ thật từ chiều cao khung và nội dung."""
    if med_h > 0:
        box_h = min(box_h, 1.6 * med_h)  # loại khung từ bị lỗi quá cao
    asc, desc, caps_only, cjk = text_profile(text)
    if cjk:  # chữ Hán/Kana/Hangul cao đều nhau: dùng trung vị
        return (med_h or box_h) / RATIO_CJK
    if asc and desc:
        ratio = RATIO_ASC_DESC
    elif caps_only:
        ratio = RATIO_CAPS
    elif asc:
        ratio = RATIO_ASC
    elif desc:
        ratio = RATIO_DESC
    else:
        ratio = RATIO_XH
    return box_h / ratio


def estimate_baseline(text: str, bottom: float, font_px: float) -> float:
    """Toạ độ y của đường chân chữ (baseline) từ đáy khung OCR."""
    _asc, desc, _caps, cjk = text_profile(text)
    if cjk:
        return bottom - CJK_DEPTH * font_px
    return bottom - (DESC_DEPTH * font_px if desc else 0.01 * font_px)


# --- Bước 1: tách dòng OCR thành các cụm ----------------------------------

def split_line(words: list, gap_factor: float = 1.25) -> list:
    """Tách một dòng OCR thành các cụm khi khoảng trống giữa hai từ quá lớn
    (ví dụ thanh menu, cột bảng)."""
    ws = [w for w in words if w.text and w.text.strip() and w.w > 0 and w.h > 0]
    if not ws:
        return []
    ws.sort(key=lambda w: w.x)
    lh = median(w.h for w in ws)
    groups = [[ws[0]]]
    for w in ws[1:]:
        prev = groups[-1][-1]
        gap = w.x - (prev.x + prev.w)
        if gap > gap_factor * lh:
            groups.append([w])
        else:
            groups[-1].append(w)
    segs = []
    for g in groups:
        x0 = min(w.x for w in g)
        y0 = min(w.y for w in g)
        x1 = max(w.x + w.w for w in g)
        y1 = max(w.y + w.h for w in g)
        text = clean_text(join_words([w.text for w in g]))
        if text:
            segs.append(Segment(text, x0, y0, x1 - x0, y1 - y0, y1 - y0, median(w.h for w in g)))
    return segs


def segments_from_lines(lines: list, gap_factor: float = 1.25) -> list:
    out = []
    for words in lines:
        out.extend(split_line(words, gap_factor))
    return out


# --- Bước 2: gom các dòng thành đoạn văn ----------------------------------

_LIST_START = re.compile(r"^([•●○■□▪▫◦‣\-–—*·>»✓✔]|\(?\d{1,3}[.)]\s|\(?[a-zA-Z][.)]\s)")
_CONT_END = set(",;-–—/(&+:")
_CJK_STOP = set("。！？!?：:")


def _continues(a: Segment, b: Segment, block_right: float) -> bool:
    """Dòng b có phải là phần nối tiếp của dòng a (văn bản bị xuống dòng)?"""
    at, bt = a.text.strip(), b.text.strip()
    if not at or not bt:
        return False
    if _LIST_START.match(bt):
        return False
    first, last = bt[0], at[-1]
    h = (a.line_h + b.line_h) / 2
    a_full = a.right >= block_right - 1.6 * h
    if is_nospace_char(last) and is_nospace_char(first):
        # đoạn văn Hán/Nhật bị xuống dòng: dòng trên phải dài và kín bề ngang
        return a_full and a.w >= 6 * a.line_h and last not in _CJK_STOP
    # dòng trên phải đủ dài (≈ 12+ ký tự) mới có khả năng là văn bản bị ngắt dòng
    long_a = a.w >= 6 * a.line_h
    if first.isalpha() and first.islower() and long_a:
        return True
    if last in _CONT_END and long_a:
        return True
    return False


def group_segments(
    segs: list,
    max_gap: float = 0.8,
    height_ratio: float = 1.45,
    align_tol: float = 1.0,
) -> list:
    """Gom các cụm thành khối (đoạn văn). Bảo thủ: chỉ nối khi chắc là văn bản xuống dòng."""
    order = sorted(segs, key=lambda s: (round(s.y), s.x))
    blocks: list = []
    open_blocks: list = []
    for s in order:
        best, best_gap = None, None
        for b in open_blocks:
            a = b.lines[-1]
            h = (a.line_h + s.line_h) / 2
            gap = s.y - a.bottom
            if gap < -0.35 * h or gap > max_gap * h:
                continue
            hi, lo = max(a.line_h, s.line_h), max(1e-6, min(a.line_h, s.line_h))
            if hi / lo > height_ratio:
                continue
            left_ok = abs(s.x - b.x) <= align_tol * h
            overlap = min(a.right, s.right) - max(a.x, s.x)
            centered = (
                overlap > 0.6 * min(a.w, s.w)
                and abs((a.x + a.w / 2) - (s.x + s.w / 2)) <= 1.5 * h
            )
            if not (left_ok or centered):
                continue
            if not _continues(a, s, b.right):
                continue
            if best is None or gap < best_gap:
                best, best_gap = b, gap
        if best is not None:
            best.lines.append(s)
        else:
            nb = Block([s])
            blocks.append(nb)
            open_blocks.append(nb)
        open_blocks = [b for b in open_blocks if s.y - b.bottom <= 2.5 * b.lines[-1].line_h]
    return blocks


# --- Gộp kết quả của nhiều bộ OCR (mỗi bộ một ngôn ngữ) -------------------

_SCRIPT_KEYS = {
    "zh": ("han",),
    "ja": ("kana", "han"),
    "ko": ("hangul",),
    "cyrl": ("cyrl",),
    "arab": ("arab",),
    "grek": ("grek",),
    "hebr": ("hebr",),
    "thai": ("thai",),
}


def _overlap_ratio(a: Segment, b: Segment) -> float:
    ix = min(a.right, b.right) - max(a.x, b.x)
    iy = min(a.bottom, b.bottom) - max(a.y, b.y)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    return inter / max(1e-6, min(a.w * a.h, b.w * b.h))


def _has_script(text: str, script: str, need_kana: bool) -> bool:
    keys = _SCRIPT_KEYS.get(script)
    if not keys:
        return False
    c = script_counts(text)
    if script == "ja" and need_kana and c.get("kana", 0) == 0:
        return False
    n = sum(c.get(k, 0) for k in keys)
    return n >= 1 and n >= 0.3 * max(1, c["letters"])


def merge_engine_results(results: list) -> list:
    """results = [(script, [Segment...]), ...] theo thứ tự ưu tiên.
    Bộ đầu tiên làm nền; các bộ sau chỉ thay thế vùng có chữ đúng hệ chữ của nó."""
    if not results:
        return []
    for script, segs in results:
        for s in segs:
            s.script = script
    merged = list(results[0][1])
    has_zh = any(sc == "zh" for sc, _ in results)
    for script, segs in results[1:]:
        for s in segs:
            if not _has_script(s.text, script, need_kana=has_zh):
                continue
            merged = [m for m in merged if _overlap_ratio(m, s) < 0.35]
            merged.append(s)
    return merged
