"""Hộp thoại "Ngôn ngữ nhận dạng": xem tiếng nào đã đọc được, tích chọn rồi cài thêm gói OCR của Windows
(cần quyền Admin - Windows hiện hộp hỏi quyền). Cài xong chương trình tự dùng ngôn ngữ mới."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from . import winutil
from . import paths

# (mã gói OCR của Windows, tên, hệ chữ)
OCR_LANGS = [
    ("en-US", "Tiếng Anh", "latn"), ("zh-CN", "Tiếng Trung (giản thể)", "zh"),
    ("zh-TW", "Tiếng Trung (phồn thể)", "zh"), ("ja-JP", "Tiếng Nhật", "ja"), ("ko-KR", "Tiếng Hàn", "ko"),
    ("ru-RU", "Tiếng Nga", "cyrl"), ("ar-SA", "Tiếng Ả Rập", "arab"), ("el-GR", "Tiếng Hy Lạp", "grek"),
    ("fr-FR", "Tiếng Pháp", "latn"), ("de-DE", "Tiếng Đức", "latn"), ("es-ES", "Tiếng Tây Ban Nha", "latn"),
    ("it-IT", "Tiếng Ý", "latn"), ("pt-BR", "Tiếng Bồ Đào Nha", "latn"), ("tr-TR", "Tiếng Thổ Nhĩ Kỳ", "latn"),
    ("pl-PL", "Tiếng Ba Lan", "latn"), ("nl-NL", "Tiếng Hà Lan", "latn"),
]
SCRIPT_NAMES = {"latn": "chữ Latin", "zh": "chữ Hán", "ja": "tiếng Nhật", "ko": "tiếng Hàn", "cyrl": "chữ Kirin",
                "arab": "chữ Ả Rập", "grek": "chữ Hy Lạp", "hebr": "chữ Do Thái", "thai": "chữ Thái"}


def is_installed(code: str, available: list) -> bool:
    """Gói OCR 'code' (vd ja-JP, zh-TW) có trong danh sách Windows báo (vd ['ja', 'zh-Hant-TW']) không."""
    c = code.lower()
    prim = c.split("-")[0]
    for tag in available or []:
        t = tag.lower()
        if t.split("-")[0] != prim:
            continue
        if prim != "zh":
            return True
        simplified = "hans" in t or t.endswith(("-cn", "-sg")) or t == "zh"
        if (c == "zh-cn") == simplified:
            return True
    return False


def _name(code: str) -> str:
    return next((n for c, n, _s in OCR_LANGS if c.lower() == code.lower()), code)


def pretty_log_line(line: str) -> str:
    """Dòng nhật ký (không dấu) của script cài -> câu tiếng Việt dễ đọc."""
    ln = line.strip()
    parts = ln.split()
    if ln.startswith("Dang doc danh sach"):
        return "Đang đọc danh sách gói nhận dạng của Windows…"
    if ln.startswith("Dang cai ") and len(parts) > 2:
        return f"Đang cài {_name(parts[2])}… (1–3 phút, cần Internet)"
    if ln.startswith("XONG ") and len(parts) > 1:
        return f"Đã cài xong {_name(parts[1])}"
    if ln.startswith("Da co san:") and len(parts) > 3:
        return f"{_name(parts[3])} đã có sẵn"
    if ln.startswith("KHONG CO") and "cho" in parts[:-1]:
        return f"Bản Windows này không có gói nhận dạng cho {_name(parts[parts.index('cho') + 1])}"
    if ln.startswith("LOI ") and len(parts) > 1:
        msg = ln.split(":", 1)[1].strip() if ":" in ln else ""
        return f"Lỗi khi cài {_name(parts[1])}" + (f": {msg}" if msg else "")
    if ln == "HOAN TAT":
        return "Hoàn tất"
    return ln


def summarize_log(text: str) -> tuple:
    """(số tiếng cài xong, danh sách lỗi) từ nhật ký của script cài."""
    done = sum(1 for ln in text.splitlines() if ln.startswith("XONG "))
    errors = [ln for ln in text.splitlines() if ln.startswith(("LOI ", "KHONG CO"))]
    return done, errors


class _Bridge(QObject):
    finished = Signal(object)  # mã thoát hoặc None


class LanguageDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint
                         | Qt.WindowType.WindowCloseButtonHint)
        self.ctl = controller
        self.setWindowTitle("Ngôn ngữ nhận dạng chữ")
        self.log_path = paths.data_path("logs", "ocr_install.log")
        self._bridge = _Bridge()
        self._bridge.finished.connect(self._on_finished)
        self._installing = False

        v = QVBoxLayout(self)
        intro = QLabel("Glossa đọc được tiếng nào là nhờ gói nhận dạng chữ (OCR) của Windows. "
                       "Tích chọn các tiếng bạn hay gặp rồi bấm <b>Cài</b> (Windows sẽ hỏi quyền Admin, "
                       "mỗi tiếng mất 1–3 phút và cần Internet).")
        intro.setWordWrap(True)
        v.addWidget(intro)

        gb = QGroupBox("Các tiếng")
        g = QGridLayout(gb)
        g.setHorizontalSpacing(14)
        self.items = {}
        for i, (code, name, _sc) in enumerate(OCR_LANGS):
            cb = QCheckBox(name)
            st = QLabel()
            r, c = divmod(i, 2)
            g.addWidget(cb, r, c * 2)
            g.addWidget(st, r, c * 2 + 1)
            self.items[code] = (cb, st)
        v.addWidget(gb)

        note = QLabel("Tiếng Pháp, Đức, Tây Ban Nha… (chữ Latin) đã đọc được tạm bằng gói tiếng Anh; cài gói "
                      "riêng giúp đọc đúng dấu hơn. Tiếng Thái, Hindi… Windows chưa có gói OCR.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        v.addWidget(note)

        self.lb_progress = QLabel()
        self.lb_progress.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(10)
        self.bar.hide()
        v.addWidget(self.lb_progress)
        v.addWidget(self.bar)

        row = QHBoxLayout()
        self.bt_install = QPushButton("Cài các tiếng đã chọn")
        self.bt_install.clicked.connect(self.install_selected)
        self.bt_refresh = QPushButton("Làm mới")
        self.bt_refresh.clicked.connect(self.ctl.engine.reload_ocr)
        bt_close = QPushButton("Đóng")
        bt_close.clicked.connect(self.hide)
        row.addWidget(self.bt_install)
        row.addWidget(self.bt_refresh)
        row.addStretch(1)
        row.addWidget(bt_close)
        v.addLayout(row)

        self._tail = QTimer(self, interval=800, timeout=self._tail_log)
        self.refresh()

    def refresh(self) -> None:
        available = self.ctl.ocr_state.get("available") or []
        for code, (cb, st) in self.items.items():
            ok = is_installed(code, available)
            cb.setEnabled(not ok and not self._installing)
            if ok:
                cb.setChecked(False)
                st.setText("<span style='color:#2E7D32'>✓ đã cài</span>")
            else:
                st.setText("<span style='color:#888'>chưa cài</span>")
        self.bt_install.setEnabled(not self._installing)

    def selected(self) -> list:
        return [code for code, (cb, _st) in self.items.items() if cb.isChecked() and cb.isEnabled()]

    def install_selected(self) -> None:
        codes = self.selected()
        if not codes:
            QMessageBox.information(self, "Ngôn ngữ nhận dạng", "Hãy tích chọn ít nhất một tiếng chưa cài.")
            return
        ps1 = paths.app_path("install_ocr_languages.ps1")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        try:
            with open(self.log_path, "w", encoding="utf-8"):
                pass
        except OSError:
            pass
        params = (f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1}" '
                  f'-Codes "{",".join(codes)}" -Log "{self.log_path}" -NoPause')
        handle = winutil.run_elevated("powershell.exe", params, paths.APP_DIR, 0)
        if not handle:
            self.lb_progress.setText("<span style='color:#C62828'>Chưa cài: cần bấm <b>Yes</b> khi Windows hỏi "
                                     "quyền Admin.</span>")
            return
        self._installing = True
        self.refresh()
        self.bar.show()
        self.lb_progress.setText("Đang chuẩn bị cài " + ", ".join(codes) + "…")
        self._tail.start()
        threading.Thread(target=lambda: self._bridge.finished.emit(winutil.wait_process(handle)),
                         name="ocr-install", daemon=True).start()

    def _read_log(self) -> str:
        try:
            with open(self.log_path, encoding="utf-8-sig", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def _tail_log(self) -> None:
        lines = [ln.strip() for ln in self._read_log().splitlines() if ln.strip()]
        if lines:
            self.lb_progress.setText(pretty_log_line(lines[-1]))

    def _on_finished(self, code) -> None:
        self._tail.stop()
        self._installing = False
        self.bar.hide()
        done, errors = summarize_log(self._read_log())
        if errors:
            self.lb_progress.setText("<span style='color:#C62828'>"
                                     + "<br>".join(pretty_log_line(e) for e in errors[:4]) + "</span>")
        else:
            self.lb_progress.setText(f"<span style='color:#2E7D32'>Đã cài xong {done} tiếng. "
                                     "Glossa đang nạp ngôn ngữ mới…</span>")
        self.ctl.engine.reload_ocr()  # ocr_info mới => refresh() được gọi lại
        self.refresh()

    def show_dialog(self) -> None:
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()
