"""Kiểm thử tính năng dịch giọng nói, phụ đề Vietsub (.SRT, cửa sổ nổi) và giao diện Dark/Light."""

import os
import time

import numpy as np
import pytest

from glossa.config import Config
from glossa.dubbing import DubbingEngine, compute_effective_rate, resolve_voice
from glossa.subtitle import SubtitleWindow
from glossa.theme import build_stylesheet
from glossa.voice import SubtitleEntry, format_srt, pcm16_to_wav, resample_to_pcm16


def test_resample_and_wav_encoding():
    # 0.1 giây sóng sin 440Hz ở 48kHz -> 16kHz mono PCM 16-bit (~1600 mẫu = 3200 bytes)
    t = np.linspace(0, 0.1, 4800, endpoint=False, dtype=np.float32)
    sine = 0.5 * np.sin(2 * np.pi * 440 * t)
    pcm = resample_to_pcm16(sine, 48000, 16000)
    assert len(pcm) == 1600 * 2
    wav = pcm16_to_wav(pcm, 16000)
    assert wav.startswith(b"RIFF") and b"WAVE" in wav[:16]


def test_format_srt_monolingual_and_bilingual():
    entries = [
        SubtitleEntry(1, 0.5, 3.25, "Hello everyone", "Xin chào mọi người", "en-US"),
        SubtitleEntry(2, 4.0, 6.75, "Welcome to Glossa", "Chào mừng đến với Glossa", "en-US"),
    ]
    mono = format_srt(entries, bilingual=False)
    assert "00:00:00,500 --> 00:00:03,250\nXin chào mọi người" in mono
    assert "Hello everyone" not in mono

    bi = format_srt(entries, bilingual=True)
    assert "00:00:00,500 --> 00:00:03,250\nHello everyone\nXin chào mọi người" in bi


def test_theme_stylesheets_render_cleanly():
    dark = build_stylesheet("dark")
    light = build_stylesheet("light")
    assert "#0B1120" in dark and "#F1F5F9" in light
    assert "QFrame#Sidebar" in dark
    assert "check_white.svg" in dark


def test_subtitle_window_show_and_options(qapp, tmp_path):
    cfg = Config(str(tmp_path / "config.json"))
    sub = SubtitleWindow(cfg)
    sub.show_listening(True)
    assert sub.isVisible()
    sub.show_subtitle("Good morning", "Chào buổi sáng")
    assert sub._original == "Good morning"
    assert sub._translated == "Chào buổi sáng"
    sub.cfg.set("subtitle_font_size", 30)
    sub.apply_style()
    sub.clear_subtitle()
    assert sub._translated == ""
    sub.close()


def test_panel_voice_vietsub_and_theme_toggle(qapp, tmp_path, monkeypatch):
    import glossa.controller as ctlmod

    monkeypatch.setattr(ctlmod.paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ctlmod.winutil, "ipc_name", lambda: f"Glossa.VoiceTest.{os.getpid()}.{time.time_ns()}")
    cfg = Config(str(tmp_path / "config.json"))
    cfg.set("show_panel_on_start", False)
    ctl = ctlmod.Controller(qapp, cfg, "app.py")
    try:
        ctl.show_panel()
        p = ctl.panel
        assert p.pages.count() == 5
        # Đổi theme Dark <-> Light
        assert cfg.get("ui_theme") == "dark"
        p._toggle_theme()
        assert cfg.get("ui_theme") == "light"
        p._toggle_theme()
        assert cfg.get("ui_theme") == "dark"

        # Tuỳ chọn Vietsub & Thuyết minh lưu ngay vào config
        p.sp_sub_size.setValue(28)
        p.ck_sub_bilingual.setChecked(False)
        p.ck_voice_tts.setChecked(True)
        p.cb_dub_voice.setCurrentIndex(p.cb_dub_voice.findData("vi-VN-NamMinhNeural"))
        p.cb_dub_rate.setCurrentIndex(p.cb_dub_rate.findData("+30%"))
        p.cb_voice_pace.setCurrentIndex(p.cb_voice_pace.findData("fast"))
        p.sl_dub_volume.setValue(75)
        p.sl_voice_sens.setValue(25)
        assert cfg.get("subtitle_font_size") == 28
        assert cfg.get("subtitle_bilingual") is False
        assert cfg.get("voice_tts") is True
        assert cfg.get("dub_voice") == "vi-VN-NamMinhNeural"
        assert cfg.get("dub_rate") == "+30%"
        assert cfg.get("voice_pace") == "fast"
        assert cfg.get("dub_volume") == 0.75
        assert cfg.get("voice_sensitivity") == 0.025

        # Kiểm tra auto-speed & resolve_voice
        assert compute_effective_rate("+15%", "Ngắn", auto_speed=True) == "+15%"
        assert compute_effective_rate("+15%", "a" * 120, auto_speed=True) == "+35%"
        assert resolve_voice("auto", "vi") == "vi-VN-HoaiMyNeural"
        assert resolve_voice("auto", "en") == "en-US-AriaNeural"
        assert resolve_voice("sapi", "vi") == "sapi"
        dub = DubbingEngine(cfg)
        assert not dub.is_speaking
        dub.shutdown()

        # Nhận phụ đề mới -> hiện trên cửa sổ Vietsub và ghi vào lịch sử
        entry = SubtitleEntry(1, 0.0, 2.0, "How are you?", "Bạn khoẻ không?", "en-US")
        ctl._on_voice_subtitle(entry)
        assert ctl.subtitle._translated == "Bạn khoẻ không?"
        assert "Bạn khoẻ không?" in p.txt_transcript.toPlainText()
        assert "voice" in ctl.hotkey_map()
    finally:
        ctl.quit()
