"""Chuyển cài đặt từ bản cũ (tên cũ ViTranslator, chạy bằng Python) sang bản cài Glossa - chỉ làm một lần."""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil

from . import paths, winutil

log = logging.getLogger(__name__)

OLD_ID = "ViTranslator"
OLD_MUTEX = "Local\\ViTranslator.SingleInstance"


def old_dirs(run_cmd: str | None = None, home: str | None = None) -> list:
    """Các thư mục có thể chứa bản cũ: theo lệnh khởi động cùng Windows, rồi ~/ViTranslator."""
    found = []
    for m in re.findall(r'"([^"]*app\.py)"', run_cmd or ""):
        found.append(os.path.dirname(m))
    found.append(os.path.join(home or os.path.expanduser("~"), OLD_ID))
    out = []
    for d in found:
        if d and os.path.isfile(os.path.join(d, "config.json")) and d not in out:
            out.append(d)
    return out


def copy_settings(src: str, data_dir: str) -> bool:
    """Chép config.json và bộ nhớ bản dịch của bản cũ. Không ghi đè cài đặt mới đã có."""
    if os.path.exists(os.path.join(data_dir, "config.json")):
        return False
    os.makedirs(os.path.join(data_dir, "cache"), exist_ok=True)
    shutil.copy2(os.path.join(src, "config.json"), os.path.join(data_dir, "config.json"))
    for f in glob.glob(os.path.join(src, "cache", "translations_*.json")):
        try:
            shutil.copy2(f, os.path.join(data_dir, "cache", os.path.basename(f)))
        except OSError:
            pass
    return True


def migrate() -> list:
    """Chạy khi mở bản cài lần đầu. Trả về các việc đã làm (để ghi log)."""
    done = []
    if paths.DATA_DIR == paths.SRC_DIR or os.path.exists(paths.data_path("config.json")):
        return done
    run_cmd = winutil.get_run_value(OLD_ID)
    try:
        for d in old_dirs(run_cmd):
            if copy_settings(d, paths.DATA_DIR):
                done.append(f"chép cài đặt từ {d}")
                break
    except OSError as e:
        log.warning("Không chép được cài đặt bản cũ: %s", e)
    if run_cmd and "app.py" in run_cmd:  # bản cũ đang tự chạy cùng Windows => chuyển sang bản mới
        if winutil.delete_run_value(OLD_ID) and winutil.set_autostart(True):
            done.append("chuyển khởi động cùng Windows sang Glossa")
    for item in done:
        log.info("Bản cũ: %s", item)
    return done


def old_version_running() -> bool:
    return winutil.mutex_exists(OLD_MUTEX)
