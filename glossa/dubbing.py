"""Bộ thuyết minh (lồng tiếng AI) cho Glossa.

- Ưu tiên Google TTS (tiếng Việt rõ ràng, ổn định, không phụ thuộc token) và hỗ trợ thêm
  Microsoft Edge Neural TTS (Hoài My / Nam Minh) với tự động dự phòng sang Google TTS.
- Dự phòng ngoại tuyến qua Windows System.Speech (PowerShell) hoạt động cả trong bản .exe.
- Phát âm thanh bằng Windows MCI (winmm.dll), hỗ trợ chỉnh tốc độ, âm lượng và ngắt ngay.
- Cung cấp cờ `is_speaking` + thời điểm kết thúc để VoiceEngine chống dội tiếng loa.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

DUB_VOICES = [
    ("google_tts", "Google TTS — Tự động theo ngôn ngữ (Ổn định nhất)"),
    ("vi-VN-HoaiMyNeural", "Tiếng Việt — Nữ (Hoài My AI)"),
    ("vi-VN-NamMinhNeural", "Tiếng Việt — Nam (Nam Minh AI)"),
    ("auto", "Tự động theo ngôn ngữ đích (Edge AI)"),
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
    "vi": "Xin chào, đây là giọng thuyết minh tiếng Việt của Glossa.",
    "en": "Hello, this is Glossa AI dubbing voice.",
    "ja": "こんにちは、これはGlossaの吹き替え音声です。",
    "ko": "안녕하세요, Glossa 더빙 음성입니다.",
    "zh-CN": "你好，这是 Glossa 的配音声音。",
}


def resolve_voice(voice_setting: str, target_lang: str = "vi") -> str:
    """Chọn mã giọng phù hợp theo cấu hình và ngôn ngữ đích."""
    v = (voice_setting or "google_tts").strip()
    if v in ("google_tts", "sapi"):
        return v
    if v == "auto":
        return TARGET_DEFAULT_VOICES.get(
            target_lang, TARGET_DEFAULT_VOICES.get(target_lang.split("-")[0], "vi-VN-HoaiMyNeural")
        )
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


def _rate_to_mci_speed(rate_str: str) -> int:
    """'+15%' -> 1150 (1000 = tốc độ chuẩn của Windows MCI)."""
    try:
        pct = int(str(rate_str).replace("%", "").replace("+", "").strip())
    except ValueError:
        pct = 15
    return max(700, min(1650, 1000 + pct * 10))


def _split_tts_chunks(text: str, max_len: int = 180) -> list[str]:
    """Chia câu dài thành các đoạn <= max_len ký tự cho Google TTS."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_len:
        return [text] if text else []
    parts: list[str] = []
    cur = ""
    for seg in re.split(r"(?<=[.!?;,。！？])\s*", text):
        if not seg:
            continue
        if len(cur) + len(seg) + 1 <= max_len:
            cur = f"{cur} {seg}".strip()
        else:
            if cur:
                parts.append(cur)
            while len(seg) > max_len:
                cut = seg.rfind(" ", 0, max_len)
                if cut <= 20:
                    cut = max_len
                parts.append(seg[:cut].strip())
                seg = seg[cut:].strip()
            cur = seg
    if cur:
        parts.append(cur)
    return parts


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
        self._session = requests.Session()
        self._session.headers["User-Agent"] = UA
        self._tmp_dir = tempfile.mkdtemp(prefix="glossa_dub_")

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def in_cooldown(self, cooldown_s: float = 0.45) -> bool:
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
        lang = self.cfg.get("target_lang", "vi")
        voice_cfg = self.cfg.get("dub_voice", "google_tts")
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
            voice = resolve_voice(self.cfg.get("dub_voice", "google_tts"), target_lang)
            base_rate = str(self.cfg.get("dub_rate", "+15%") or "+15%")
            rate = compute_effective_rate(base_rate, text, bool(self.cfg.get("dub_auto_speed", True)))
            volume = max(0.05, min(1.0, float(self.cfg.get("dub_volume", 0.85))))

            try:
                if voice == "google_tts":
                    if self._speak_google(text, target_lang, rate, volume):
                        continue
                    self._speak_sapi(text, volume)
                elif voice == "sapi":
                    self._speak_sapi(text, volume)
                else:
                    # Edge Neural TTS -> tự dự phòng sang Google TTS -> Windows SAPI
                    if self._speak_edge(text, voice, rate, volume):
                        continue
                    if self._speak_google(text, target_lang, rate, volume):
                        continue
                    self._speak_sapi(text, volume)
            except Exception as e:
                log.warning("Thuyết minh lỗi: %s", e)
                if self.on_error:
                    self.on_error(str(e))

    def _speak_google(self, text: str, target_lang: str, rate: str, volume: float) -> bool:
        """Tải MP3 trực tiếp từ Google Translate TTS và phát bằng Windows MCI (chỉnh được tốc độ & âm lượng)."""
        chunks = _split_tts_chunks(text, 180)
        if not chunks:
            return True
        tl = (target_lang or "vi").strip()
        speed_mci = _rate_to_mci_speed(rate)
        played_any = False
        for chunk in chunks:
            if self._stop or self._interrupt.is_set():
                return True
            mp3_path = os.path.join(self._tmp_dir, f"gtts_{uuid.uuid4().hex[:10]}.mp3")
            try:
                r = self._session.get(
                    "https://translate.google.com/translate_tts",
                    params={"ie": "UTF-8", "client": "tw-ob", "tl": tl, "q": chunk},
                    timeout=(5, 10),
                )
                if r.status_code != 200 or len(r.content) < 256:
                    log.info("Google TTS HTTP %s (%d byte)", r.status_code, len(r.content))
                    return played_any
                with open(mp3_path, "wb") as f:
                    f.write(r.content)
                if self._stop or self._interrupt.is_set():
                    return True
                if self._play_mp3(mp3_path, volume, speed=speed_mci):
                    played_any = True
            except Exception as e:
                log.info("Google TTS lỗi (%s)", e)
                return played_any
            finally:
                try:
                    if os.path.exists(mp3_path):
                        os.remove(mp3_path)
                except OSError:
                    pass
        return played_any

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
            log.info("edge-tts không khả dụng (%s), chuyển sang Google TTS", e)
            return False
        finally:
            try:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            except OSError:
                pass

    def _play_mp3(self, path: str, volume: float, speed: int | None = None) -> bool:
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
            if speed is not None and speed != 1000:
                _mci(f"set {alias} speed {int(speed)}")
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

    def _speak_sapi(self, text: str, volume: float = 0.85) -> None:
        """Dự phòng ngoại tuyến bằng Windows System.Speech (hoạt động cả trong bản .exe)."""
        if sys.platform != "win32":
            return
        self._set_speaking(True)
        try:
            safe = text.replace("'", "''")
            vol = int(round(max(10, min(100, volume * 100))))
            ps = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Volume = {vol}; $s.Rate = 1; $s.Speak('{safe}')"
            )
            enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=20,
            )
        except Exception as e:
            log.debug("Windows SAPI lỗi: %s", e)
        finally:
            self._set_speaking(False)
