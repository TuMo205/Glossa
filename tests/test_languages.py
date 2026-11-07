"""Nhiều ngôn ngữ: chọn bộ OCR theo hệ chữ, cài gói OCR, đổi ngôn ngữ đích."""

import json
import os
import time

import numpy as np
import pytest

from glossa.engine import PROBE_S, SCRIPT_TTL_S, Engine
from glossa.langdialog import is_installed, summarize_log
from glossa.layout import Segment, merge_engine_results
from glossa.ocr_windows import WindowsOcr
from glossa.textutil import should_translate


class _Lang:
    def __init__(self, tag):
        self.language_tag = tag


def test_auto_mode_uses_one_engine_per_script_for_all_installed_languages():
    avail = [_Lang(t) for t in ["en-US", "fr-FR", "zh-Hans-CN", "ja", "ko", "ru", "ar", "el"]]
    chosen = [lang.language_tag for lang in WindowsOcr._choose(avail, "auto", 8)]
    assert chosen == ["en-US", "zh-Hans-CN", "ja", "ko", "ru", "ar", "el"]  # tiếng Hàn, Nga... đều được dùng
    assert [lang.language_tag for lang in WindowsOcr._choose(avail, ["ko"], 8)] == ["ko"]


def test_merge_marks_which_engine_read_each_line():
    latin = [Segment("Hello world", 0, 0, 100, 14, 14), Segment("garbled ?? x", 0, 40, 100, 14, 14)]
    korean = [Segment("안녕하세요 여러분", 0, 40, 100, 14, 14)]
    out = merge_engine_results([("latn", latin), ("ko", korean)])
    assert sorted((s.text, s.script) for s in out) == [("Hello world", "latn"), ("안녕하세요 여러분", "ko")]


class _MultiOcr:
    supports_scripts = True
    scripts = ["latn", "zh", "ja", "ko", "cyrl"]

    def __init__(self):
        self.calls = []  # tham số scripts mỗi lần gọi
        self.scene = []

    def recognize(self, img, scale=1.0, scripts=None):
        self.calls.append(None if scripts is None else set(scripts))
        out = []
        for text, sc in self.scene:
            if scripts is None or sc in scripts or sc == "latn":
                s = Segment(text, 10, 10, 100, 14, 14)
                s.script = sc
                out.append(s)
        return out


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def test_partial_ocr_runs_only_engines_of_scripts_seen_on_screen():
    ocr = _MultiOcr()
    eng = Engine(_Cfg(), None, None, ocr_factory=lambda *a: ocr, capturer_factory=lambda: None)
    eng.ocr = ocr
    img = np.zeros((40, 40, 4), np.uint8)
    ocr.scene = [("Hello", "latn"), ("こんにちは", "ja")]
    eng._ocr(img, 1.0, full=True)
    assert ocr.calls[-1] is None, "đọc cả màn hình: chạy mọi bộ OCR"
    eng._ocr(img, 1.0)
    assert ocr.calls[-1] == {"latn", "ja"}, "đọc từng phần: chỉ bộ của hệ chữ đang có trên màn hình"
    eng._last_probe = time.monotonic() - PROBE_S - 1
    eng._ocr(img, 1.0)
    assert ocr.calls[-1] is None, "thỉnh thoảng cho mọi bộ đọc thử để phát hiện tiếng mới"
    eng._script_seen["ja"] = time.monotonic() - SCRIPT_TTL_S - 1
    eng._ocr(img, 1.0)
    assert ocr.calls[-1] == {"latn"}, "lâu không thấy tiếng Nhật: tạm nghỉ bộ tiếng Nhật"
    assert set(eng.seen_scripts()) == {"latn"}


def test_vietnamese_is_translated_when_target_is_another_language():
    vi = "Cài đặt tài khoản của bạn"
    assert not should_translate(vi)               # dịch sang tiếng Việt: bỏ qua chữ đã là tiếng Việt
    assert should_translate(vi, target="en")      # dịch sang tiếng Anh: phải dịch


def test_ocr_language_status_and_install_log():
    avail = ["en-US", "zh-Hans-CN", "ja"]
    assert is_installed("en-US", avail) and is_installed("zh-CN", avail) and is_installed("ja-JP", avail)
    assert not is_installed("zh-TW", avail) and not is_installed("ko-KR", avail)
    assert is_installed("zh-TW", ["zh-Hant-TW"]) and not is_installed("zh-CN", ["zh-Hant-TW"])
    done, errors = summarize_log("Dang cai ko-KR ...\nXONG ko-KR\nKHONG CO goi OCR cho th-TH\nHOAN TAT\n")
    assert done == 1 and errors == ["KHONG CO goi OCR cho th-TH"]


def test_old_config_is_migrated_to_use_all_languages(tmp_path):
    from glossa.config import Config

    p = tmp_path / "config.json"
    p.write_text(json.dumps({"max_ocr_engines": 3, "target_lang": "vi", "style": "dark"}), encoding="utf-8")
    cfg = Config(str(p))
    assert cfg.get("max_ocr_engines") == 8 and cfg.get("style") == "dark"
    assert json.loads(p.read_text(encoding="utf-8"))["config_version"] == 2
    p.write_text(json.dumps({"config_version": 2, "max_ocr_engines": 3}), encoding="utf-8")
    assert Config(str(p)).get("max_ocr_engines") == 3   # người dùng tự đặt 3 ở bản mới: giữ nguyên


@pytest.fixture
def ctl(qapp, tmp_path, monkeypatch):
    import glossa.controller as ctlmod
    from glossa.config import Config

    monkeypatch.setattr(ctlmod.paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ctlmod.winutil, "ipc_name", lambda: f"Glossa.TestL.{os.getpid()}.{time.time_ns()}")
    cfg = Config(str(tmp_path / "config.json"))
    cfg.set("show_panel_on_start", False)
    c = ctlmod.Controller(qapp, cfg, "app.py")
    yield c
    c.quit()


def test_switching_target_language_uses_separate_memory(ctl):
    ctl.cache.put("Settings", "Cài đặt")
    ctl.set_target("en")
    assert ctl.translator.target == "en" and ctl.cfg.get("target_lang") == "en"
    assert ctl.cache.get("Settings") is None, "không được dùng bản dịch tiếng Việt khi dịch sang tiếng Anh"
    assert ctl.cache_path.endswith("translations_en.json")
    ctl.set_target("vi")
    assert ctl.cache.get("Settings") == "Cài đặt"   # nạp lại bộ nhớ tiếng Việt đã lưu
    ctl.show_panel()
    p = ctl.panel
    p.cb_target.setCurrentIndex(p.cb_target.findData("ja"))
    assert ctl.translator.target == "ja"


def test_language_dialog_installs_selected_packs(ctl, qapp, monkeypatch):
    import glossa.langdialog as ld

    calls = {}

    def fake_run(file, params, cwd=None, show=0):
        calls["params"] = params
        log = params.split('-Log "')[1].split('"')[0]
        with open(log, "w", encoding="utf-8") as f:
            f.write("Dang cai ko-KR ...\nXONG ko-KR\nHOAN TAT\n")
        return 1234

    monkeypatch.setattr(ld.winutil, "run_elevated", fake_run)
    monkeypatch.setattr(ld.winutil, "wait_process", lambda h, timeout_s=None: 0)
    reloads = []
    monkeypatch.setattr(ctl.engine, "reload_ocr", lambda: reloads.append(1))
    ctl.ocr_state = {"available": ["en-US", "zh-Hans-CN"], "active": ["en-US", "zh-Hans-CN"], "error": None}
    ctl.show_languages()
    d = ctl.lang_dialog
    assert not d.items["en-US"][0].isEnabled(), "tiếng đã cài thì không cần chọn"
    d.items["ko-KR"][0].setChecked(True)
    d.install_selected()
    assert '-Codes "ko-KR"' in calls["params"] and "-NoPause" in calls["params"]
    for _ in range(100):
        qapp.processEvents()
        if reloads:
            break
        time.sleep(0.01)
    assert reloads, "cài xong phải nạp lại OCR"
    assert "Đã cài xong 1 tiếng" in d.lb_progress.text()


def test_language_dialog_reports_declined_admin_prompt(ctl, monkeypatch):
    import glossa.langdialog as ld

    monkeypatch.setattr(ld.winutil, "run_elevated", lambda *a, **k: None)
    ctl.show_languages()
    d = ctl.lang_dialog
    d.items["ja-JP"][0].setChecked(True)
    d.install_selected()
    assert "quyền Admin" in d.lb_progress.text() and d.bt_install.isEnabled()


def test_install_progress_is_shown_in_vietnamese():
    from glossa.langdialog import pretty_log_line

    assert pretty_log_line("Dang cai ja-JP ... (1-3 phut, can Internet)") == "Đang cài Tiếng Nhật… (1–3 phút, cần Internet)"
    assert pretty_log_line("XONG ko-KR") == "Đã cài xong Tiếng Hàn"
    assert pretty_log_line("KHONG CO goi OCR cho th-TH tren ban Windows nay") == \
        "Bản Windows này không có gói nhận dạng cho th-TH"
    assert pretty_log_line("KHONG CO goi OCR cho ar-SA") == "Bản Windows này không có gói nhận dạng cho Tiếng Ả Rập"
    assert pretty_log_line("LOI ru-RU : 0x800f0954") == "Lỗi khi cài Tiếng Nga: 0x800f0954"
    assert pretty_log_line("Da co san: en-US") == "Tiếng Anh đã có sẵn"
