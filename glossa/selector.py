"""Cửa sổ chọn vùng cần dịch: kéo chuột để khoanh vùng, Esc hoặc chuột phải để huỷ."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import winutil


class RegionSelector(QWidget):
    finished = Signal(object)  # (x, y, w, h) pixel thật, hoặc None nếu huỷ

    def __init__(self, screen):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.qscreen = screen
        self._origin = None
        self._current = None
        self._done = False
        try:
            self.setScreen(screen)
        except Exception:
            pass
        self.setGeometry(screen.geometry())

    def start(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _emit(self, value) -> None:
        if not self._done:
            self._done = True
            self.finished.emit(value)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._emit(None)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.RightButton:
            self._emit(None)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._origin = e.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._origin is not None:
            self._current = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        r = QRect(self._origin, e.position().toPoint()).normalized()
        self._origin = None
        if r.width() < 12 or r.height() < 12:
            self.update()
            return
        self._emit(self._to_native(r))

    def _to_native(self, r: QRect) -> tuple:
        nr = winutil.get_window_rect(int(self.winId()))
        if nr is None:
            dpr = self.devicePixelRatioF()
            g = self.geometry()
            nr = (g.x(), g.y(), int(g.width() * dpr), int(g.height() * dpr))
        dpr = nr[2] / max(1, self.width())
        return (int(round(nr[0] + r.x() * dpr)), int(round(nr[1] + r.y() * dpr)),
                int(round(r.width() * dpr)), int(round(r.height() * dpr)))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self._origin is not None and self._current is not None:
            r = QRect(self._origin, self._current).normalized()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(r, QColor(0, 0, 0, 1))  # gần trong suốt nhưng vẫn nhận chuột
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(QColor(0, 200, 255))
            pen.setWidth(2)
            p.setPen(pen)
            p.drawRect(r.adjusted(0, 0, -1, -1))
        f = QFont("Segoe UI")
        f.setPixelSize(18)
        p.setFont(f)
        tip = "Kéo chuột để khoanh vùng cần dịch  •  Esc / chuột phải để huỷ"
        tr = QRect(0, 24, self.width(), 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 24, 210))
        w = p.fontMetrics().horizontalAdvance(tip) + 32
        p.drawRoundedRect(QRect((self.width() - w) // 2, 24, w, 40), 8, 8)
        p.setPen(QColor(255, 255, 255))
        p.drawText(tr, int(Qt.AlignmentFlag.AlignCenter.value), tip)
        p.end()


class SelectionSession:
    """Mở một bộ chọn vùng trên mỗi màn hình; gọi callback(rect hoặc None) một lần."""

    def __init__(self, screens: list, callback):
        self.callback = callback
        self.windows = []
        self._fired = False
        for s in screens:
            w = RegionSelector(s)
            w.finished.connect(self._done)
            self.windows.append(w)
        for w in self.windows:
            w.start()

    def _done(self, value) -> None:
        if self._fired:
            return
        self._fired = True
        for w in self.windows:
            w.close()
        self.windows = []
        self.callback(value)
