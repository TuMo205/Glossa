"""Lớp phủ trong suốt, xuyên chuột, luôn trên cùng: vẽ bản dịch đè lên chữ gốc."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import winutil
from .textlayout import (
    HOLD_TOL,
    SMOOTH,
    horizontal_room,
    make_font,
    likely_misread,
    match_rects,
    place_text,
    similarity,
    unify_sizes,
)
from .translator import SKIP

log = logging.getLogger(__name__)

FADE_S = 0.16          # bản dịch mới hiện mờ dần trong 0,16 giây (không bật lên đột ngột)
GRAVE_S = 2.0          # nhớ khối vừa mất trong 2 giây để khớp lại nếu nó quay về
GEO_TOL = 2.0          # lệch vị trí <= 2 px thật: coi là rung do OCR, giữ vị trí cũ


def _color_close(a, b, tol: int = 36) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) <= tol


class _Item:
    __slots__ = ("src", "tr", "held", "bx", "by", "bw", "bh", "base", "pitch", "nl", "est", "px", "bold",
                 "bg", "fg", "born", "rect", "lines", "font")


class OverlayWindow(QWidget):
    def __init__(self, screen, key: str, cfg, cache):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("Glossa overlay")
        self.key = key
        self.qscreen = screen
        self.cfg = cfg
        self.cache = cache
        self.native = (0, 0, 1, 1)
        self.dpr = 1.0
        self.exclusion_ok = False
        self._by_target: dict = {}
        self._items: list = []        # các bản dịch đang hiển thị (đã bố trí)
        self._grave: list = []        # [(lúc mất, _Item)] khối vừa mất, để khớp lại nếu quay về
        self._sig = None
        self._hover = -1
        self._region = None
        self._layout_cache: dict = {}
        self._anim = QTimer(self, interval=16, timeout=self._anim_tick)
        self._resize_all = False

    # --- vị trí & thuộc tính cửa sổ ---
    def place(self) -> None:
        geo = self.qscreen.geometry() if self.cfg.get("include_taskbar") else self.qscreen.availableGeometry()
        try:
            self.setScreen(self.qscreen)
        except Exception:
            pass
        self.setGeometry(geo)
        self.ensure_shown()
        self._layout_cache.clear()
        self.relayout()

    def ensure_shown(self) -> None:
        if not self.isVisible():
            self.show()
        hwnd = int(self.winId())
        winutil.add_ex_style(hwnd, winutil.WS_EX_TRANSPARENT | winutil.WS_EX_NOACTIVATE | winutil.WS_EX_TOOLWINDOW,
                             remove=winutil.WS_EX_APPWINDOW)
        if self.cfg.get("exclude_from_capture", True):
            self.exclusion_ok = winutil.set_exclude_from_capture(hwnd, True)
        else:
            winutil.set_exclude_from_capture(hwnd, False)
            self.exclusion_ok = False
        winutil.set_topmost(hwnd)
        self.refresh_native()

    def refresh_native(self) -> None:
        r = winutil.get_window_rect(int(self.winId()))
        if r is None:  # không phải Windows: suy ra từ Qt
            dpr = self.devicePixelRatioF()
            g = self.geometry()
            r = (g.x(), g.y(), int(round(g.width() * dpr)), int(round(g.height() * dpr)))
        self.native = r
        self.dpr = r[2] / max(1, self.width())

    def reassert_topmost(self) -> None:
        if self.isVisible():
            winutil.set_topmost(int(self.winId()))

    def contains_native(self, x: float, y: float) -> bool:
        nx, ny, nw, nh = self.native
        return nx <= x < nx + nw and ny <= y < ny + nh

    def to_local(self, x: float, y: float) -> tuple:
        return (x - self.native[0]) / self.dpr, (y - self.native[1]) / self.dpr

    # --- dữ liệu ---
    def set_blocks(self, target_key: str, blocks: list) -> None:
        self._by_target[target_key] = blocks
        self.relayout()

    def restyle(self) -> None:
        """Phông/cỡ chữ/độ che vừa đổi: vẽ lại ngay với cấu hình mới."""
        self._layout_cache.clear()
        self._resize_all = True  # lần dựng tới: tính lại cỡ chữ theo tỉ lệ mới, không giữ cỡ cũ
        self._sig = None
        self.relayout()
        self.update()

    def clear(self) -> None:
        self._by_target.clear()
        self._items = []
        self._grave = []
        self._sig = None
        self.update()

    def set_region(self, native_rect) -> None:
        self._region = native_rect
        self.update()

    def relayout(self) -> None:
        """Dựng lại danh sách bản dịch từ các khối OCR mới nhất, giữ ổn định so với lần trước."""
        now = time.monotonic()
        family = self.cfg.get("font_family", "Segoe UI")
        scale = float(self.cfg.get("font_scale", 1.0))
        dpr = self.dpr
        bounds = QRectF(0, 0, self.width(), self.height())
        blocks = [b for bl in self._by_target.values() for b in bl]
        self._grave = [(t, it) for t, it in self._grave if now - t <= GRAVE_S][-400:]
        prev = list(self._items) + [it for _t, it in self._grave]
        n_shown = len(self._items)
        match = match_rects([(b.x, b.y, b.w, b.h) for b in blocks], [(p.bx, p.by, p.bw, p.bh) for p in prev])

        cands = []  # (khối, khối cũ khớp hoặc None, bản dịch, đang tạm giữ, có phải khối đang hiện)
        for b, mi in zip(blocks, match):
            p = prev[mi] if mi >= 0 else None
            tr, held = self._choose_translation(b, p)
            if tr is None:
                continue
            cands.append((b, p, tr, held, 0 <= mi < n_shown))

        # cỡ chữ: đúng cỡ gốc (ước lượng được làm mượt theo thời gian), gom các cỡ chỉ lệch do sai số đo
        resize_all, self._resize_all = self._resize_all, False
        est = []
        for b, p, *_ in cands:
            e = max(6.0, (b.font_px or b.line_h) / dpr * scale)
            if p is not None and not resize_all:
                e = (1 - SMOOTH) * p.est + SMOOTH * e
            est.append(e)
        weights = [min(60, sum(ch.isalpha() for ch in b.text)) * (1.5 if b.n_lines > 1 else 1.0) + 1
                   for b, *_ in cands]
        unified = unify_sizes(est, weights)

        new_items = []
        for (b, p, tr, held, was_shown), e, px in zip(cands, est, unified):
            it = _Item()
            it.src, it.tr, it.held, it.nl, it.est = b.text, tr, held, b.n_lines, e
            same_x = p is not None and abs(b.x - p.bx) <= GEO_TOL and abs(b.w - p.bw) <= max(4.0, 0.06 * p.bw)
            if same_x and b.n_lines == p.nl and likely_misread(p.src, b.text) and \
                    abs((b.y + b.h / 2) - (p.by + p.bh / 2)) <= max(GEO_TOL, 0.2 * p.bh):
                # cùng dòng chữ, cùng chỗ: chữ gốc không đổi, mọi khác biệt chỉ là do OCR
                # (khung cao/thấp hơn vài px khi vùng cắt khác) => giữ nguyên tuyệt đối
                it.bx, it.by, it.bw, it.bh, it.base, it.pitch = p.bx, p.by, p.bw, p.bh, p.base, p.pitch
                it.bold = p.bold
                if resize_all:
                    it.px = px
                else:
                    it.est, it.px = p.est, p.px
            else:
                it.bold = bool(getattr(b, "bold", False))
                if same_x and abs(b.y - p.by) <= GEO_TOL and abs(b.h - p.bh) <= max(2.0, 0.12 * p.bh):
                    it.bx, it.by, it.bw, it.bh, it.base, it.pitch = p.bx, p.by, p.bw, p.bh, p.base, p.pitch
                else:
                    it.bx, it.by, it.bw, it.bh = b.x, b.y, b.w, b.h
                    it.base = b.baseline or (b.y + b.h)
                    it.pitch = b.pitch
                if p is not None and not resize_all and abs(e - p.px) <= max(0.75, HOLD_TOL * p.px):
                    px = p.px  # cỡ vẫn trong sai số: giữ nguyên, không nhảy
                it.px = px
            it.bg = p.bg if p is not None and _color_close(b.bg, p.bg) else b.bg
            it.fg = p.fg if p is not None and _color_close(b.fg, p.fg, 60) else b.fg
            it.born = p.born if (p is not None and was_shown) else now
            new_items.append(it)

        # bố trí: khoảng trống ngang tính trên toàn bộ khối đang hiển thị
        local = [((it.bx - self.native[0]) / dpr, (it.by - self.native[1]) / dpr, it.bw / dpr, it.bh / dpr)
                 for it in new_items]
        rooms = horizontal_room(local, 0.0, bounds.width())
        if len(self._layout_cache) > 4000:
            self._layout_cache.clear()
        placed: list = []  # (chỉ số, khung nền) theo thứ tự từ trái sang phải
        for i in sorted(range(len(new_items)), key=lambda k: local[k][0]):
            it, (lx, ly, lw, lh), room = new_items[i], local[i], rooms[i]
            min_left = None
            for j, r in placed:  # bản dịch đứng trước trên cùng hàng
                _jx, jy, _jw, jh = local[j]
                if min(ly + lh, jy + jh) - max(ly, jy) >= 0.4 * min(lh, jh):
                    min_left = r.right() if min_left is None else max(min_left, r.right())  # nền khít nhau
            base = (it.base - self.native[1]) / dpr
            pitch = it.pitch / dpr
            key = (it.tr, round(lx), round(ly), round(lw), round(lh), round(base, 1), round(pitch, 1),
                   it.nl, it.px, it.bold, round(room[0]), round(room[1]),
                   None if min_left is None else round(min_left), family)
            pl = self._layout_cache.get(key)
            if pl is None:
                pl = place_text(it.tr, lx, ly, lw, lh, base, pitch, it.nl, it.px, family, room, bounds,
                                it.bold, min_left)
                self._layout_cache[key] = pl
            it.rect, it.lines, it.font = pl.rect, pl.lines, pl.font
            placed.append((i, pl.rect))
        new_items.sort(key=lambda it: (it.rect.top(), it.rect.left()))

        kept = {id(p) for _b, p, *_ in cands if p is not None}
        self._grave = [(t, it) for t, it in self._grave if id(it) not in kept]
        self._grave.extend((now, it) for it in self._items if id(it) not in kept)
        self._items = new_items
        self._hover = -1
        sig = tuple((it.rect.getRect(), it.tr, it.px, it.bg, it.fg) for it in new_items)
        if sig != self._sig or self._fading(now):
            self._sig = sig
            self.update()
            if self._fading(now) and not self._anim.isActive():
                self._anim.start()

    def _choose_translation(self, b, p):
        """(bản dịch, đang tạm giữ) hoặc (None, False) nếu chưa có gì để hiện."""
        cached = self.cache.get(b.text)
        if cached is not None and cached != SKIP:
            return cached, False
        if p is not None and p.tr:
            if cached is None and similarity(b.text, p.src) >= 0.6:
                return p.tr, True             # đang chờ bản dịch mới: tạm giữ bản cũ thay vì nháy trống
            if cached == SKIP and likely_misread(p.src, b.text):
                return p.tr, False            # OCR đọc sai thành chữ không dịch được: giữ bản cũ
        return None, False

    def _fading(self, now: float) -> bool:
        return any(now - it.born < FADE_S for it in self._items)

    def _anim_tick(self) -> None:
        self.update()
        if not self._fading(time.monotonic()):
            self._anim.stop()

    @property
    def item_count(self) -> int:
        return len(self._items)

    # --- vẽ ---
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        now = time.monotonic()
        alpha = int(255 * max(0.3, min(1.0, float(self.cfg.get("box_opacity", 1.0)))))
        for i, it in enumerate(self._items):
            if i == self._hover:
                continue
            p.setOpacity(min(1.0, max(0.0, (now - it.born) / FADE_S)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(it.bg[0], it.bg[1], it.bg[2], alpha))
            radius = min(4.0, max(1.0, it.px * 0.15))
            p.drawRoundedRect(it.rect, radius, radius)
            p.setPen(QColor(it.fg[0], it.fg[1], it.fg[2]))
            p.setFont(it.font)
            for x, y, text in it.lines:
                p.drawText(QPointF(x, y), text)
        p.setOpacity(1.0)
        if self._region and self.cfg.get("show_region_border", True):
            x, y, w, h = self._region
            lx, ly = self.to_local(x, y)
            pen = QPen(QColor(0, 190, 255, 170))
            pen.setWidthF(1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(lx - 2, ly - 2, w / self.dpr + 4, h / self.dpr + 4))
        p.end()

    def check_hover(self, global_pos) -> None:
        """global_pos=None: bỏ trạng thái rê chuột (khi tắt tính năng)."""
        hover = -1
        if global_pos is not None:
            pos = QPointF(self.mapFromGlobal(global_pos))
            for i, it in enumerate(self._items):
                if it.rect.adjusted(-3, -3, 3, 3).contains(pos):
                    hover = i
                    break
        if hover != self._hover:
            self._hover = hover
            self.update()


class OsdWindow(QWidget):
    """Thông báo nhỏ hiện giữa phía trên màn hình rồi tự ẩn (bật/tắt, đổi chế độ...)."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._text = ""
        self._font = make_font("Segoe UI", 17)
        from PySide6.QtCore import QTimer

        self._timer = QTimer(self, singleShot=True, timeout=self.hide)

    def show_text(self, text: str, screen, ms: int = 1600) -> None:
        self._text = text
        fm = QFontMetricsF(self._font)
        w = int(fm.horizontalAdvance(text) + 40)
        h = int(fm.height() + 22)
        g = screen.availableGeometry()
        self.setGeometry(g.x() + (g.width() - w) // 2, g.y() + 36, w, h)
        self.show()
        hwnd = int(self.winId())
        winutil.add_ex_style(hwnd, winutil.WS_EX_TRANSPARENT | winutil.WS_EX_NOACTIVATE | winutil.WS_EX_TOOLWINDOW)
        winutil.set_exclude_from_capture(hwnd, True)
        winutil.set_topmost(hwnd)
        self.update()
        self._timer.start(ms)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(22, 24, 30, 225))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 10, 10)
        p.setPen(QColor(255, 255, 255))
        p.setFont(self._font)
        p.drawText(QRectF(0, 0, self.width(), self.height()), int(Qt.AlignmentFlag.AlignCenter.value), self._text)
        p.end()
