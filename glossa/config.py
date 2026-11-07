"""Đọc/ghi config.json (trong thư mục dữ liệu, xem paths.py). Thiếu khoá nào thì dùng giá trị mặc định."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading

from . import paths

log = logging.getLogger(__name__)

DEFAULTS = {
    "target_lang": "vi",
    "provider": "google",              # "google" | "google_gtx" (tự chuyển sang cái còn lại khi lỗi)
    "ocr_languages": "auto",           # "auto" hoặc danh sách, vd ["en-US"]
    "max_ocr_engines": 8,              # tối đa số bộ OCR (mỗi hệ chữ một bộ) ở chế độ tự động
    "interval_ms": 500,                # chu kỳ quét màn hình
    "mode": "fullscreen",              # "fullscreen" | "region"
    "region": None,                    # [x, y, w, h] pixel thật
    "region_upscale": 2.0,             # phóng ảnh vùng nhỏ cho OCR chính xác hơn
    "include_taskbar": False,
    "style": "auto",                   # "auto" (giống màu gốc) | "dark" | "light"
    "box_opacity": 1.0,
    "font_family": "Segoe UI",
    "font_scale": 1.0,
    "hover_reveal": False,             # rê chuột lên bản dịch để xem chữ gốc
    "show_region_border": True,
    "refine_small_text": True,         # đọc lại chữ nhỏ ở độ phân giải 2× để dịch đúng hơn
    "persist_cache": True,             # nhớ bản dịch chữ giao diện ngắn qua các lần mở
    "exclude_from_capture": True,      # lớp phủ không lọt vào ảnh chụp/quay màn hình
    "max_blocks": 250,
    "start_paused": False,
    "show_panel_on_start": True,       # mở bảng điều khiển khi khởi động (như Unikey)
    "panel_expanded": False,           # bảng điều khiển đang mở rộng
    "hotkeys": {
        "toggle": "Ctrl+Alt+T",
        "hide": "Ctrl+Alt+H",
        "region": "Ctrl+Alt+R",
        "fullscreen": "Ctrl+Alt+F",
        "panel": "Ctrl+Alt+P",
        "quit": "Ctrl+Alt+Q",
    },
    "log_level": "INFO",
    "config_version": 2,
}


def _migrate(user: dict) -> dict:
    """Nâng cấp config.json của bản cũ."""
    if int(user.get("config_version", 1) or 1) < 2:
        # bản cũ ghi sẵn max_ocr_engines = 3 => tiếng Hàn, Nga... đã cài cũng không được dùng
        if user.get("max_ocr_engines") == 3:
            user.pop("max_ocr_engines")
        user["config_version"] = 2
    return user


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, path: str | None = None):
        self.path = path or paths.data_path("config.json")
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                with open(self.path, encoding="utf-8-sig") as f:
                    user = json.load(f)
                if isinstance(user, dict):
                    migrated = int(user.get("config_version", 1) or 1) < DEFAULTS["config_version"]
                    self._data = _merge(copy.deepcopy(DEFAULTS), _migrate(user))
                    if migrated:
                        self.save()
            except FileNotFoundError:
                self.save()
            except Exception as e:
                log.warning("config.json lỗi (%s) - dùng mặc định", e)

    def save(self) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except OSError as e:
                log.warning("Không lưu được config.json: %s", e)

    def get(self, key: str, default=None):
        with self._lock:
            v = self._data.get(key, default)
            return copy.deepcopy(v) if isinstance(v, (dict, list)) else v

    def set(self, key: str, value, save: bool = True) -> None:
        with self._lock:
            self._data[key] = value
            if save:
                self.save()
