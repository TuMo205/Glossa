"""Cửa sổ phụ đề Vietsub nổi kiểu rạp phim:
- Hiệu ứng hiện/ẩn mờ dần (smooth fade).
- Thanh công cụ mini hiện khi rê chuột (A- / A+ / Song ngữ / Khoá xuyên chuột).
- Kéo thả tự do, hỗ trợ song ngữ, tự ẩn khỏi ảnh chụp để OCR không tự dịch lại.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import winutil

log = logging.getLogger(__name__)

SUBTITLE_COLORS = [
    ("#FFFFFF", "Trắng tinh"),
    ("#FFE066", "Vàng phim (Vietsub)"),
    ("#7DD3FC", "Xanh ngọc nhạt"),
    ("#A7F3D0", "Xanh lá dịu"),
]

FADE_IN_S = 0.18
FADE_OUT_S = 0.45


class SubtitleWindow(QWidget):
    """Thanh phụ đề nổi luôn ở trên cùng để hiển thị Vietsub thời gian thực."""

    def __init__(self, cfg, on_option_changed=None):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setWindowTitle("Glossa Vietsub")

        self.cfg = cfg
        self.on_option_changed = on_option_changed
        self._original = ""
        self._translated = ""
        self._listening = False
        self._drag_pos: QPoint | None = None
        self._hovered = False
        self._hover_btn: str | None = None
        self._btn_rects: dict[str, QRectF] = {}

        # hiệu ứng fade mượt
        self._alpha = 1.0
        self._fade_start = 0.0
        self._fading_out = False

        self._hide_timer = QTimer(self, singleShot=True, timeout=self._start_fade_out)
        self._anim_timer = QTimer(self, interval=16, timeout=self._anim_tick)
        self._top_timer = QTimer(self, interval=3000, timeout=self.reassert_topmost)

        self._apply_geometry()

    def _apply_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(840, 124)
            return
        g = screen.availableGeometry()
        w = min(1080, max(540, int(g.width() * 0.72)))
        h = self._compute_height(w)
        pos = self.cfg.get("subtitle_pos")
        if isinstance(pos, list) and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
            x = max(g.x() - w + 120, min(x, g.x() + g.width() - 120))
            y = max(g.y(), min(y, g.y() + g.height() - 60))
        else:
            x = g.x() + (g.width() - w) // 2
            y = g.y() + g.height() - h - 54
        self.setGeometry(x, y, w, h)

    def _compute_height(self, width: int) -> int:
        px = max(14, min(48, int(self.cfg.get("subtitle_font_size", 24))))
        bilingual = bool(self.cfg.get("subtitle_bilingual", True))
        main_font = self._make_font(px, bold=True)
        fm_main = QFontMetricsF(main_font)
        text = self._translated or (
            "Vietsub đang lắng nghe… (kéo thả để đổi vị trí)" if self._listening else ""
        )
        inner_w = max(200, width - 56)
        rect_main = fm_main.boundingRect(
            QRectF(0, 0, inner_w, 400),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap),
            text or "Ag",
        )
        total_h = rect_main.height() + 38
        if bilingual and self._original and self._original.strip().lower() != self._translated.strip().lower():
            sub_font = self._make_font(max(12, int(px * 0.65)), bold=False)
            fm_sub = QFontMetricsF(sub_font)
            rect_sub = fm_sub.boundingRect(
                QRectF(0, 0, inner_w, 200),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap),
                self._original,
            )
            total_h += rect_sub.height() + 8
        return max(74, min(280, int(total_h)))

    def _make_font(self, pixel_size: int, bold: bool = False) -> QFont:
        f = QFont(self.cfg.get("font_family", "Segoe UI"))
        f.setPixelSize(pixel_size)
        f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return f

    def apply_style(self) -> None:
        self._apply_window_flags()
        old_bottom = self.y() + self.height()
        h = self._compute_height(self.width())
        if h != self.height():
            self.setGeometry(self.x(), max(0, old_bottom - h), self.width(), h)
        self.update()

    def _apply_window_flags(self) -> None:
        locked = bool(self.cfg.get("subtitle_locked", False))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        if locked:
            winutil.add_ex_style(
                hwnd,
                winutil.WS_EX_TRANSPARENT | winutil.WS_EX_NOACTIVATE | winutil.WS_EX_TOOLWINDOW,
                remove=winutil.WS_EX_APPWINDOW,
            )
        else:
            winutil.add_ex_style(
                hwnd,
                winutil.WS_EX_NOACTIVATE | winutil.WS_EX_TOOLWINDOW,
                remove=winutil.WS_EX_TRANSPARENT | winutil.WS_EX_APPWINDOW,
            )
        winutil.set_exclude_from_capture(hwnd, True)
        winutil.set_topmost(hwnd)

    def show_listening(self, active: bool) -> None:
        self._listening = bool(active)
        if active and self.cfg.get("subtitle_enabled", True):
            self._original = ""
            self._translated = ""
            self._alpha = 1.0
            self._fading_out = False
            self._apply_geometry()
            self.show()
            self._apply_window_flags()
            self._top_timer.start()
            self.update()
        else:
            self._hide_timer.stop()
            self._anim_timer.stop()
            self._top_timer.stop()
            self.hide()

    def show_subtitle(self, original: str, translated: str, interim: bool = False) -> None:
        if not self.cfg.get("subtitle_enabled", True):
            return
        had_text = bool(self._translated) and not self._fading_out
        self._original = (original or "").strip()
        self._translated = (translated or original or "").strip()
        if not self._translated:
            return
        self._fading_out = False
        if had_text:
            # Đang có chữ trên thanh phụ đề -> cập nhật nối tiếp mượt mà, không nháy fade-in lại
            self._alpha = 1.0
        else:
            self._fade_start = time.monotonic()
            self._alpha = 0.25
            self._anim_timer.start()
        if not self.isVisible():
            self._apply_geometry()
            self.show()
            self._apply_window_flags()
            self._top_timer.start()
        else:
            old_bottom = self.y() + self.height()
            h = self._compute_height(self.width())
            if h != self.height():
                self.setGeometry(self.x(), max(0, old_bottom - h), self.width(), h)
        self.update()
        hide_ms = max(2000, int(float(self.cfg.get("subtitle_hide_s", 7.0)) * 1000))
        self._hide_timer.start(hide_ms)

    def clear_subtitle(self) -> None:
        self._original = ""
        self._translated = ""
        self._fading_out = False
        self._alpha = 1.0
        self.update()

    def _start_fade_out(self) -> None:
        self._fading_out = True
        self._fade_start = time.monotonic()
        self._anim_timer.start()

    def _anim_tick(self) -> None:
        elapsed = time.monotonic() - self._fade_start
        if self._fading_out:
            self._alpha = max(0.0, 1.0 - elapsed / FADE_OUT_S)
            if self._alpha <= 0.01:
                self._original = ""
                self._translated = ""
                self._fading_out = False
                self._alpha = 1.0
                self._anim_timer.stop()
        else:
            self._alpha = min(1.0, elapsed / FADE_IN_S)
            if self._alpha >= 0.99:
                self._alpha = 1.0
                self._anim_timer.stop()
        self.update()

    def reassert_topmost(self) -> None:
        if self.isVisible():
            winutil.set_topmost(int(self.winId()))

    def reset_position(self) -> None:
        self.cfg.set("subtitle_pos", None)
        self._apply_geometry()
        self.update()

    # --- nút mini khi rê chuột ---
    def _layout_buttons(self) -> dict[str, QRectF]:
        w = float(self.width())
        bh = 22.0
        y = 10.0
        specs = [("lock", 52.0), ("bi", 68.0), ("plus", 26.0), ("minus", 26.0)]
        rects = {}
        x = w - 14.0
        for key, bw in specs:
            x -= bw
            rects[key] = QRectF(x, y, bw, bh)
            x -= 5.0
        return rects

    def _handle_mini_button(self, key: str) -> None:
        if key == "minus":
            sz = max(14, int(self.cfg.get("subtitle_font_size", 24)) - 2)
            self.cfg.set("subtitle_font_size", sz)
            self.apply_style()
        elif key == "plus":
            sz = min(48, int(self.cfg.get("subtitle_font_size", 24)) + 2)
            self.cfg.set("subtitle_font_size", sz)
            self.apply_style()
        elif key == "bi":
            self.cfg.set("subtitle_bilingual", not bool(self.cfg.get("subtitle_bilingual", True)))
            self.apply_style()
        elif key == "lock":
            self.cfg.set("subtitle_locked", True)
            self._hovered = False
            self.apply_style()
        if self.on_option_changed:
            self.on_option_changed()

    def enterEvent(self, ev) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hovered = False
        self._hover_btn = None
        self.update()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if not self.cfg.get("subtitle_locked", False) and ev.button() == Qt.MouseButton.LeftButton:
            pt = ev.position()
            for key, r in self._btn_rects.items():
                if r.contains(pt):
                    self._handle_mini_button(key)
                    ev.accept()
                    return
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_pos is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            pos = ev.globalPosition().toPoint() - self._drag_pos
            self.move(pos)
            ev.accept()
            return
        if self._hovered:
            pt = ev.position()
            hit = next((k for k, r in self._btn_rects.items() if r.contains(pt)), None)
            if hit != self._hover_btn:
                self._hover_btn = hit
                self.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.SizeAllCursor)
                self.update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag_pos is not None:
            self._drag_pos = None
            self.cfg.set("subtitle_pos", [self.x(), self.y()])
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    # --- vẽ phụ đề ---
    def paintEvent(self, _ev) -> None:
        has_text = bool(self._translated)
        if not has_text and not self._listening:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setOpacity(self._alpha if has_text else 1.0)

        opacity = max(0.0, min(1.0, float(self.cfg.get("subtitle_opacity", 0.84))))
        px = max(14, min(48, int(self.cfg.get("subtitle_font_size", 24))))
        bilingual = bool(self.cfg.get("subtitle_bilingual", True))
        color_hex = str(self.cfg.get("subtitle_color", "#FFFFFF") or "#FFFFFF")

        w, h = float(self.width()), float(self.height())
        box = QRectF(4, 4, w - 8, h - 8)

        bg_alpha = int(255 * (opacity if has_text else min(0.55, max(0.35, opacity))))
        if bg_alpha > 4 or self._hovered:
            p.setBrush(QColor(11, 15, 25, max(bg_alpha, 120 if self._hovered else 0)))
            border_alpha = 110 if self._hovered else (42 if bg_alpha > 20 else 0)
            p.setPen(QPen(QColor(148, 163, 184, border_alpha), 1.2) if border_alpha else Qt.PenStyle.NoPen)
            p.drawRoundedRect(box, 14, 14)

        if self._listening:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#10B981"))
            p.drawEllipse(QRectF(16, 15, 8, 8))

        # Thanh công cụ mini khi rê chuột (khi chưa khoá)
        self._btn_rects = {}
        if self._hovered and not self.cfg.get("subtitle_locked", False):
            self._btn_rects = self._layout_buttons()
            labels = {"minus": "A-", "plus": "A+", "bi": "Song ngữ", "lock": "Khoá"}
            bf = self._make_font(11, bold=True)
            p.setFont(bf)
            for key, r in self._btn_rects.items():
                active_bi = key == "bi" and bilingual
                hovered = key == self._hover_btn
                bg = QColor("#6366F1") if (active_bi or hovered) else QColor(30, 41, 59, 210)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(r, 6, 6)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), labels[key])

        top_pad = 34 if (self._hovered and not self.cfg.get("subtitle_locked", False)) else 14
        inner = box.adjusted(24, top_pad, -24, -12)
        flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap)

        if not has_text:
            hint_font = self._make_font(max(13, int(px * 0.60)), bold=False)
            p.setFont(hint_font)
            p.setPen(QColor(203, 213, 225, 210))
            hint = "Vietsub đang lắng nghe… (rê chuột để tuỳ chỉnh hoặc kéo thả vị trí)"
            if self.cfg.get("subtitle_locked", False):
                hint = "Vietsub đang lắng nghe…"
            p.drawText(inner, flags, hint)
            p.end()
            return

        show_orig = (
            bilingual
            and bool(self._original)
            and self._original.strip().lower() != self._translated.strip().lower()
        )

        main_font = self._make_font(px, bold=True)
        if show_orig:
            sub_font = self._make_font(max(12, int(px * 0.65)), bold=False)
            fm_sub = QFontMetricsF(sub_font)
            sub_h = fm_sub.boundingRect(
                QRectF(0, 0, inner.width(), 160),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap),
                self._original,
            ).height()
            orig_rect = QRectF(inner.left(), inner.top(), inner.width(), sub_h + 4)
            main_rect = QRectF(
                inner.left(), orig_rect.bottom() + 4, inner.width(), max(24.0, inner.bottom() - orig_rect.bottom() - 4)
            )
            p.setFont(sub_font)
            p.setPen(QColor(0, 0, 0, 210))
            p.drawText(orig_rect.translated(1, 1), flags, self._original)
            p.setPen(QColor(203, 213, 225, 235))
            p.drawText(orig_rect, flags, self._original)
        else:
            main_rect = inner

        p.setFont(main_font)
        shadow = QColor(0, 0, 0, 230)
        p.setPen(shadow)
        for dx, dy in ((-1.5, 0), (1.5, 0), (0, -1.5), (0, 1.5), (0, 2.2)):
            p.drawText(main_rect.translated(dx, dy), flags, self._translated)
        p.setPen(QColor(color_hex))
        p.drawText(main_rect, flags, self._translated)
        p.end()
