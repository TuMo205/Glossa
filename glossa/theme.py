"""Hệ thống giao diện hiện đại v2 (Sidebar Navigation, Dark/Light, SVG checkmark & chevron)."""

from __future__ import annotations

import os
import tempfile

_ASSET_DIR = os.path.join(tempfile.gettempdir(), "glossa_ui_assets")


def _ensure_svg_assets() -> dict[str, str]:
    """Tạo các file SVG nhỏ (dấu tích checkbox, mũi tên combobox) để Qt QSS hiển thị sắc nét."""
    os.makedirs(_ASSET_DIR, exist_ok=True)
    svgs = {
        "check_white": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
            '<path d="M2.5 7.2L5.6 10.3L11.5 3.8" fill="none" stroke="#FFFFFF" '
            'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "chev_dark": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            '<path d="M2.5 4.2L6 7.8L9.5 4.2" fill="none" stroke="#94A3B8" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "chev_light": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            '<path d="M2.5 4.2L6 7.8L9.5 4.2" fill="none" stroke="#475569" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
    }
    paths = {}
    for name, content in svgs.items():
        p = os.path.join(_ASSET_DIR, f"{name}.svg")
        if not os.path.isfile(p):
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content)
            except OSError:
                pass
        paths[name] = p.replace("\\", "/")
    return paths


PALETTES = {
    "dark": {
        "bg": "#0B1120",
        "sidebar": "#0F172A",
        "surface": "#151F32",
        "surface_alt": "#1E293B",
        "border": "#263349",
        "border_hover": "#3B4D6B",
        "input_bg": "#0B1120",
        "text": "#F8FAFC",
        "text_sec": "#94A3B8",
        "text_muted": "#64748B",
        "accent": "#6366F1",
        "accent_hover": "#4F46E5",
        "accent_soft": "rgba(99, 102, 241, 0.16)",
        "danger": "#EF4444",
        "danger_hover": "#DC2626",
        "blue": "#3B82F6",
        "blue_hover": "#2563EB",
        "success": "#10B981",
        "success_hover": "#059669",
        "warn": "#F59E0B",
        "btn_bg": "#1E293B",
        "btn_hover": "#283548",
        "btn_pressed": "#151F32",
        "scroll_handle": "#334155",
    },
    "light": {
        "bg": "#F1F5F9",
        "sidebar": "#FFFFFF",
        "surface": "#FFFFFF",
        "surface_alt": "#F8FAFC",
        "border": "#E2E8F0",
        "border_hover": "#CBD5E1",
        "input_bg": "#F8FAFC",
        "text": "#0F172A",
        "text_sec": "#475569",
        "text_muted": "#64748B",
        "accent": "#4F46E5",
        "accent_hover": "#4338CA",
        "accent_soft": "rgba(79, 70, 229, 0.10)",
        "danger": "#DC2626",
        "danger_hover": "#B91C1C",
        "blue": "#2563EB",
        "blue_hover": "#1D4ED8",
        "success": "#059669",
        "success_hover": "#047857",
        "warn": "#D97706",
        "btn_bg": "#F1F5F9",
        "btn_hover": "#E2E8F0",
        "btn_pressed": "#CBD5E1",
        "scroll_handle": "#CBD5E1",
    },
}


def get_palette(theme: str = "dark") -> dict:
    return PALETTES["light" if theme == "light" else "dark"]


def build_stylesheet(theme: str = "dark") -> str:
    p = get_palette(theme)
    assets = _ensure_svg_assets()
    check_url = assets["check_white"]
    chev_url = assets["chev_light" if theme == "light" else "chev_dark"]

    return f"""
    QDialog, QWidget#GlossaRoot {{
        background-color: {p['bg']};
        color: {p['text']};
        font-family: "Segoe UI", "Inter", sans-serif;
        font-size: 9.5pt;
    }}
    QFrame#Sidebar {{
        background-color: {p['sidebar']};
        border-right: 1px solid {p['border']};
    }}
    QLabel {{
        color: {p['text']};
        background: transparent;
    }}
    QLabel[role="subtitle"] {{
        color: {p['text_sec']};
        font-size: 8.5pt;
    }}
    QLabel[role="muted"] {{
        color: {p['text_muted']};
        font-size: 8.2pt;
    }}
    QLabel[role="title"] {{
        color: {p['text']};
        font-size: 13pt;
        font-weight: 700;
    }}
    QLabel[role="page_title"] {{
        color: {p['text']};
        font-size: 12pt;
        font-weight: 700;
    }}
    QLabel[role="section"] {{
        color: {p['text_sec']};
        font-size: 8.5pt;
        font-weight: 700;
        letter-spacing: 0.3px;
    }}
    QFrame[card="true"] {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 12px;
    }}
    QGroupBox {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 10px;
        margin-top: 12px;
        padding: 12px 12px 10px 12px;
        font-weight: 600;
        color: {p['text_sec']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 6px;
        background-color: {p['bg']};
        color: {p['text_sec']};
        font-size: 8.5pt;
        font-weight: 700;
    }}
    QPushButton[nav="true"] {{
        background-color: transparent;
        color: {p['text_sec']};
        border: none;
        border-radius: 9px;
        padding: 9px 12px;
        text-align: left;
        font-weight: 600;
        font-size: 9.5pt;
    }}
    QPushButton[nav="true"]:hover {{
        background-color: {p['accent_soft']};
        color: {p['text']};
    }}
    QPushButton[nav="true"]:checked {{
        background-color: {p['accent']};
        color: #FFFFFF;
    }}
    QPushButton {{
        background-color: {p['btn_bg']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 6px 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {p['btn_hover']};
        border-color: {p['border_hover']};
    }}
    QPushButton:pressed {{
        background-color: {p['btn_pressed']};
    }}
    QPushButton:disabled {{
        color: {p['text_muted']};
        background-color: {p['surface_alt']};
        border-color: {p['border']};
    }}
    QPushButton[variant="primary"] {{
        background-color: {p['accent']};
        color: #FFFFFF;
        border: 1px solid {p['accent']};
    }}
    QPushButton[variant="primary"]:hover {{
        background-color: {p['accent_hover']};
        border-color: {p['accent_hover']};
    }}
    QPushButton[variant="danger_outline"] {{
        background-color: transparent;
        border: 1px solid {p['border']};
        color: {p['danger']};
    }}
    QPushButton[variant="danger_outline"]:hover {{
        background-color: rgba(239, 68, 68, 0.12);
        border-color: {p['danger']};
    }}
    QComboBox, QSpinBox, QKeySequenceEdit, QFontComboBox {{
        background-color: {p['input_bg']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 5px 10px;
        min-height: 22px;
    }}
    QComboBox:hover, QSpinBox:hover, QKeySequenceEdit:hover {{
        border-color: {p['border_hover']};
    }}
    QComboBox:focus, QSpinBox:focus, QKeySequenceEdit:focus {{
        border-color: {p['accent']};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 26px;
        border: none;
    }}
    QComboBox::down-arrow {{
        image: url("{chev_url}");
        width: 12px;
        height: 12px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {p['surface']};
        color: {p['text']};
        border: 1px solid {p['border']};
        selection-background-color: {p['accent']};
        selection-color: #FFFFFF;
        outline: none;
        padding: 4px;
    }}
    QCheckBox {{
        color: {p['text']};
        spacing: 8px;
        padding: 2px 0;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1.5px solid {p['border_hover']};
        background-color: {p['input_bg']};
    }}
    QCheckBox::indicator:hover {{
        border-color: {p['accent']};
    }}
    QCheckBox::indicator:checked {{
        background-color: {p['accent']};
        border-color: {p['accent']};
        image: url("{check_url}");
    }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {p['input_bg']};
        border: 1px solid {p['border']};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {p['accent']};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: #FFFFFF;
        border: 2px solid {p['accent']};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 8px;
    }}
    QProgressBar {{
        background-color: {p['input_bg']};
        border: 1px solid {p['border']};
        border-radius: 5px;
        text-align: center;
        color: {p['text']};
    }}
    QProgressBar::chunk {{
        background-color: {p['success']};
        border-radius: 4px;
    }}
    QPlainTextEdit, QTextEdit {{
        background-color: {p['input_bg']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 8px;
        selection-background-color: {p['accent']};
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['scroll_handle']};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QMenu {{
        background-color: {p['surface']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 14px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background-color: {p['accent']};
        color: #FFFFFF;
    }}
    QMenu::separator {{
        height: 1px;
        background: {p['border']};
        margin: 4px 8px;
    }}
    """
