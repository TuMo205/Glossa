"""Bố trí bản dịch trên lớp phủ.

Nguyên tắc (để dễ đọc, không nhức mắt):
- Bản dịch dùng ĐÚNG cỡ chữ gốc và nằm trên cùng đường chân chữ (baseline) với chữ gốc.
  Câu dài hơn thì nới khung sang phải/xuống dưới, KHÔNG thu nhỏ chữ.
- Các khối có cỡ chỉ lệch nhau ở mức sai số đo (<= 6%) dùng chung một cỡ; cỡ ước lượng được làm
  mượt theo thời gian và chỉ đổi khi lệch rõ (> 6%) để chữ không nhảy cỡ giữa các lần quét.
- Khối nào trùng vị trí với khối cũ thì giữ nguyên vị trí/cỡ/màu/bản dịch cũ nếu chỉ lệch chút ít
  do OCR (chống giật).

Toạ độ trong module này là pixel logic của cửa sổ lớp phủ.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from difflib import SequenceMatcher

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTextLayout, QTextOption

from .textutil import norm_key

SIZE_TOL = 0.06        # gom các cỡ lệch nhau trong mức sai số đo (~±5%)
SNAP_TOL = 0.05        # chỉ kéo về cỡ chung khi cách cỡ chung <= 5%, xa hơn thì giữ cỡ riêng
HOLD_TOL = 0.06        # khối đang hiện chỉ đổi cỡ khi ước lượng mới lệch > 6%
SMOOTH = 0.4           # trọng số của lần đo mới khi làm mượt cỡ ước lượng


def make_font(family: str, px: float, bold: bool = False) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(6, int(round(px))))
    if bold:
        f.setWeight(QFont.Weight.Bold)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


# --- cỡ chữ ------------------------------------------------------------------

def unify_sizes(values: list, weights: list | None = None, tol: float = SIZE_TOL,
                snap: float = SNAP_TOL) -> list:
    """Cỡ chữ (số nguyên px) cho từng khối: các cỡ chỉ chênh nhau ở mức sai số đo được gom thành
    một nhóm và dùng chung cỡ trung vị có trọng số (đoạn văn dài đo chính xác hơn nhãn ngắn).
    Khối nào cách cỡ chung quá `snap` thì giữ cỡ của chính nó (không bị đoạn văn kéo theo)."""
    n = len(values)
    if not n:
        return []
    w = list(weights) if weights else [1.0] * n
    order = sorted(range(n), key=lambda i: values[i])
    out = [0] * n
    group: list = []

    def flush() -> None:
        total = sum(w[i] for i in group)
        acc, center = 0.0, values[group[-1]]
        for i in group:
            acc += w[i]
            if acc >= total / 2:
                center = values[i]
                break
        c = max(6, int(round(center)))
        for i in group:
            v = values[i]
            out[i] = c if abs(c - v) <= snap * v + 0.5 else max(6, int(round(v)))

    for i in order:
        if group and values[i] > values[group[0]] * (1 + tol):
            flush()
            group = []
        group.append(i)
    flush()
    return out


# --- khớp khối mới với khối cũ ----------------------------------------------------

def iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = min(ax + aw, bx + bw) - max(ax, bx)
    iy = min(ay + ah, by + bh) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    return inter / max(1e-6, aw * ah + bw * bh - inter)


def match_rects(new: list, old: list, min_iou: float = 0.4) -> list:
    """Ghép mỗi khung mới với một khung cũ (tham lam theo IoU). Trả về chỉ số khung cũ hoặc -1."""
    pairs = []
    order = sorted(range(len(old)), key=lambda j: old[j][1])
    oy = [old[j][1] for j in order]
    max_h = max((o[3] for o in old), default=0)
    for i, r in enumerate(new):
        lo = bisect.bisect_left(oy, r[1] - max_h)
        hi = bisect.bisect_right(oy, r[1] + r[3])
        for k in range(lo, hi):
            j = order[k]
            v = iou(r, old[j])
            if v >= min_iou:
                pairs.append((v, i, j))
    pairs.sort(key=lambda t: (-t[0], t[2]))  # IoU cao trước; bằng nhau thì ưu tiên khung cũ đứng trước
    out = [-1] * len(new)
    used = set()
    for _v, i, j in pairs:
        if out[i] < 0 and j not in used:
            out[i] = j
            used.add(j)
    return out


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def likely_misread(a: str, b: str) -> bool:
    """b có phải là cùng dòng chữ a nhưng OCR đọc sai vài ký tự không (số liệu phải giống hệt)."""
    if norm_key(a) == norm_key(b):
        return True
    return _digits(a) == _digits(b) and similarity(a, b) >= 0.75


def similarity(a: str, b: str) -> float:
    ka, kb = norm_key(a), norm_key(b)
    if not ka or not kb:
        return 0.0
    return SequenceMatcher(None, ka, kb, autojunk=False).ratio()


# --- khoảng trống & xuống dòng ------------------------------------------------------

def horizontal_room(rects: list, left_bound: float, right_bound: float, gap: float = 4.0) -> list:
    """Với mỗi khung (x, y, w, h): khoảng ngang (trái, phải) có thể nới ra mà không đè lên
    khối khác trên cùng hàng."""
    order = sorted(range(len(rects)), key=lambda i: rects[i][1])
    ys = [rects[i][1] for i in order]
    max_h = max((r[3] for r in rects), default=0)
    out = []
    for i, (x, y, w, h) in enumerate(rects):
        left, right = left_bound, right_bound
        lo = bisect.bisect_left(ys, y - max_h)
        hi = bisect.bisect_right(ys, y + h)
        for k in range(lo, hi):
            j = order[k]
            if j == i:
                continue
            x2, y2, w2, h2 = rects[j]
            if min(y + h, y2 + h2) - max(y, y2) < 0.4 * min(h, h2):
                continue
            if x2 >= x + w * 0.5:
                right = min(right, x2 - gap)
            elif x2 + w2 <= x + w * 0.5:
                left = max(left, x2 + w2 + gap)
        out.append((min(left, x), max(right, x + w)))
    return out


def wrap(text: str, font: QFont, width: float) -> list:
    """Xuống dòng theo từ. Trả về [(chuỗi, bề rộng), ...]."""
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1.0, width))
        lines.append((line.textStart(), line.textLength(), line.naturalTextWidth()))
    layout.endLayout()
    return [(text[s:s + n].strip(), w) for s, n, w in lines if text[s:s + n].strip()] or [(text, width)]


# --- đặt chữ -------------------------------------------------------------------------

@dataclass
class Placement:
    rect: QRectF          # nền che chữ gốc
    lines: list           # [(x, baseline_y, chuỗi)]
    font: QFont


def place_text(text: str, x: float, y: float, w: float, h: float, baseline: float, pitch: float,
               n_lines: int, px: int, family: str, room: tuple, bounds: QRectF, bold: bool = False,
               min_left: float | None = None) -> Placement:
    """Đặt bản dịch với cỡ px (bằng cỡ gốc), dòng đầu nằm trên baseline của dòng gốc đầu tiên.

    min_left: mép phải của bản dịch đứng trước trên cùng hàng - bản dịch này bắt đầu sau đó
    (thanh menu, thanh công cụ: các mục dịch dài hơn được xếp nối tiếp thay vì đè lên nhau)."""
    font = make_font(family, px, bold)
    fm = QFontMetricsF(font)
    pad = max(2.0, round(px * 0.18))
    room_l, room_r = room
    lim_l = room_l if min_left is None else max(room_l, min_left)
    start = x if min_left is None else max(x, min_left + pad)
    right_limit = bounds.right() - pad
    if n_lines <= 1:
        tw = fm.horizontalAdvance(text)
        if start + tw <= right_limit:                       # một dòng, nới sang phải nếu cần
            lines = [(start, baseline, text, tw)]
        elif start == x and tw <= right_limit - (lim_l + pad):  # chạm mép màn hình: lùi sang trái
            lines = [(right_limit - tw, baseline, text, tw)]
        else:                                               # quá dài: xuống dòng, cỡ chữ giữ nguyên
            step = fm.lineSpacing()
            width = max(right_limit - start, 4 * px)
            lines = [(start, baseline + i * step, t, lw) for i, (t, lw) in enumerate(wrap(text, font, width))]
    else:
        width = max(w - (start - x), 4 * px)
        wrapped = wrap(text, font, width)
        if len(wrapped) > n_lines:
            wider = min(room_r - start - pad, w * 1.35)
            if wider > width + px:
                w2 = wrap(text, font, wider)
                if len(w2) < len(wrapped):
                    wrapped = w2
        step = pitch if pitch > 0 else fm.lineSpacing()
        step = min(max(step, 1.12 * px), 1.8 * px)  # đủ chỗ cho dấu tiếng Việt
        lines = [(start, baseline + i * step, t, lw) for i, (t, lw) in enumerate(wrapped)]
    left = min(x, min(lx for lx, _, _, _ in lines)) - pad
    if min_left is not None:
        left = max(left, min_left)  # không che bản dịch đứng trước
    right = max(x + w, max(lx + lw for lx, _, _, lw in lines)) + pad
    top = min(y, lines[0][1] - 0.95 * px) - 1
    bottom = max(y + h, lines[-1][1] + 0.27 * px) + 1
    # bám lưới pixel: cạnh sắc nét, không rung khi toạ độ lệch nửa pixel
    left, top, right, bottom = math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom)
    dx = dy = 0.0
    if right > bounds.right():
        dx = bounds.right() - right
    if left + dx < bounds.left():
        dx = bounds.left() - left
    if bottom > bounds.bottom():
        dy = bounds.bottom() - bottom
    if top + dy < bounds.top():
        dy = bounds.top() - top
    rect = QRectF(left + dx, top + dy, right - left, bottom - top)
    return Placement(rect, [(lx + dx, ly + dy, t) for lx, ly, t, _ in lines], font)
