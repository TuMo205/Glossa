"""Bảng điều khiển Glossa thiết kế hiện đại kiểu ứng dụng Windows 11:
- Thanh điều hướng bên trái (Sidebar) với 5 trang rõ ràng, không chật chội.
- Xem trước trực tiếp (Live Preview) thanh phụ đề Vietsub & sóng âm thanh (Audio Visualizer).
- Chuyển nhanh Dark / Light mode, đổi gì lưu và áp dụng ngay lập tức.
"""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .dubbing import DUB_RATES, DUB_VOICES
from .hotkeys import parse_hotkey
from .icons import BLUE, RED, make_icon
from .ocr_windows import lang_display_name
from .subtitle import SUBTITLE_COLORS
from .theme import build_stylesheet, get_palette
from .translator import PROVIDER_NAMES, TARGETS
from .voice import SPEECH_LANGS, VOICE_PACES, VOICE_SOURCES, list_audio_devices

INTERVALS = [(300, "Nhanh (0,3 giây)"), (500, "Vừa (0,5 giây)"), (1200, "Tiết kiệm pin (1,2 giây)")]
STYLES = [("auto", "Giống màu chữ gốc"), ("dark", "Nền tối, chữ trắng"), ("light", "Nền sáng, chữ đen")]
HOTKEYS = [
    ("toggle", "Bật / tạm dừng dịch"),
    ("hide", "Ẩn / hiện bản dịch"),
    ("region", "Chọn vùng dịch"),
    ("fullscreen", "Dịch toàn màn hình"),
    ("voice", "Bật / tắt Giọng nói & Vietsub"),
    ("panel", "Mở bảng điều khiển"),
    ("quit", "Thoát chương trình"),
]
OPTIONS = [
    ("refine_small_text", "Đọc lại chữ nhỏ cho chính xác (2×)"),
    ("persist_cache", "Nhớ bản dịch qua các lần mở"),
    ("hover_reveal", "Rê chuột lên bản dịch để xem chữ gốc"),
    ("include_taskbar", "Dịch cả thanh tác vụ"),
    ("show_region_border", "Hiện khung quanh vùng đang dịch"),
    ("start_paused", "Khởi động ở trạng thái tạm dừng"),
    ("show_panel_on_start", "Bật bảng điều khiển khi khởi động"),
]


def qt_to_hotkey(seq: QKeySequence) -> str:
    s = seq.toString(QKeySequence.SequenceFormat.PortableText).split(",")[0].strip()
    return s.replace("Meta+", "Win+")


def hotkey_to_qt(combo: str) -> QKeySequence:
    return QKeySequence.fromString((combo or "").replace("Win+", "Meta+"), QKeySequence.SequenceFormat.PortableText)


class AudioVisualizer(QWidget):
    """Thanh sóng âm thanh nhiều cột mượt mà hiển thị mức âm lượng trực tiếp."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self._level = 0.0
        self._bars = [0.06] * 24
        self._phase = 0.0

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))
        self._phase += 0.55
        n = len(self._bars)
        for i in range(n):
            center_weight = 1.0 - abs(i - (n - 1) / 2.0) / (n / 2.0)
            wave = 0.55 + 0.45 * math.sin(self._phase + i * 0.55)
            target = max(0.08, self._level * (0.35 + 0.65 * center_weight) * wave)
            self._bars[i] = self._bars[i] * 0.45 + target * 0.55
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        n = len(self._bars)
        gap = 3.0
        bw = max(3.0, (w - gap * (n - 1)) / n)
        for i, val in enumerate(self._bars):
            bh = max(4.0, val * (h - 4.0))
            x = i * (bw + gap)
            y = (h - bh) / 2.0
            color = QColor("#10B981") if val > 0.14 else QColor("#334155")
            if val > 0.72:
                color = QColor("#34D399")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y, bw, bh), 2.0, 2.0)
        p.end()


class VietsubPreview(QWidget):
    """Khung xem trước trực tiếp kiểu dáng thanh phụ đề Vietsub (cỡ chữ, màu, độ mờ, song ngữ)."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setFixedHeight(86)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        w, h = float(self.width()), float(self.height())

        # Nền mô phỏng khung phim gradient tối
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor("#1E1B4B"))
        grad.setColorAt(0.5, QColor("#0F172A"))
        grad.setColorAt(1.0, QColor("#134E4A"))
        p.setPen(QPen(QColor(148, 163, 184, 55), 1.0))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)

        # Nhãn nhỏ "XEM TRƯỚC VIETSUB"
        lbl_f = QFont("Segoe UI", 7)
        lbl_f.setBold(True)
        p.setFont(lbl_f)
        p.setPen(QColor(148, 163, 184, 150))
        p.drawText(QRectF(10, 5, 160, 14), int(Qt.AlignmentFlag.AlignLeft), "XEM TRƯỚC PHỤ ĐỀ")

        opacity = max(0.0, min(1.0, float(self.cfg.get("subtitle_opacity", 0.84))))
        px = max(14, min(48, int(self.cfg.get("subtitle_font_size", 24))))
        scaled_px = max(12, min(22, int(px * 0.68)))
        bilingual = bool(self.cfg.get("subtitle_bilingual", True))
        color_hex = str(self.cfg.get("subtitle_color", "#FFFFFF") or "#FFFFFF")

        pill = QRectF(18, 22, w - 36, h - 30)
        bg_alpha = int(255 * opacity)
        if bg_alpha > 4:
            p.setPen(QPen(QColor(148, 163, 184, 45), 1.0))
            p.setBrush(QColor(11, 15, 25, bg_alpha))
            p.drawRoundedRect(pill, 9, 9)

        flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        if bilingual:
            sf = QFont(self.cfg.get("font_family", "Segoe UI"))
            sf.setPixelSize(max(10, int(scaled_px * 0.68)))
            p.setFont(sf)
            p.setPen(QColor(203, 213, 225, 225))
            p.drawText(QRectF(pill.left(), pill.top() + 3, pill.width(), pill.height() * 0.40),
                       flags, "Today we are exploring Tokyo together.")
            main_rect = QRectF(pill.left(), pill.top() + pill.height() * 0.40, pill.width(), pill.height() * 0.56)
        else:
            main_rect = pill

        mf = QFont(self.cfg.get("font_family", "Segoe UI"))
        mf.setPixelSize(scaled_px)
        mf.setBold(True)
        p.setFont(mf)
        p.setPen(QColor(0, 0, 0, 220))
        p.drawText(main_rect.translated(1, 1.5), flags, "Hôm nay chúng ta sẽ cùng khám phá Tokyo.")
        p.setPen(QColor(color_hex))
        p.drawText(main_rect, flags, "Hôm nay chúng ta sẽ cùng khám phá Tokyo.")
        p.end()


class ControlPanel(QDialog):
    def __init__(self, controller):
        super().__init__(
            None,
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.ctl = controller
        self.cfg = controller.cfg
        self._updating = False
        self._loaded_audio_source = None
        self.setObjectName("GlossaRoot")
        self.setWindowTitle(f"Glossa {__version__} — Dịch màn hình, Vietsub & Thuyết minh AI")
        self.setWindowIcon(make_icon(True))
        self.setMinimumSize(840, 600)
        self.resize(870, 660)

        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._build_sidebar())

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 14, 16, 12)
        rv.setSpacing(10)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._wrap_scroll(self._build_screen_page()))
        self.pages.addWidget(self._wrap_scroll(self._build_voice_page()))
        self.pages.addWidget(self._build_history_page())
        self.pages.addWidget(self._wrap_scroll(self._build_display_page()))
        self.pages.addWidget(self._wrap_scroll(self._build_hotkeys_page()))
        rv.addWidget(self.pages, 1)

        # Thanh mở rộng nhanh (giữ tương thích toggle_expanded & panel_expanded)
        self.ext = self._build_quick_bar()
        rv.addWidget(self.ext)
        self.ext.setVisible(bool(self.cfg.get("panel_expanded", False)))

        shell.addWidget(right, 1)

        self._nav_buttons[0].setChecked(True)
        self._apply_theme()
        self._timer = QTimer(self, interval=1000, timeout=self._refresh_status)
        self.refresh()

    def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.viewport().setAutoFillBackground(False)
        widget.setAutoFillBackground(False)
        sa.setWidget(widget)
        return sa

    # ------------------------------------------------------------------ theme
    def _apply_theme(self) -> None:
        theme = self.cfg.get("ui_theme", "dark")
        self.setStyleSheet(build_stylesheet(theme))
        self.bt_theme.setText("☀ Sáng" if theme == "dark" else "☾ Tối")
        self._refresh_state()

    def _toggle_theme(self) -> None:
        cur = self.cfg.get("ui_theme", "dark")
        nxt = "light" if cur == "dark" else "dark"
        self.cfg.set("ui_theme", nxt)
        self._apply_theme()
        if self.ctl.lang_dialog is not None:
            self.ctl.lang_dialog.apply_theme()

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> QFrame:
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(220)
        v = QVBoxLayout(sb)
        v.setContentsMargins(12, 14, 12, 12)
        v.setSpacing(6)

        # Brand
        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.lb_badge = QLabel("V")
        self.lb_badge.setFixedSize(34, 34)
        self.lb_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.addWidget(self.lb_badge)

        bc = QVBoxLayout()
        bc.setSpacing(0)
        tr = QHBoxLayout()
        tr.setSpacing(6)
        lt = QLabel("Glossa")
        lt.setProperty("role", "title")
        lv = QLabel(f"v{__version__}")
        lv.setStyleSheet(
            "background: rgba(99,102,241,0.18); color: #818CF8; "
            "border-radius: 5px; padding: 1px 6px; font-size: 7.5pt; font-weight: 700;"
        )
        tr.addWidget(lt)
        tr.addWidget(lv)
        tr.addStretch(1)
        ls = QLabel("Dịch màn hình & Vietsub")
        ls.setProperty("role", "muted")
        bc.addLayout(tr)
        bc.addWidget(ls)
        brand.addLayout(bc, 1)
        v.addLayout(brand)
        v.addSpacing(8)

        # Nav items
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: list[QPushButton] = []
        nav_items = [
            "Dịch màn hình",
            "Vietsub && Thuyết minh",
            "Lịch sử && Xuất .SRT",
            "Hiển thị && Tuỳ chọn",
            "Phím tắt && Hệ thống",
        ]
        for idx, text in enumerate(nav_items):
            b = QPushButton(text)
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _c, i=idx: self.pages.setCurrentIndex(i))
            self._nav_group.addButton(b, idx)
            self._nav_buttons.append(b)
            v.addWidget(b)

        v.addStretch(1)

        # Quick status card in sidebar
        qcard = QFrame()
        qcard.setProperty("card", True)
        qv = QVBoxLayout(qcard)
        qv.setContentsMargins(10, 10, 10, 10)
        qv.setSpacing(8)

        f = QFont(self.font())
        f.setBold(True)
        self.bt_toggle = QPushButton()
        self.bt_toggle.setCheckable(True)
        self.bt_toggle.setMinimumHeight(36)
        self.bt_toggle.setFont(f)
        self.bt_toggle.clicked.connect(lambda checked: self.ctl.set_active(checked))

        self.bt_voice = QPushButton()
        self.bt_voice.setCheckable(True)
        self.bt_voice.setMinimumHeight(36)
        self.bt_voice.setFont(f)
        self.bt_voice.clicked.connect(lambda checked: self.ctl.set_voice_active(checked))

        qv.addWidget(self.bt_toggle)
        qv.addWidget(self.bt_voice)
        v.addWidget(qcard)
        v.addSpacing(4)

        # Bottom utility row
        util = QHBoxLayout()
        util.setSpacing(6)
        self.bt_theme = QPushButton()
        self.bt_theme.setAutoDefault(False)
        self.bt_theme.clicked.connect(self._toggle_theme)
        self.bt_close = QPushButton("Ẩn")
        self.bt_close.setProperty("variant", "primary")
        self.bt_close.setDefault(True)
        self.bt_close.clicked.connect(self.hide)
        self.bt_exit = QPushButton("Thoát")
        self.bt_exit.setProperty("variant", "danger_outline")
        self.bt_exit.setAutoDefault(False)
        self.bt_exit.clicked.connect(self.ctl.quit)
        util.addWidget(self.bt_theme, 1)
        util.addWidget(self.bt_close)
        util.addWidget(self.bt_exit)
        v.addLayout(util)
        return sb

    # ------------------------------------------------------------------ Page 0: Screen
    def _build_screen_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(10)

        hdr = QLabel("Dịch màn hình trực tiếp")
        hdr.setProperty("role", "page_title")
        v.addWidget(hdr)

        st_card = QFrame()
        st_card.setProperty("card", True)
        sv = QVBoxLayout(st_card)
        sv.setContentsMargins(14, 10, 14, 10)
        self.lb_status = QLabel()
        self.lb_status.setWordWrap(True)
        self.lb_status.setProperty("role", "subtitle")
        sv.addWidget(self.lb_status)
        v.addWidget(st_card)

        card = QFrame()
        card.setProperty("card", True)
        g = QGridLayout(card)
        g.setContentsMargins(14, 14, 14, 14)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)

        self.cb_mode = QComboBox()
        self.cb_mode.addItem("Toàn màn hình", "fullscreen")
        self.cb_mode.addItem("Vùng đã chọn", "region")
        self.cb_mode.currentIndexChanged.connect(self._on_mode)

        self.bt_region = QPushButton("Chọn vùng…")
        self.bt_region.setAutoDefault(False)
        self.bt_region.clicked.connect(self._on_pick_region)

        self.cb_lang = QComboBox()
        self.cb_lang.currentIndexChanged.connect(self._on_lang)

        self.bt_langs = QPushButton("Thêm tiếng…")
        self.bt_langs.setAutoDefault(False)
        self.bt_langs.setToolTip("Cài thêm gói nhận dạng chữ: Nhật, Hàn, Nga, Ả Rập…")
        self.bt_langs.clicked.connect(self.ctl.show_languages)

        self.cb_target = QComboBox()
        for code, name in TARGETS:
            self.cb_target.addItem(name, code)
        self.cb_target.currentIndexChanged.connect(self._on_target)

        self.cb_provider = QComboBox()
        for key, name in PROVIDER_NAMES.items():
            self.cb_provider.addItem(name, key)
        self.cb_provider.currentIndexChanged.connect(self._on_provider)

        self.cb_speed = QComboBox()
        for ms, name in INTERVALS:
            self.cb_speed.addItem(name, ms)
        self.cb_speed.currentIndexChanged.connect(self._on_speed)

        rows = [
            ("Chế độ dịch:", self.cb_mode),
            ("Đọc chữ tiếng:", self.cb_lang),
            ("Dịch sang:", self.cb_target),
            ("Dịch vụ dịch:", self.cb_provider),
            ("Tốc độ quét:", self.cb_speed),
        ]
        for r, (label, w) in enumerate(rows):
            lb = QLabel(label)
            lb.setProperty("role", "subtitle")
            g.addWidget(lb, r, 0)
            g.addWidget(w, r, 1, 1, 1 if w in (self.cb_mode, self.cb_lang) else 2)
        g.addWidget(self.bt_region, 0, 2)
        g.addWidget(self.bt_langs, 1, 2)
        g.setColumnStretch(1, 1)
        v.addWidget(card)

        # Thẻ phím tắt nhanh
        tip_card = QFrame()
        tip_card.setProperty("card", True)
        tg = QGridLayout(tip_card)
        tg.setContentsMargins(14, 12, 14, 12)
        tg.setHorizontalSpacing(14)
        tg.setVerticalSpacing(7)
        lh = QLabel("Phím tắt nhanh")
        lh.setProperty("role", "section")
        tg.addWidget(lh, 0, 0, 1, 4)
        for idx, (k, desc) in enumerate([
            ("Ctrl+Alt+T", "Bật / tạm dừng dịch"),
            ("Ctrl+Alt+V", "Bật / tắt Vietsub & Thuyết minh"),
            ("Ctrl+Alt+R", "Khoanh vùng dịch"),
            ("Ctrl+Alt+F", "Dịch toàn màn hình"),
            ("Ctrl+Alt+H", "Ẩn / hiện bản dịch"),
            ("Ctrl+Alt+P", "Mở bảng điều khiển"),
        ]):
            r, c = divmod(idx, 2)
            lk = QLabel(k)
            lk.setStyleSheet(
                "background: rgba(99,102,241,0.16); color: #818CF8; "
                "border-radius: 5px; padding: 2px 7px; font-weight: 700; font-size: 8.2pt;"
            )
            ld = QLabel(desc)
            ld.setProperty("role", "subtitle")
            tg.addWidget(lk, r + 1, c * 2)
            tg.addWidget(ld, r + 1, c * 2 + 1)
        v.addWidget(tip_card)
        v.addStretch(1)
        return page

    # ------------------------------------------------------------------ Page 1: Voice, Vietsub & Dubbing
    def _build_voice_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(10)

        hdr = QLabel("Dịch giọng nói, Vietsub & Thuyết minh AI")
        hdr.setProperty("role", "page_title")
        v.addWidget(hdr)

        # Xem trước trực tiếp phụ đề Vietsub
        self.sub_preview = VietsubPreview(self.cfg)
        v.addWidget(self.sub_preview)

        # Card 1: Nguồn thu âm & tốc độ nhận dạng
        src_card = QFrame()
        src_card.setProperty("card", True)
        g = QGridLayout(src_card)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)

        self.cb_voice_source = QComboBox()
        for key, name in VOICE_SOURCES:
            self.cb_voice_source.addItem(name, key)
        self.cb_voice_source.currentIndexChanged.connect(self._on_voice_source)

        self.cb_voice_device = QComboBox()
        self.cb_voice_device.currentIndexChanged.connect(self._on_voice_device)

        self.bt_voice_refresh = QPushButton("↻")
        self.bt_voice_refresh.setAutoDefault(False)
        self.bt_voice_refresh.setFixedWidth(34)
        self.bt_voice_refresh.setToolTip("Làm mới danh sách thiết bị âm thanh")
        self.bt_voice_refresh.clicked.connect(self._fill_audio_devices)

        self.cb_voice_lang = QComboBox()
        for code, name in SPEECH_LANGS:
            self.cb_voice_lang.addItem(name, code)
        self.cb_voice_lang.currentIndexChanged.connect(self._on_voice_lang)

        self.cb_voice_pace = QComboBox()
        for code, name, _mx, _sil in VOICE_PACES:
            self.cb_voice_pace.addItem(name, code)
        self.cb_voice_pace.currentIndexChanged.connect(
            lambda _i: self._set("voice_pace", self.cb_voice_pace.currentData())
        )

        self.sl_voice_sens = QSlider(Qt.Orientation.Horizontal)
        self.sl_voice_sens.setRange(5, 60)  # 0.005 .. 0.060
        self.lb_voice_sens = QLabel("15")
        self.lb_voice_sens.setMinimumWidth(28)
        self.sl_voice_sens.valueChanged.connect(self._on_voice_sens)

        self.visualizer = AudioVisualizer()
        self.lb_voice_status = QLabel("Sẵn sàng thu âm thanh hệ thống hoặc microphone.")
        self.lb_voice_status.setProperty("role", "subtitle")
        self.lb_voice_status.setWordWrap(True)

        for r, (lbl, w) in enumerate([
            ("Nguồn âm thanh:", self.cb_voice_source),
            ("Thiết bị thu:", self.cb_voice_device),
            ("Ngôn ngữ nói:", self.cb_voice_lang),
            ("Nhịp tách câu:", self.cb_voice_pace),
        ]):
            lb = QLabel(lbl)
            lb.setProperty("role", "subtitle")
            g.addWidget(lb, r, 0)
            g.addWidget(w, r, 1, 1, 1 if w is self.cb_voice_device else 2)
        g.addWidget(self.bt_voice_refresh, 1, 2)

        lb_sens = QLabel("Ngưỡng lọc ồn:")
        lb_sens.setProperty("role", "subtitle")
        sens_row = QHBoxLayout()
        sens_row.addWidget(self.sl_voice_sens, 1)
        sens_row.addWidget(self.lb_voice_sens)
        g.addWidget(lb_sens, 4, 0)
        g.addLayout(sens_row, 4, 1, 1, 2)

        lb_vis = QLabel("Sóng âm thanh:")
        lb_vis.setProperty("role", "subtitle")
        g.addWidget(lb_vis, 5, 0)
        g.addWidget(self.visualizer, 5, 1, 1, 2)
        g.addWidget(self.lb_voice_status, 6, 0, 1, 3)
        g.setColumnStretch(1, 1)
        v.addWidget(src_card)

        # Card 2: Phụ đề Vietsub nổi
        sub_card = QFrame()
        sub_card.setProperty("card", True)
        sg = QGridLayout(sub_card)
        sg.setContentsMargins(14, 12, 14, 12)
        sg.setHorizontalSpacing(10)
        sg.setVerticalSpacing(7)

        lb_ssec = QLabel("PHỤ ĐỀ VIETSUB NỔI")
        lb_ssec.setProperty("role", "section")
        sg.addWidget(lb_ssec, 0, 0, 1, 4)

        self.ck_sub_enabled = QCheckBox("Hiện phụ đề nổi")
        self.ck_sub_enabled.toggled.connect(lambda on: self._set_sub("subtitle_enabled", bool(on)))
        self.ck_sub_bilingual = QCheckBox("Song ngữ (gốc + dịch)")
        self.ck_sub_bilingual.toggled.connect(lambda on: self._set_sub("subtitle_bilingual", bool(on)))
        self.ck_sub_locked = QCheckBox("Khoá xuyên chuột")
        self.ck_sub_locked.toggled.connect(lambda on: self._set_sub("subtitle_locked", bool(on)))

        sg.addWidget(self.ck_sub_enabled, 1, 0, 1, 2)
        sg.addWidget(self.ck_sub_bilingual, 1, 2)
        sg.addWidget(self.ck_sub_locked, 1, 3)

        self.sp_sub_size = QSpinBox()
        self.sp_sub_size.setRange(14, 48)
        self.sp_sub_size.setSingleStep(2)
        self.sp_sub_size.setSuffix(" px")
        self.sp_sub_size.valueChanged.connect(lambda val: self._set_sub("subtitle_font_size", int(val)))

        self.cb_sub_color = QComboBox()
        for hx, name in SUBTITLE_COLORS:
            self.cb_sub_color.addItem(name, hx)
        self.cb_sub_color.currentIndexChanged.connect(
            lambda _i: self._set_sub("subtitle_color", self.cb_sub_color.currentData())
        )

        self.sl_sub_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_sub_opacity.setRange(0, 100)
        self.lb_sub_opacity = QLabel("84%")
        self.lb_sub_opacity.setMinimumWidth(36)
        self.sl_sub_opacity.valueChanged.connect(self._on_sub_opacity)

        self.bt_sub_reset = QPushButton("Đặt lại vị trí")
        self.bt_sub_reset.setAutoDefault(False)
        self.bt_sub_reset.clicked.connect(self.ctl.reset_subtitle_position)

        sg.addWidget(self._sub_lbl("Cỡ chữ:"), 2, 0)
        sg.addWidget(self.sp_sub_size, 2, 1)
        sg.addWidget(self._sub_lbl("Màu chữ:"), 2, 2)
        sg.addWidget(self.cb_sub_color, 2, 3)

        op_row = QHBoxLayout()
        op_row.addWidget(self.sl_sub_opacity, 1)
        op_row.addWidget(self.lb_sub_opacity)
        sg.addWidget(self._sub_lbl("Độ mờ nền:"), 3, 0)
        sg.addLayout(op_row, 3, 1, 1, 2)
        sg.addWidget(self.bt_sub_reset, 3, 3)
        v.addWidget(sub_card)

        # Card 3: Thuyết minh AI (Lồng tiếng)
        dub_card = QFrame()
        dub_card.setProperty("card", True)
        dg = QGridLayout(dub_card)
        dg.setContentsMargins(14, 12, 14, 12)
        dg.setHorizontalSpacing(10)
        dg.setVerticalSpacing(7)

        lb_dsec = QLabel("THUYẾT MINH AI (LỒNG TIẾNG)")
        lb_dsec.setProperty("role", "section")
        self.bt_dub_test = QPushButton("▶ Thử giọng")
        self.bt_dub_test.setProperty("variant", "primary")
        self.bt_dub_test.setAutoDefault(False)
        self.bt_dub_test.clicked.connect(self.ctl.test_dub_voice)
        dg.addWidget(lb_dsec, 0, 0, 1, 3)
        dg.addWidget(self.bt_dub_test, 0, 3)

        self.ck_voice_tts = QCheckBox("Bật thuyết minh AI")
        self.ck_voice_tts.toggled.connect(lambda on: self._set("voice_tts", bool(on)))
        self.ck_dub_auto_speed = QCheckBox("Tự tăng tốc câu dài")
        self.ck_dub_auto_speed.setToolTip("Tự đọc nhanh hơn khi bản dịch dài để không bị trễ nhịp phim")
        self.ck_dub_auto_speed.toggled.connect(lambda on: self._set("dub_auto_speed", bool(on)))
        self.ck_dub_anti_echo = QCheckBox("Chống dội tiếng loa")
        self.ck_dub_anti_echo.toggled.connect(lambda on: self._set("dub_anti_echo", bool(on)))

        dg.addWidget(self.ck_voice_tts, 1, 0, 1, 2)
        dg.addWidget(self.ck_dub_auto_speed, 1, 2)
        dg.addWidget(self.ck_dub_anti_echo, 1, 3)

        self.cb_dub_voice = QComboBox()
        for code, name in DUB_VOICES:
            self.cb_dub_voice.addItem(name, code)
        self.cb_dub_voice.currentIndexChanged.connect(
            lambda _i: self._set("dub_voice", self.cb_dub_voice.currentData())
        )

        self.cb_dub_rate = QComboBox()
        for code, name in DUB_RATES:
            self.cb_dub_rate.addItem(name, code)
        self.cb_dub_rate.currentIndexChanged.connect(
            lambda _i: self._set("dub_rate", self.cb_dub_rate.currentData())
        )

        self.sl_dub_volume = QSlider(Qt.Orientation.Horizontal)
        self.sl_dub_volume.setRange(10, 100)
        self.lb_dub_volume = QLabel("85%")
        self.lb_dub_volume.setMinimumWidth(36)
        self.sl_dub_volume.valueChanged.connect(self._on_dub_volume)

        dg.addWidget(self._sub_lbl("Giọng đọc:"), 2, 0)
        dg.addWidget(self.cb_dub_voice, 2, 1, 1, 3)
        dg.addWidget(self._sub_lbl("Tốc độ:"), 3, 0)
        dg.addWidget(self.cb_dub_rate, 3, 1)
        vol_row = QHBoxLayout()
        vol_row.addWidget(self.sl_dub_volume, 1)
        vol_row.addWidget(self.lb_dub_volume)
        dg.addWidget(self._sub_lbl("Âm lượng:"), 3, 2)
        dg.addLayout(vol_row, 3, 3)
        v.addWidget(dub_card)
        v.addStretch(1)
        return page

    # ------------------------------------------------------------------ Page 2: History & SRT
    def _build_history_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        top = QHBoxLayout()
        hdr = QLabel("Lịch sử dịch giọng nói & Xuất phụ đề .SRT")
        hdr.setProperty("role", "page_title")
        self.bt_copy_sub = QPushButton("Sao chép")
        self.bt_copy_sub.setAutoDefault(False)
        self.bt_copy_sub.clicked.connect(self._on_copy_transcript)
        self.bt_export_srt = QPushButton("Xuất .SRT…")
        self.bt_export_srt.setProperty("variant", "primary")
        self.bt_export_srt.setAutoDefault(False)
        self.bt_export_srt.clicked.connect(self._on_export_srt)
        self.bt_clear_sub = QPushButton("Xoá")
        self.bt_clear_sub.setAutoDefault(False)
        self.bt_clear_sub.clicked.connect(self._on_clear_transcript)
        top.addWidget(hdr, 1)
        top.addWidget(self.bt_copy_sub)
        top.addWidget(self.bt_export_srt)
        top.addWidget(self.bt_clear_sub)
        v.addLayout(top)

        self.txt_transcript = QPlainTextEdit()
        self.txt_transcript.setReadOnly(True)
        self.txt_transcript.setPlaceholderText(
            "Lời nói nhận dạng và bản dịch Vietsub sẽ hiện theo thời gian thực tại đây.\n"
            "Bạn có thể sao chép hoặc bấm 'Xuất .SRT…' để lưu thành file phụ đề chuẩn cho video."
        )
        v.addWidget(self.txt_transcript, 1)

        # Thanh đọc thuyết minh nhanh một câu bất kỳ
        speak_card = QFrame()
        speak_card.setProperty("card", True)
        sh = QHBoxLayout(speak_card)
        sh.setContentsMargins(10, 8, 10, 8)
        sh.setSpacing(8)
        self.ed_quick_dub = QLineEdit()
        self.ed_quick_dub.setPlaceholderText("Nhập hoặc dán câu bất kỳ để nghe thuyết minh AI ngay…")
        self.ed_quick_dub.setStyleSheet("padding: 6px 10px; border-radius: 7px;")
        self.ed_quick_dub.returnPressed.connect(self._on_quick_dub)
        bt_speak = QPushButton("▶ Đọc ngay")
        bt_speak.setProperty("variant", "primary")
        bt_speak.setAutoDefault(False)
        bt_speak.clicked.connect(self._on_quick_dub)
        sh.addWidget(self.ed_quick_dub, 1)
        sh.addWidget(bt_speak)
        v.addWidget(speak_card)
        return page

    # ------------------------------------------------------------------ Page 3: Display & Options
    def _build_display_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(10)

        hdr = QLabel("Hiển thị lớp phủ & Tuỳ chọn")
        hdr.setProperty("role", "page_title")
        v.addWidget(hdr)

        disp = QGroupBox("Hiển thị bản dịch trên màn hình")
        g = QGridLayout(disp)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        self.cb_style = QComboBox()
        for key, name in STYLES:
            self.cb_style.addItem(name, key)
        self.cb_style.currentIndexChanged.connect(lambda _i: self._set("style", self.cb_style.currentData()))

        self.sp_scale = QSpinBox()
        self.sp_scale.setRange(70, 150)
        self.sp_scale.setSingleStep(5)
        self.sp_scale.setSuffix(" %")
        self.sp_scale.setToolTip("100% = đúng cỡ chữ gốc")
        self.sp_scale.valueChanged.connect(lambda val: self._set("font_scale", round(val / 100.0, 2)))

        self.sl_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_opacity.setRange(30, 100)
        self.lb_opacity = QLabel()
        self.lb_opacity.setMinimumWidth(36)
        self.sl_opacity.valueChanged.connect(self._on_opacity)

        self.cb_font = QFontComboBox()
        self.cb_font.currentFontChanged.connect(lambda f: self._set("font_family", f.family()))

        g.addWidget(self._sub_lbl("Màu nền:"), 0, 0)
        g.addWidget(self.cb_style, 0, 1, 1, 2)
        g.addWidget(self._sub_lbl("Cỡ chữ:"), 1, 0)
        g.addWidget(self.sp_scale, 1, 1, 1, 2)
        g.addWidget(self._sub_lbl("Độ che:"), 2, 0)
        g.addWidget(self.sl_opacity, 2, 1)
        g.addWidget(self.lb_opacity, 2, 2)
        g.addWidget(self._sub_lbl("Phông chữ:"), 3, 0)
        g.addWidget(self.cb_font, 3, 1, 1, 2)
        g.setColumnStretch(1, 1)
        v.addWidget(disp)

        opt = QGroupBox("Tuỳ chọn hành vi")
        ov = QVBoxLayout(opt)
        ov.setSpacing(5)
        self.checks = {}
        for key, label in OPTIONS:
            c = QCheckBox(label)
            c.toggled.connect(lambda on, k=key: self._set(k, bool(on)))
            ov.addWidget(c)
            self.checks[key] = c
        self.ck_autostart = QCheckBox("Khởi động cùng Windows")
        self.ck_autostart.toggled.connect(self._on_autostart)
        ov.addWidget(self.ck_autostart)
        v.addWidget(opt)
        v.addStretch(1)
        return page

    # ------------------------------------------------------------------ Page 4: Hotkeys & System
    def _build_hotkeys_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 4, 0)
        v.setSpacing(10)

        hdr = QLabel("Phím tắt toàn cục & Công cụ hệ thống")
        hdr.setProperty("role", "page_title")
        v.addWidget(hdr)

        keys = QGroupBox("Phím tắt (bấm vào ô rồi nhấn tổ hợp phím mới)")
        kg = QGridLayout(keys)
        kg.setHorizontalSpacing(10)
        kg.setVerticalSpacing(8)
        self.key_edits = {}
        for i, (name, label) in enumerate(HOTKEYS):
            e = QKeySequenceEdit()
            if hasattr(e, "setMaximumSequenceLength"):
                e.setMaximumSequenceLength(1)
            if hasattr(e, "setClearButtonEnabled"):
                e.setClearButtonEnabled(True)
            e.setMinimumWidth(130)
            e.editingFinished.connect(self._on_hotkeys)
            kg.addWidget(self._sub_lbl(label + ":"), i, 0)
            kg.addWidget(e, i, 1)
            self.key_edits[name] = e
        self.lb_keys = QLabel()
        self.lb_keys.setWordWrap(True)
        kg.addWidget(self.lb_keys, len(HOTKEYS), 0, 1, 2)
        kg.setColumnStretch(1, 1)
        v.addWidget(keys)

        tools = QGroupBox("Công cụ & Thông tin")
        tg = QGridLayout(tools)
        tg.setSpacing(8)
        items = [
            ("Xoá bộ nhớ bản dịch", self._on_clear_cache),
            ("Ngôn ngữ nhận dạng…", self.ctl.show_languages),
            ("Mở thư mục nhật ký", self.ctl.open_logs),
            ("Khôi phục mặc định", self._on_defaults),
            ("Hướng dẫn sử dụng", self.ctl.open_help),
            ("Thông tin Glossa", self.show_about),
        ]
        for i, (text, fn) in enumerate(items):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            tg.addWidget(b, i // 2, i % 2)
        v.addWidget(tools)
        v.addStretch(1)
        return page

    def _build_quick_bar(self) -> QFrame:
        bar = QFrame()
        bar.setProperty("card", True)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)
        lb = QLabel("Công cụ nhanh:")
        lb.setProperty("role", "muted")
        h.addWidget(lb)
        for text, fn in (("Chọn vùng…", self._on_pick_region), ("Thêm tiếng…", self.ctl.show_languages),
                         ("Thử thuyết minh", self.ctl.test_dub_voice)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch(1)
        return bar

    def _sub_lbl(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setProperty("role", "subtitle")
        return lb

    # ------------------------------------------------------------------ đồng bộ từ trạng thái
    def refresh(self) -> None:
        self._updating = True
        try:
            region_ok = bool(self.cfg.get("region"))
            mode = self.cfg.get("mode", "fullscreen") if region_ok else "fullscreen"
            self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(mode)))
            self._fill_langs()

            i = self.cb_target.findData(self.cfg.get("target_lang", "vi"))
            if i < 0:
                self.cb_target.addItem(self.cfg.get("target_lang", "vi"), self.cfg.get("target_lang", "vi"))
                i = self.cb_target.count() - 1
            self.cb_target.setCurrentIndex(i)

            self.cb_provider.setCurrentIndex(max(0, self.cb_provider.findData(self.cfg.get("provider", "google"))))

            ms = int(self.cfg.get("interval_ms", 500))
            i = self.cb_speed.findData(ms)
            if i < 0:
                self.cb_speed.addItem(f"Tuỳ chỉnh ({ms} ms)", ms)
                i = self.cb_speed.count() - 1
            self.cb_speed.setCurrentIndex(i)

            # Voice, Vietsub & Dubbing
            vsrc = self.cfg.get("voice_source", "loopback")
            self.cb_voice_source.setCurrentIndex(max(0, self.cb_voice_source.findData(vsrc)))
            if self._loaded_audio_source != vsrc:
                self._fill_audio_devices()
            self.cb_voice_lang.setCurrentIndex(
                max(0, self.cb_voice_lang.findData(self.cfg.get("voice_lang", "en-US")))
            )
            self.cb_voice_pace.setCurrentIndex(
                max(0, self.cb_voice_pace.findData(self.cfg.get("voice_pace", "balanced")))
            )
            sens = int(round(float(self.cfg.get("voice_sensitivity", 0.015)) * 1000))
            self.sl_voice_sens.setValue(max(5, min(60, sens)))
            self.lb_voice_sens.setText(str(self.sl_voice_sens.value()))

            self.ck_sub_enabled.setChecked(bool(self.cfg.get("subtitle_enabled", True)))
            self.ck_sub_bilingual.setChecked(bool(self.cfg.get("subtitle_bilingual", True)))
            self.ck_sub_locked.setChecked(bool(self.cfg.get("subtitle_locked", False)))
            self.sp_sub_size.setValue(int(self.cfg.get("subtitle_font_size", 24)))
            sub_op = int(round(float(self.cfg.get("subtitle_opacity", 0.84)) * 100))
            self.sl_sub_opacity.setValue(sub_op)
            self.lb_sub_opacity.setText(f"{sub_op}%")
            self.cb_sub_color.setCurrentIndex(
                max(0, self.cb_sub_color.findData(self.cfg.get("subtitle_color", "#FFFFFF")))
            )

            self.ck_voice_tts.setChecked(bool(self.cfg.get("voice_tts", False)))
            self.ck_dub_auto_speed.setChecked(bool(self.cfg.get("dub_auto_speed", True)))
            self.ck_dub_anti_echo.setChecked(bool(self.cfg.get("dub_anti_echo", True)))
            self.cb_dub_voice.setCurrentIndex(
                max(0, self.cb_dub_voice.findData(self.cfg.get("dub_voice", "vi-VN-HoaiMyNeural")))
            )
            self.cb_dub_rate.setCurrentIndex(
                max(0, self.cb_dub_rate.findData(self.cfg.get("dub_rate", "+15%")))
            )
            dvol = int(round(float(self.cfg.get("dub_volume", 0.85)) * 100))
            self.sl_dub_volume.setValue(dvol)
            self.lb_dub_volume.setText(f"{dvol}%")

            # Display & Options
            self.cb_style.setCurrentIndex(max(0, self.cb_style.findData(self.cfg.get("style", "auto"))))
            self.sp_scale.setValue(int(round(float(self.cfg.get("font_scale", 1.0)) * 100)))
            op = int(round(float(self.cfg.get("box_opacity", 1.0)) * 100))
            self.sl_opacity.setValue(op)
            self.lb_opacity.setText(f"{op}%")
            self.cb_font.setCurrentFont(QFont(self.cfg.get("font_family", "Segoe UI")))
            for key, c in self.checks.items():
                c.setChecked(bool(self.cfg.get(key, False)))
            self.ck_autostart.setChecked(self.ctl.autostart_enabled())

            # Hotkeys
            hk = self.ctl.hotkey_map()
            for name, e in self.key_edits.items():
                e.setKeySequence(hotkey_to_qt(hk.get(name, "")))
            failed = getattr(self.ctl.hotkeys, "failed", [])
            self.lb_keys.setText(
                ("<span style='color:#EF4444'>Không đăng ký được (đang bị chương trình khác dùng): "
                 + ", ".join(failed) + "</span>")
                if failed else ""
            )
            self.lb_keys.setVisible(bool(failed))
        finally:
            self._updating = False
        self.sub_preview.update()
        self._refresh_state()
        self._refresh_status()

    def _fill_langs(self) -> None:
        st = self.ctl.ocr_state
        current = self.cfg.get("ocr_languages", "auto")
        active = st.get("active") or []
        self.cb_lang.clear()
        self.cb_lang.addItem("Tự động — mọi tiếng đã cài" + (f" ({len(active)})" if active else ""), "auto")
        for tag in st.get("available") or []:
            self.cb_lang.addItem(lang_display_name(tag), tag)
        sel = 0
        if isinstance(current, list) and len(current) == 1:
            sel = max(0, self.cb_lang.findData(current[0]))
        self.cb_lang.setCurrentIndex(sel)

    def _fill_audio_devices(self) -> None:
        prev = self._updating
        self._updating = True
        try:
            source = self.cfg.get("voice_source", "loopback")
            self._loaded_audio_source = source
            wanted = self.cfg.get("voice_device", "") or ""
            self.cb_voice_device.clear()
            self.cb_voice_device.addItem("Tự động (Thiết bị mặc định)", "")
            for name, label in list_audio_devices(source):
                self.cb_voice_device.addItem(label, name)
            self.cb_voice_device.setCurrentIndex(max(0, self.cb_voice_device.findData(wanted)))
        finally:
            self._updating = prev

    def _refresh_state(self) -> None:
        pal = get_palette(self.cfg.get("ui_theme", "dark"))
        on = bool(self.ctl.active)
        self.bt_toggle.setChecked(on)
        self.bt_toggle.setText("V  Đang dịch" if on else "E  Tạm dừng")
        color = pal["danger"] if on else pal["blue"]
        hover = pal["danger_hover"] if on else pal["blue_hover"]
        self.bt_toggle.setStyleSheet(
            f"QPushButton {{ background:{color}; color:#FFFFFF; border:none; border-radius:9px; padding:6px 10px; }}"
            f"QPushButton:hover {{ background:{hover}; }}"
        )
        self.bt_toggle.setToolTip(
            "Bật/tạm dừng dịch màn hình (" + self.ctl.hotkey_map().get("toggle", "") + ")"
        )

        self.lb_badge.setText("V" if on else "E")
        self.lb_badge.setStyleSheet(
            f"background:{RED if on else BLUE}; color:#FFFFFF; border-radius:9px; font-size:15pt; font-weight:900;"
        )

        von = bool(getattr(self.ctl, "voice_active", False))
        self.bt_voice.setChecked(von)
        self.bt_voice.setText("●  Đang Vietsub" if von else "▶  Bật Vietsub")
        if von:
            self.bt_voice.setStyleSheet(
                f"QPushButton {{ background:{pal['success']}; color:#FFFFFF; border:none; border-radius:9px; padding:6px 10px; }}"
                f"QPushButton:hover {{ background:{pal['success_hover']}; }}"
            )
        else:
            self.bt_voice.setStyleSheet(
                f"QPushButton {{ background:{pal['btn_bg']}; color:{pal['text']}; "
                f"border:1px solid {pal['border']}; border-radius:9px; padding:6px 10px; }}"
                f"QPushButton:hover {{ background:{pal['btn_hover']}; border-color:{pal['accent']}; }}"
            )
        self.bt_voice.setToolTip(
            "Bật/tắt dịch giọng nói, Vietsub & Thuyết minh (" + self.ctl.hotkey_map().get("voice", "") + ")"
        )

    def _refresh_status(self) -> None:
        self.lb_status.setText(self.ctl.status_text())
        self._refresh_state()

    def set_voice_level(self, level: float) -> None:
        self.visualizer.set_level(level)

    def set_voice_status(self, text: str) -> None:
        self.lb_voice_status.setText(text)

    def append_transcript(self, entry) -> None:
        orig = (entry.original or "").strip()
        tr = (entry.translated or orig).strip()
        if orig and orig.lower() != tr.lower():
            line = f"{orig}\n  → {tr}\n"
        else:
            line = f"{tr}\n"
        self.txt_transcript.appendPlainText(line.rstrip())
        sb = self.txt_transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------------ hành động
    def toggle_expanded(self) -> None:
        self.ext.setVisible(not self.ext.isVisible())
        self.cfg.set("panel_expanded", self.ext.isVisible())

    def _set(self, key: str, value) -> None:
        if not self._updating:
            self.ctl.set_option(key, value)

    def _set_sub(self, key: str, value) -> None:
        if not self._updating:
            self.ctl.set_subtitle_option(key, value)
            self.sub_preview.update()

    def _on_mode(self, _i) -> None:
        if self._updating:
            return
        if self.cb_mode.currentData() == "region":
            if self.cfg.get("region"):
                self.ctl.set_region_mode()
            else:
                self._on_pick_region()
        else:
            self.ctl.set_fullscreen()

    def _on_pick_region(self) -> None:
        self.hide()
        self.ctl.start_region_select()

    def _on_lang(self, _i) -> None:
        if self._updating:
            return
        tag = self.cb_lang.currentData()
        self.ctl.set_ocr_languages("auto" if tag == "auto" else [tag])

    def _on_target(self, _i) -> None:
        if not self._updating:
            self.ctl.set_target(self.cb_target.currentData())

    def _on_provider(self, _i) -> None:
        if not self._updating:
            self.ctl.set_provider(self.cb_provider.currentData())

    def _on_speed(self, _i) -> None:
        if not self._updating:
            self.ctl.set_interval(int(self.cb_speed.currentData()))

    def _on_voice_source(self, _i) -> None:
        if self._updating:
            return
        self.cfg.set("voice_source", self.cb_voice_source.currentData())
        self.cfg.set("voice_device", "")
        self._fill_audio_devices()
        self.ctl.restart_voice_if_active()

    def _on_voice_device(self, _i) -> None:
        if self._updating:
            return
        self.cfg.set("voice_device", self.cb_voice_device.currentData() or "")
        self.ctl.restart_voice_if_active()

    def _on_voice_lang(self, _i) -> None:
        if not self._updating:
            self.cfg.set("voice_lang", self.cb_voice_lang.currentData())

    def _on_voice_sens(self, v: int) -> None:
        self.lb_voice_sens.setText(str(v))
        self._set("voice_sensitivity", round(v / 1000.0, 4))

    def _on_sub_opacity(self, v: int) -> None:
        self.lb_sub_opacity.setText(f"{v}%")
        self._set_sub("subtitle_opacity", round(v / 100.0, 2))

    def _on_dub_volume(self, v: int) -> None:
        self.lb_dub_volume.setText(f"{v}%")
        self._set("dub_volume", round(v / 100.0, 2))

    def _on_quick_dub(self) -> None:
        text = self.ed_quick_dub.text().strip()
        if text:
            self.ctl.voice.dubbing.speak(text, target_lang=self.cfg.get("target_lang", "vi"))

    def _on_copy_transcript(self) -> None:
        text = self.txt_transcript.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
            self.lb_voice_status.setText("Đã sao chép lịch sử phụ đề vào clipboard.")

    def _on_clear_transcript(self) -> None:
        self.txt_transcript.clear()
        self.ctl.voice.clear_history()
        self.ctl.subtitle.clear_subtitle()

    def _on_export_srt(self) -> None:
        srt = self.ctl.voice.export_srt(bilingual=bool(self.cfg.get("subtitle_bilingual", False)))
        if not srt.strip():
            QMessageBox.information(self, "Xuất phụ đề .SRT", "Chưa có lời thoại nào để xuất.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu file phụ đề Vietsub (.SRT)", "vietsub.srt", "SubRip Subtitle (*.srt);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(srt)
            self.lb_voice_status.setText(f"Đã xuất phụ đề: {os.path.basename(path)}")
        except OSError as e:
            QMessageBox.warning(self, "Lỗi lưu file", f"Không lưu được file .SRT:\n{e}")

    def _on_opacity(self, v: int) -> None:
        self.lb_opacity.setText(f"{v}%")
        self._set("box_opacity", round(v / 100.0, 2))

    def _on_autostart(self, on: bool) -> None:
        if not self._updating:
            self.ctl.set_autostart(on)

    def _on_hotkeys(self) -> None:
        if self._updating:
            return
        new = {name: qt_to_hotkey(e.keySequence()) for name, e in self.key_edits.items()}
        bad = [n for n, c in new.items() if c and not parse_hotkey(c)]
        used = [c for c in new.values() if c]
        dup = sorted({c for c in used if used.count(c) > 1})
        if bad or dup:
            names = dict(HOTKEYS)
            msg = []
            if bad:
                msg.append("Không dùng được: " + ", ".join(f"{names[n]} ({new[n]})" for n in bad))
            if dup:
                msg.append("Bị trùng: " + ", ".join(dup))
            self.lb_keys.setText("<span style='color:#EF4444'>" + " • ".join(msg) + "</span>")
            self.lb_keys.setVisible(True)
            return
        self.ctl.set_hotkeys(new)
        self.refresh()

    def _on_clear_cache(self) -> None:
        self.ctl.clear_cache()
        self._refresh_status()

    def _on_defaults(self) -> None:
        r = QMessageBox.question(
            self,
            "Khôi phục mặc định",
            "Đưa các tuỳ chọn hiển thị và tuỳ chọn khác về mặc định?\n(Phím tắt và vùng dịch đã chọn được giữ nguyên.)",
        )
        if r == QMessageBox.StandardButton.Yes:
            self.ctl.reset_defaults()
            self.refresh()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "Thông tin",
            f"<b>Glossa {__version__}</b><br>"
            "Dịch trực tiếp mọi chữ trên màn hình, dịch giọng nói, phụ đề Vietsub &amp; Thuyết minh AI."
            "<br><br>Nhận dạng chữ: Windows OCR<br>Nhận dạng giọng nói: Google Speech / WASAPI Loopback<br>"
            "Thuyết minh AI: Microsoft Edge Neural TTS<br>Dịch thuật: Google Dịch<br><br>"
            f"Thư mục dữ liệu:<br><code>{os.path.dirname(self.cfg.path)}</code>",
        )

    def show_panel(self) -> None:
        self.refresh()
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self._timer.start()

    # ------------------------------------------------------------------ đóng = ẩn
    def hideEvent(self, ev) -> None:
        self._timer.stop()
        super().hideEvent(ev)

    def closeEvent(self, ev) -> None:
        ev.ignore()
        self.hide()

    def reject(self) -> None:
        self.hide()
