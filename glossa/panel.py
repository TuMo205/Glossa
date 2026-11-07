"""Bảng điều khiển kiểu Unikey.

- Gọn: phần cơ bản ở trên, bấm "Mở rộng" mới hiện các tuỳ chọn khác.
- Đổi gì có hiệu lực ngay và được lưu vào config.json (không cần nút Áp dụng).
- "Đóng" chỉ ẩn bảng, chương trình vẫn chạy ngầm ở khay; "Kết thúc" mới thoát hẳn.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFontComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .hotkeys import parse_hotkey
from .icons import BLUE, RED, make_icon
from .ocr_windows import lang_display_name
from .translator import PROVIDER_NAMES, TARGETS

INTERVALS = [(300, "Nhanh (0,3 giây)"), (500, "Vừa (0,5 giây)"), (1200, "Tiết kiệm pin (1,2 giây)")]
STYLES = [("auto", "Giống màu chữ gốc"), ("dark", "Nền tối, chữ trắng"), ("light", "Nền sáng, chữ đen")]
HOTKEYS = [
    ("toggle", "Bật / tạm dừng dịch"),
    ("hide", "Ẩn / hiện bản dịch"),
    ("region", "Chọn vùng dịch"),
    ("fullscreen", "Dịch toàn màn hình"),
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
    """QKeySequence -> 'Ctrl+Alt+T' theo cách app đọc (chỉ lấy tổ hợp đầu tiên)."""
    s = seq.toString(QKeySequence.SequenceFormat.PortableText).split(",")[0].strip()
    return s.replace("Meta+", "Win+")


def hotkey_to_qt(combo: str) -> QKeySequence:
    return QKeySequence.fromString((combo or "").replace("Win+", "Meta+"), QKeySequence.SequenceFormat.PortableText)


class ControlPanel(QDialog):
    def __init__(self, controller):
        super().__init__(None, Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint
                         | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinimizeButtonHint
                         | Qt.WindowType.WindowCloseButtonHint)
        self.ctl = controller
        self.cfg = controller.cfg
        self._updating = False
        self.setWindowTitle(f"Glossa {__version__} — Bảng điều khiển")
        self.setWindowIcon(make_icon(True))

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)  # tự co giãn khi mở rộng/thu gọn

        root.addWidget(self._build_basic())
        root.addLayout(self._build_state_row())
        root.addLayout(self._build_buttons())
        self.ext = self._build_extended()
        root.addWidget(self.ext)
        self.ext.setVisible(bool(self.cfg.get("panel_expanded", False)))
        self._update_expand_text()

        self._timer = QTimer(self, interval=1000, timeout=self._refresh_status)
        self.refresh()

    # ------------------------------------------------------------------ dựng giao diện
    def _build_basic(self) -> QGroupBox:
        gb = QGroupBox("Điều khiển")
        g = QGridLayout(gb)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.cb_mode = QComboBox()
        self.cb_mode.addItem("Toàn màn hình", "fullscreen")
        self.cb_mode.addItem("Vùng đã chọn", "region")
        self.cb_mode.currentIndexChanged.connect(self._on_mode)
        self.bt_region = QPushButton("Chọn vùng…")
        self.bt_region.clicked.connect(self._on_pick_region)
        self.cb_lang = QComboBox()
        self.cb_lang.currentIndexChanged.connect(self._on_lang)
        self.bt_langs = QPushButton("Thêm tiếng…")
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
        rows = [("Chế độ dịch:", self.cb_mode), ("Đọc chữ tiếng:", self.cb_lang), ("Dịch sang:", self.cb_target),
                ("Dịch vụ dịch:", self.cb_provider), ("Tốc độ quét:", self.cb_speed)]
        for r, (label, w) in enumerate(rows):
            g.addWidget(QLabel(label), r, 0)
            g.addWidget(w, r, 1, 1, 1 if w in (self.cb_mode, self.cb_lang) else 2)
        g.addWidget(self.bt_region, 0, 2)
        g.addWidget(self.bt_langs, 1, 2)
        g.setColumnMinimumWidth(1, 210)
        return gb

    def _build_state_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.bt_toggle = QPushButton()
        self.bt_toggle.setCheckable(True)
        self.bt_toggle.setMinimumSize(132, 36)
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1.5)
        self.bt_toggle.setFont(f)
        self.bt_toggle.clicked.connect(lambda checked: self.ctl.set_active(checked))
        self.lb_status = QLabel()
        self.lb_status.setWordWrap(True)
        self.lb_status.setMinimumWidth(240)
        self.lb_status.setStyleSheet("color: palette(placeholder-text);")
        row.addWidget(self.bt_toggle)
        row.addWidget(self.lb_status, 1)
        return row

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self.bt_close = QPushButton("Đóng")
        self.bt_close.setDefault(True)
        self.bt_close.clicked.connect(self.hide)
        self.bt_exit = QPushButton("Kết thúc")
        self.bt_exit.clicked.connect(self.ctl.quit)
        self.bt_expand = QPushButton()
        self.bt_expand.clicked.connect(self.toggle_expanded)
        self.bt_help = QPushButton("Hướng dẫn")
        self.bt_help.clicked.connect(self.ctl.open_help)
        self.bt_about = QPushButton("Thông tin")
        self.bt_about.clicked.connect(self.show_about)
        for b in (self.bt_close, self.bt_exit, self.bt_expand, self.bt_help, self.bt_about):
            b.setAutoDefault(False)
            row.addWidget(b)
        self.bt_close.setAutoDefault(True)
        return row

    def _build_extended(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)

        disp = QGroupBox("Hiển thị bản dịch")
        g = QGridLayout(disp)
        self.cb_style = QComboBox()
        for key, name in STYLES:
            self.cb_style.addItem(name, key)
        self.cb_style.currentIndexChanged.connect(
            lambda _i: self._set("style", self.cb_style.currentData()))
        self.sp_scale = QSpinBox()
        self.sp_scale.setRange(70, 150)
        self.sp_scale.setSingleStep(5)
        self.sp_scale.setSuffix(" %")
        self.sp_scale.setToolTip("100% = đúng cỡ chữ gốc")
        self.sp_scale.valueChanged.connect(lambda v: self._set("font_scale", round(v / 100.0, 2)))
        self.sl_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sl_opacity.setRange(30, 100)
        self.lb_opacity = QLabel()
        self.lb_opacity.setMinimumWidth(36)
        self.sl_opacity.valueChanged.connect(self._on_opacity)
        self.cb_font = QFontComboBox()
        self.cb_font.setMaximumWidth(170)
        self.cb_font.currentFontChanged.connect(lambda f: self._set("font_family", f.family()))
        g.addWidget(QLabel("Màu nền:"), 0, 0)
        g.addWidget(self.cb_style, 0, 1, 1, 2)
        g.addWidget(QLabel("Cỡ chữ:"), 1, 0)
        g.addWidget(self.sp_scale, 1, 1, 1, 2)
        g.addWidget(QLabel("Độ che:"), 2, 0)
        g.addWidget(self.sl_opacity, 2, 1)
        g.addWidget(self.lb_opacity, 2, 2)
        g.addWidget(QLabel("Phông chữ:"), 3, 0)
        g.addWidget(self.cb_font, 3, 1, 1, 2)
        g.setRowStretch(4, 1)  # dồn các dòng lên trên, không giãn theo nhóm bên cạnh

        opt = QGroupBox("Tuỳ chọn")
        ov = QVBoxLayout(opt)
        ov.setSpacing(3)
        self.checks = {}
        for key, label in OPTIONS:
            c = QCheckBox(label)
            c.toggled.connect(lambda on, k=key: self._set(k, bool(on)))
            ov.addWidget(c)
            self.checks[key] = c
        self.ck_autostart = QCheckBox("Khởi động cùng Windows")
        self.ck_autostart.toggled.connect(self._on_autostart)
        ov.addWidget(self.ck_autostart)
        top.addWidget(disp, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(opt, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        keys = QGroupBox("Phím tắt (bấm vào ô rồi nhấn tổ hợp phím mới)")
        kg = QGridLayout(keys)
        kg.setHorizontalSpacing(8)
        self.key_edits = {}
        for i, (name, label) in enumerate(HOTKEYS):
            e = QKeySequenceEdit()
            if hasattr(e, "setMaximumSequenceLength"):
                e.setMaximumSequenceLength(1)
            if hasattr(e, "setClearButtonEnabled"):
                e.setClearButtonEnabled(True)
            e.setMinimumWidth(120)
            e.editingFinished.connect(self._on_hotkeys)
            kg.addWidget(QLabel(label + ":"), i // 2, (i % 2) * 2)
            kg.addWidget(e, i // 2, (i % 2) * 2 + 1)
            self.key_edits[name] = e
        self.lb_keys = QLabel()
        self.lb_keys.setWordWrap(True)
        kg.addWidget(self.lb_keys, (len(HOTKEYS) + 1) // 2, 0, 1, 4)
        v.addWidget(keys)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        for text, fn in (("Xoá bộ nhớ bản dịch", self._on_clear_cache),
                         ("Ngôn ngữ nhận dạng…", self.ctl.show_languages),
                         ("Mở thư mục nhật ký", self.ctl.open_logs),
                         ("Mặc định", self._on_defaults)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            tools.addWidget(b)
        v.addLayout(tools)
        return w

    # ------------------------------------------------------------------ đồng bộ từ trạng thái
    def refresh(self) -> None:
        """Đặt lại mọi ô theo cấu hình/trạng thái hiện tại (không gây ra thay đổi ngược lại)."""
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
            i = self.cb_provider.findData(self.cfg.get("provider", "google"))
            self.cb_provider.setCurrentIndex(max(0, i))
            ms = int(self.cfg.get("interval_ms", 500))
            i = self.cb_speed.findData(ms)
            if i < 0:
                self.cb_speed.addItem(f"Tuỳ chỉnh ({ms} ms)", ms)
                i = self.cb_speed.count() - 1
            self.cb_speed.setCurrentIndex(i)
            self.cb_style.setCurrentIndex(max(0, self.cb_style.findData(self.cfg.get("style", "auto"))))
            self.sp_scale.setValue(int(round(float(self.cfg.get("font_scale", 1.0)) * 100)))
            op = int(round(float(self.cfg.get("box_opacity", 1.0)) * 100))
            self.sl_opacity.setValue(op)
            self.lb_opacity.setText(f"{op}%")
            self.cb_font.setCurrentFont(QFont(self.cfg.get("font_family", "Segoe UI")))
            for key, c in self.checks.items():
                c.setChecked(bool(self.cfg.get(key, False)))
            self.ck_autostart.setChecked(self.ctl.autostart_enabled())
            hk = self.ctl.hotkey_map()
            for name, e in self.key_edits.items():
                e.setKeySequence(hotkey_to_qt(hk.get(name, "")))
            failed = getattr(self.ctl.hotkeys, "failed", [])
            self.lb_keys.setText(("<span style='color:#C62828'>Không đăng ký được (đang bị chương trình khác "
                                  "dùng): " + ", ".join(failed) + "</span>") if failed else "")
            self.lb_keys.setVisible(bool(failed))
        finally:
            self._updating = False
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

    def _refresh_state(self) -> None:
        on = bool(self.ctl.active)
        self.bt_toggle.setChecked(on)
        self.bt_toggle.setText("V   Đang dịch" if on else "E   Tạm dừng")
        color = RED if on else BLUE
        self.bt_toggle.setStyleSheet(
            f"QPushButton {{ background:{color}; color:white; border:1px solid {color}; border-radius:4px;"
            f" padding:4px 12px; }} QPushButton:hover {{ border:1px solid #222; }}")
        self.bt_toggle.setToolTip("Bấm để " + ("tạm dừng" if on else "bật") + " dịch  ("
                                  + self.ctl.hotkey_map().get("toggle", "") + ")")

    def _refresh_status(self) -> None:
        self.lb_status.setText(self.ctl.status_text())
        self._refresh_state()

    def _update_expand_text(self) -> None:
        self.bt_expand.setText("Thu gọn ▴" if self.ext.isVisible() else "Mở rộng ▾")

    # ------------------------------------------------------------------ hành động
    def toggle_expanded(self) -> None:
        self.ext.setVisible(not self.ext.isVisible())
        self.cfg.set("panel_expanded", self.ext.isVisible())
        self._update_expand_text()

    def _set(self, key: str, value) -> None:
        if not self._updating:
            self.ctl.set_option(key, value)

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
        self.hide()  # để chọn vùng phía sau bảng
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
            self.lb_keys.setText("<span style='color:#C62828'>" + " • ".join(msg) + "</span>")
            self.lb_keys.setVisible(True)
            return
        self.ctl.set_hotkeys(new)
        self.refresh()

    def _on_clear_cache(self) -> None:
        self.ctl.clear_cache()
        self._refresh_status()

    def _on_defaults(self) -> None:
        r = QMessageBox.question(self, "Khôi phục mặc định",
                                 "Đưa các tuỳ chọn hiển thị và tuỳ chọn khác về mặc định?\n"
                                 "(Phím tắt và vùng dịch đã chọn được giữ nguyên.)")
        if r == QMessageBox.StandardButton.Yes:
            self.ctl.reset_defaults()
            self.refresh()

    def show_about(self) -> None:
        QMessageBox.about(
            self, "Thông tin",
            f"<b>Glossa {__version__}</b><br>Dịch trực tiếp mọi chữ trên màn hình sang tiếng Việt."
            "<br><br>Nhận dạng chữ: Windows OCR<br>Dịch: Google Dịch<br><br>"
            f"Thư mục chương trình:<br><code>{os.path.dirname(self.cfg.path)}</code>")

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

    def reject(self) -> None:  # phím Esc
        self.hide()
