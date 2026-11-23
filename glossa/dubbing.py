"""Bộ thuyết minh (lồng tiếng AI) cho Glossa.

- Dùng Microsoft Edge Neural TTS (edge-tts) cho giọng tiếng Việt tự nhiên (Hoài My / Nam Minh)
  và nhiều ngôn ngữ khác; tự dự phòng sang Windows SAPI / pyttsx3 khi ngoại tuyến.
- Phát âm thanh bằng Windows MCI (winmm.dll) không cần thêm thư viện nặng, hỗ trợ chỉnh
  âm lượng và ngắt ngay lập tức.
- Cung cấp cờ `is_speaking` + thời điểm kết thúc để VoiceEngine chống dội tiếng khi thu
  âm thanh hệ thống (WASAPI Loopback).
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import queue
import sys
import tempfile
import threading
import time
import uuid

log = logging.getLogger(__name__)

DUB_VOICES = [
    ("vi-VN-HoaiMyNeural", "Tiếng Việt — Nữ (Hoài My AI)"),
    ("vi-VN-NamMinhNeural", "Tiếng Việt — Nam (Nam Minh AI)"),
    ("auto", "Tự động theo ngôn ngữ đích (AI)"),
    ("sapi", "Giọng Windows có sẵn (Offline)"),
]

DUB_RATES = [
    ("-15%", "Chậm (0,85×)"),
    ("+0%", "Bình thường (1,0×)"),
    ("+15%", "Nhanh nhẹ — Phim (1,15×)"),
    ("+30%", "Nhanh (1,3×)"),
    ("+45%", "Rất nhanh (1,45×)"),
]

TARGET_DEFAULT_VOICES = {
    "vi": "vi-VN-HoaiMyNeural",
    "en": "en-US-AriaNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural",
    "zh-TW": "zh-TW-HsiaoChenNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "th": "th-TH-PremwadeeNeural",
    "id": "id-ID-GadisNeural",
    "lo": "lo-LA-KeomanyNeural",
    "km": "km-KH-SreymomNeural",
}

SAMPLE_TEXTS = {
    "vi": "Xin chào, đây là giọng thuyết minh tiếng Việt tự nhiên của Glossa.",
    "en": "Hello, this is Glossa AI dubbing voice.",
    "ja": "こんにちは、これはGlossaのAI吹き替え音声です。",
    "ko": "안녕하세요, Glossa AI 더빙 음성입니다.",
    "zh-CN": "你好，这是 Glossa 的 AI 配音声音。",
}


def resolve_voice(voice_setting: str, target_lang: str = "vi") -> str:
    """Chọn mã giọng edge-tts phù hợp theo cấu hình và ngôn ngữ đích."""
    v = (voice_setting or "vi-VN-HoaiMyNeural").strip()
    if v == "sapi":
        return "sapi"
    if v == "auto":
        return TARGET_DEFAULT_VOICES.get(target_lang, TARGET_DEFAULT_VOICES.get(target_lang.split("-")[0], "vi-VN-HoaiMyNeural"))
    return v


def compute_effective_rate(base_rate: str, text: str, auto_speed: bool = True) -> str:
    """Tự tăng tốc độ đọc nhẹ khi câu dịch dài để thuyết minh luôn bắt kịp video."""
    try:
        pct = int(str(base_rate).replace("%", "").replace("+", "").strip())
    except ValueError:
        pct = 15
    if auto_speed:
        n = len((text or "").strip())
        if n > 110:
            pct += 20
        elif n > 70:
            pct += 10
    pct = max(-30, min(65, pct))
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def _mci(cmd: str) -> tuple[int, str]:
    """Gọi winmm.mciSendStringW trên Windows."""
    if sys.platform != "win32":
        return 1, ""
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, len(buf), 0)
    return int(err), buf.value


class DubbingEngine:
    """Luồng tổng hợp và phát thuyết minh AI (không chặn giao diện, hỗ trợ chống dội tiếng)."""

    def __init__(self, cfg, on_state=None, on_error=None):
        self.cfg = cfg
        self.on_state = on_state
        self.on_error = on_error

        self._q: queue.Queue = queue.Queue(maxsize=3)
        self._stop = False
        self._interrupt = threading.Event()
        self._thread: threading.Thread | None = None
        self._speaking = False
        self._last_end = 0.0
        self._lock = threading.Lock()
        self._tmp_dir = tempfile.mkdtemp(prefix="glossa_dub_")

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def in_cooldown(self, cooldown_s: float = 0.45) -> bool:
        """True khi đang phát thuyết minh hoặc vừa phát xong trong `cooldown_s` giây."""
        if self._speaking:
            return True
        return (time.monotonic() - self._last_end) < cooldown_s

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop = False
            self._interrupt.clear()
            self._thread = threading.Thread(target=self._run, name="dubbing-tts", daemon=True)
            self._thread.start()

    def stop_current(self) -> None:
        """Ngắt ngay câu đang đọc và xoá hàng đợi."""
        self._interrupt.set()
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def shutdown(self) -> None:
        self._stop = True
        self.stop_current()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        try:
            for f in os.listdir(self._tmp_dir):
                try:
                    os.remove(os.path.join(self._tmp_dir, f))
                except OSError:
                    pass
            os.rmdir(self._tmp_dir)
        except OSError:
            pass

    def speak(self, text: str, target_lang: str | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.start()
        self._interrupt.clear()
        lang = target_lang or self.cfg.get("target_lang", "vi")
        while self._q.full():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        try:
            self._q.put_nowait((text, lang))
        except queue.Full:
            pass

    def test_voice(self) -> None:
        """Đọc câu mẫu để người dùng nghe thử giọng, tốc độ và âm lượng."""
        lang = self.cfg.get("target_lang", "vi")
        voice_cfg = self.cfg.get("dub_voice", "vi-VN-HoaiMyNeural")
        if voice_cfg.startswith("vi-VN"):
            sample = SAMPLE_TEXTS["vi"]
        else:
            sample = SAMPLE_TEXTS.get(lang, SAMPLE_TEXTS["vi"])
        self.stop_current()
        self.speak(sample, lang)

    def _set_speaking(self, state: bool) -> None:
        self._speaking = state
        if not state:
            self._last_end = time.monotonic()
        if self.on_state:
            self.on_state(state)

    def _run(self) -> None:
        while not self._stop:
            try:
                item = self._q.get(timeout=0.4)
            except queue.Empty:
                continue
            if item is None or self._stop:
                break
            text, target_lang = item
            if self._interrupt.is_set():
                continue
            voice = resolve_voice(self.cfg.get("dub_voice", "vi-VN-HoaiMyNeural"), target_lang)
            base_rate = str(self.cfg.get("dub_rate", "+15%") or "+15%")
            rate = compute_effective_rate(base_rate, text, bool(self.cfg.get("dub_auto_speed", True)))
            volume = max(0.05, min(1.0, float(self.cfg.get("dub_volume", 0.85))))

            try:
                if voice != "sapi" and self._speak_edge(text, voice, rate, volume):
                    continue
                self._speak_sapi(text)
            except Exception as e:
                log.warning("Thuyết minh lỗi: %s", e)
                if self.on_error:
                    self.on_error(str(e))

    def _speak_edge(self, text: str, voice: str, rate: str, volume: float) -> bool:
        try:
            import edge_tts
        except ImportError:
            return False

        mp3_path = os.path.join(self._tmp_dir, f"dub_{uuid.uuid4().hex[:10]}.mp3")
        try:
            async def _synth():
                comm = edge_tts.Communicate(text, voice=voice, rate=rate)
                await comm.save(mp3_path)

            asyncio.run(_synth())
            if self._stop or self._interrupt.is_set() or not os.path.isfile(mp3_path):
                return True
            return self._play_mp3(mp3_path, volume)
        except Exception as e:
            log.info("edge-tts không khả dụng (%s), chuyển sang giọng Windows", e)
            return False
        finally:
            try:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            except OSError:
                pass

    def _play_mp3(self, path: str, volume: float) -> bool:
        if sys.platform != "win32":
            return False
        alias = f"glossa_{uuid.uuid4().hex[:8]}"
        err, _ = _mci(f'open "{path}" type mpegvideo alias {alias}')
        if err != 0:
            log.warning("MCI không mở được file âm thanh (mã %d)", err)
            return False
        self._set_speaking(True)
        try:
            vol_mci = int(round(max(0.0, min(1.0, volume)) * 1000))
            _mci(f"setaudio {alias} volume to {vol_mci}")
            err, _ = _mci(f"play {alias}")
            if err != 0:
                return False
            while not self._stop and not self._interrupt.is_set():
                e2, mode = _mci(f"status {alias} mode")
                if e2 != 0 or mode.strip().lower() != "playing":
                    break
                time.sleep(0.05)
            _mci(f"stop {alias}")
            return True
        finally:
            _mci(f"close {alias}")
            self._set_speaking(False)

    def _speak_sapi(self, text: str) -> None:
        self._set_speaking(True)
        try:
            try:
                import pyttsx3

                eng = pyttsx3.init()
                eng.setProperty("rate", 190)
                eng.setProperty("volume", float(self.cfg.get("dub_volume", 0.85)))
                eng.say(text)
                eng.runAndWait()
                return
            except Exception:
                pass
            if sys.platform == "win32":
                try:
                    import win32com.client

                    sapi = win32com.client.Dispatch("SAPI.SpVoice")
                    sapi.Volume = int(round(float(self.cfg.get("dub_volume", 0.85)) * 100))
                    sapi.Speak(text)
                except Exception:
                    pass
        finally:
            self._set_speaking(False)
