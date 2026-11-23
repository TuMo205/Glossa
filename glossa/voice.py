"""Thu âm giọng nói / âm thanh hệ thống (WASAPI Loopback), nhận dạng giọng nói (STT),
dịch sang ngôn ngữ đích, đọc bản dịch (TTS) và xuất phụ đề .SRT (Vietsub)."""

from __future__ import annotations

import io
import json
import logging
import queue
import threading
import time
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import requests

from .dubbing import DubbingEngine
from .translator import SKIP

log = logging.getLogger(__name__)

SPEECH_LANGS = [
    ("en-US", "Tiếng Anh (Mỹ)"),
    ("en-GB", "Tiếng Anh (Anh)"),
    ("ja-JP", "Tiếng Nhật"),
    ("ko-KR", "Tiếng Hàn"),
    ("zh-CN", "Tiếng Trung (Phổ thông)"),
    ("zh-TW", "Tiếng Trung (Đài Loan)"),
    ("fr-FR", "Tiếng Pháp"),
    ("de-DE", "Tiếng Đức"),
    ("es-ES", "Tiếng Tây Ban Nha"),
    ("ru-RU", "Tiếng Nga"),
    ("th-TH", "Tiếng Thái"),
    ("id-ID", "Tiếng Indonesia"),
    ("vi-VN", "Tiếng Việt"),
]

VOICE_SOURCES = [
    ("loopback", "Âm thanh hệ thống (Loa / Video / Phim)"),
    ("mic", "Microphone (Giọng nói trực tiếp)"),
]

VOICE_PACES = [
    ("ultra", "Siêu nhanh — Livestream / Video nhanh (2,0 giây)", 2.0, 0.28),
    ("fast", "Nhanh — YouTube / Phim (2,8 giây)", 2.8, 0.38),
    ("balanced", "Cân bằng — Phim / Hội thoại (4,0 giây)", 4.0, 0.50),
    ("accurate", "Chính xác — Bài giảng / Họp (5,8 giây)", 5.8, 0.70),
]
_PACE_MAP = {k: (mx, sil) for k, _lbl, mx, sil in VOICE_PACES}

TARGET_SR = 16000  # 16 kHz mono PCM cho STT


@dataclass
class SubtitleEntry:
    index: int
    start_s: float
    end_s: float
    original: str
    translated: str
    lang: str
    interim: bool = False


def _fmt_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_srt(entries: list[SubtitleEntry], bilingual: bool = False) -> str:
    """Xuất danh sách phụ đề thành nội dung file .srt chuẩn."""
    blocks = []
    for idx, e in enumerate(entries, start=1):
        lines = []
        if bilingual and e.original and e.original.strip().lower() != e.translated.strip().lower():
            lines.append(e.original.strip())
        lines.append((e.translated or e.original).strip())
        blocks.append(f"{idx}\n{_fmt_srt_time(e.start_s)} --> {_fmt_srt_time(e.end_s)}\n" + "\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def resample_to_pcm16(mono: np.ndarray, src_rate: int, dst_rate: int = TARGET_SR) -> bytes:
    """Chuyển mảng float32 mono [-1..1] từ src_rate sang int16 16kHz PCM."""
    if mono.size == 0:
        return b""
    if src_rate != dst_rate and mono.size > 1:
        n_out = max(1, int(round(mono.size * dst_rate / src_rate)))
        x_old = np.linspace(0.0, 1.0, mono.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
        mono = np.interp(x_new, x_old, mono).astype(np.float32)
    clipped = np.clip(mono, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def pcm16_to_wav(pcm: bytes, sample_rate: int = TARGET_SR) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def list_audio_devices(source: str = "loopback") -> list[tuple[str, str]]:
    """Trả về [(tên_thiết_bị, nhãn_hiển_thị), ...] dùng soundcard (WASAPI thuần, không lỗi PortAudio)."""
    try:
        import soundcard as sc
    except ImportError:
        return []

    out: list[tuple[str, str]] = []
    try:
        if source == "loopback":
            try:
                def_name = sc.default_speaker().name
            except Exception:
                def_name = ""
            for spk in sc.all_speakers():
                name = spk.name
                label = f"{name} (Mặc định)" if name == def_name else name
                out.append((name, label))
        else:
            try:
                def_name = sc.default_microphone().name
            except Exception:
                def_name = ""
            for mic in sc.all_microphones(include_loopback=False):
                name = mic.name
                label = f"{name} (Mặc định)" if name == def_name else name
                out.append((name, label))
    except Exception as e:
        log.warning("Không liệt kê được thiết bị âm thanh: %s", e)
    return out


_STT_SESSION = requests.Session()
_STT_URL_CACHE: dict[str, str] = {}


def _google_stt_url(lang: str) -> str:
    url = _STT_URL_CACHE.get(lang)
    if url:
        return url
    from speech_recognition.recognizers import google as sr_google

    rb = sr_google.create_request_builder(
        endpoint="http://www.google.com/speech-api/v2/recognize",
        key=None,
        language=lang,
        filter_level=0,
    )
    url = rb.build_url().replace("http://", "https://")
    _STT_URL_CACHE[lang] = url
    return url


def _parse_google_stt(body: str) -> str:
    """Đọc JSON nhiều dòng của Google Speech API v2 -> câu nhận dạng tốt nhất."""
    best = ""
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        results = obj.get("result") or []
        for res in results:
            alts = res.get("alternative") or []
            if alts and isinstance(alts[0], dict) and alts[0].get("transcript"):
                best = str(alts[0]["transcript"]).strip()
    return best


def recognize_pcm(pcm: bytes, lang: str = "en-US", sample_rate: int = TARGET_SR, session=None) -> str:
    """Nhận dạng giọng nói tốc độ cao: gửi thẳng PCM 16-bit qua HTTPS keep-alive (~180ms, không gọi flac.exe)."""
    if not pcm:
        return ""
    sess = session or _STT_SESSION
    try:
        url = _google_stt_url(lang)
        r = sess.post(
            url,
            data=pcm,
            headers={"Content-Type": f"audio/l16; rate={sample_rate}"},
            timeout=(4, 8),
        )
        if r.status_code == 200:
            return _parse_google_stt(r.text)
        log.debug("Direct STT HTTP %s, chuyển sang SpeechRecognition", r.status_code)
    except Exception as e:
        log.debug("Direct STT lỗi (%s), chuyển sang SpeechRecognition", e)

    try:
        import speech_recognition as sr
    except ImportError as e:
        raise RuntimeError("Thiếu thư viện SpeechRecognition (pip install SpeechRecognition)") from e

    recognizer = sr.Recognizer()
    audio = sr.AudioData(pcm, sample_rate, 2)
    try:
        text = recognizer.recognize_google(audio, language=lang)
        return (text or "").strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        raise RuntimeError(f"Lỗi nhận dạng giọng nói: {e}") from e


class VoiceEngine:
    """Bộ thu âm thời gian thực (WASAPI loopback / mic) + VAD + STT + dịch Vietsub + Thuyết minh AI."""

    def __init__(self, cfg, translator, on_subtitle=None, on_level=None, on_status=None, on_error=None):
        self.cfg = cfg
        self.translator = translator
        self.on_subtitle = on_subtitle
        self.on_level = on_level
        self.on_status = on_status
        self.on_error = on_error

        self._running = False
        self._lock = threading.Lock()
        self._cap_thread = None
        self._stt_thread = None
        self._stt_q: queue.Queue = queue.Queue(maxsize=4)
        self._stt_pool: ThreadPoolExecutor | None = None
        self._seq_counter = 0
        self._last_emitted_seq = 0
        self.dubbing = DubbingEngine(cfg, on_error=self._emit_error)
        self._session_t0 = 0.0
        self._history: list[SubtitleEntry] = []
        self._next_idx = 1
        self.last_error: str | None = None
        self.active_device: str = ""

    @property
    def running(self) -> bool:
        return self._running

    @property
    def history(self) -> list[SubtitleEntry]:
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
            self._next_idx = 1

    def export_srt(self, bilingual: bool = False) -> str:
        return format_srt(self.history, bilingual=bilingual)

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
            self._running = True
            self.last_error = None
            self._session_t0 = time.monotonic()
            self._seq_counter = 0
            self._last_emitted_seq = 0
            self._stt_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice-stt")
            # dọn hàng đợi cũ
            while not self._stt_q.empty():
                try:
                    self._stt_q.get_nowait()
                except queue.Empty:
                    break
            self._cap_thread = threading.Thread(target=self._capture_loop, name="voice-capture", daemon=True)
            self._stt_thread = threading.Thread(target=self._stt_loop, name="voice-stt-dispatch", daemon=True)
            self._cap_thread.start()
            self._stt_thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self.dubbing.stop_current()
        try:
            self._stt_q.put_nowait(None)
        except queue.Full:
            pass
        if self.on_level:
            self.on_level(0.0)
        if self.on_status:
            self.on_status("Đã tắt dịch giọng nói")

    def shutdown(self) -> None:
        self.stop()
        self.dubbing.shutdown()

    def _emit_error(self, msg: str) -> None:
        self.last_error = msg
        log.warning("VoiceEngine: %s", msg)
        if self.on_error:
            self.on_error(msg)

    def _pick_microphone(self, sc):
        source = self.cfg.get("voice_source", "loopback")
        wanted = (self.cfg.get("voice_device", "") or "").strip()
        if source == "loopback":
            if wanted:
                try:
                    mic = sc.get_microphone(id=wanted, include_loopback=True)
                    if mic is not None:
                        return mic
                except Exception:
                    pass
            spk = sc.default_speaker()
            if spk is None:
                raise RuntimeError("Không tìm thấy loa mặc định để thu âm thanh hệ thống.")
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
            if mic is None:
                mics = [m for m in sc.all_microphones(include_loopback=True) if getattr(m, "isloopback", False)]
                if mics:
                    mic = mics[0]
            if mic is None:
                raise RuntimeError("Không tìm thấy thiết bị âm thanh hệ thống (WASAPI Loopback).")
            return mic
        else:
            if wanted:
                for m in sc.all_microphones(include_loopback=False):
                    if m.name == wanted:
                        return m
            mic = sc.default_microphone()
            if mic is None:
                raise RuntimeError("Không tìm thấy Microphone nào trên máy.")
            return mic

    def _capture_loop(self) -> None:
        from . import winutil

        winutil.com_init_mta()
        try:
            import soundcard as sc
        except ImportError:
            self._emit_error("Thiếu thư viện thu âm 'soundcard' (pip install soundcard).")
            self._running = False
            return

        try:
            mic = self._pick_microphone(sc)
            self.active_device = mic.name
        except Exception as e:
            self._emit_error(str(e))
            self._running = False
            return

        if self.on_status:
            self.on_status(f"Đang nghe: {self.active_device}")

        rate = TARGET_SR
        chunk_frames = int(rate * 0.05)  # 50 ms mỗi khung (800 mẫu ở 16kHz)
        frame_s = chunk_frames / float(rate)
        pre_roll = deque(maxlen=max(1, int(round(0.35 / frame_s))))
        buf: list[np.ndarray] = []
        speaking = False
        silence_acc = 0.0
        speech_acc = 0.0
        speech_start_t = 0.0
        speech_peak = 0.0
        last_interim_t = 0.0
        last_level_emit = 0.0

        try:
            with mic.recorder(samplerate=rate, channels=1, blocksize=chunk_frames) as rec:
                while self._running:
                    try:
                        data = rec.record(numframes=chunk_frames)
                    except Exception as e:
                        if self._running:
                            self._emit_error(f"Lỗi đọc âm thanh: {e}")
                        break

                    samples = np.asarray(data, dtype=np.float32)
                    if samples.ndim > 1:
                        samples = samples.mean(axis=1)

                    # Chống dội tiếng: khi đang phát thuyết minh qua loa, tạm bỏ qua khung thu loopback
                    if (
                        self.cfg.get("voice_source", "loopback") == "loopback"
                        and self.cfg.get("dub_anti_echo", True)
                        and self.dubbing.in_cooldown(0.45)
                    ):
                        pre_roll.clear()
                        if speaking:
                            buf = []
                            speaking = False
                            silence_acc = 0.0
                            speech_acc = 0.0
                        continue

                    rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
                    now = time.monotonic()
                    if self.on_level and now - last_level_emit >= 0.065:
                        last_level_emit = now
                        self.on_level(min(1.0, rms * 6.0))

                    thresh = float(self.cfg.get("voice_sensitivity", 0.015))
                    pace = self.cfg.get("voice_pace", "fast")
                    max_chunk, silence_limit = _PACE_MAP.get(
                        pace,
                        (float(self.cfg.get("voice_max_chunk_s", 2.8)), float(self.cfg.get("voice_silence_s", 0.38))),
                    )

                    # Ngưỡng nhận biết khoảng ngắt giữa câu kể cả khi phim có nhạc nền
                    dip_thresh = max(thresh, speech_peak * 0.28) if speaking else thresh
                    if rms >= dip_thresh:
                        if not speaking:
                            speaking = True
                            speech_start_t = max(0.0, now - self._session_t0 - 0.35)
                            speech_peak = rms
                            last_interim_t = now
                            buf = list(pre_roll)
                            speech_acc = len(buf) * frame_s
                        else:
                            speech_peak = max(speech_peak * 0.96, rms)
                        buf.append(samples)
                        speech_acc += frame_s
                        silence_acc = 0.0
                    else:
                        pre_roll.append(samples)
                        if speaking:
                            buf.append(samples)
                            speech_acc += frame_s
                            silence_acc += frame_s

                    # Phụ đề tạm thời (Live Streaming): cập nhật ngay mỗi ~1.05s trong lúc người nói chưa dứt câu
                    if (
                        speaking
                        and speech_acc >= 1.05
                        and (now - last_interim_t) >= 1.05
                        and speech_acc < (max_chunk - 0.45)
                        and silence_acc < silence_limit
                    ):
                        last_interim_t = now
                        snap = np.concatenate(buf)
                        pcm = resample_to_pcm16(snap, rate, TARGET_SR)
                        end_t = max(speech_start_t + 0.4, now - self._session_t0)
                        self._seq_counter += 1
                        self._enqueue_stt((self._seq_counter, pcm, speech_start_t, end_t, True))

                    if speaking and (silence_acc >= silence_limit or speech_acc >= max_chunk):
                        if speech_acc >= 0.40:
                            audio_mono = np.concatenate(buf)
                            pcm = resample_to_pcm16(audio_mono, rate, TARGET_SR)
                            end_t = max(speech_start_t + 0.4, now - self._session_t0)
                            self._seq_counter += 1
                            self._enqueue_stt((self._seq_counter, pcm, speech_start_t, end_t, False))
                        buf = []
                        speaking = False
                        silence_acc = 0.0
                        speech_acc = 0.0
                        speech_peak = 0.0
        except Exception as e:
            if self._running:
                self._emit_error(f"Không mở được thiết bị âm thanh: {e}")
        finally:
            self._running = False
            if self.on_level:
                self.on_level(0.0)

    def _enqueue_stt(self, item) -> None:
        while self._stt_q.full():
            try:
                self._stt_q.get_nowait()
            except queue.Empty:
                break
        try:
            self._stt_q.put_nowait(item)
        except queue.Full:
            pass

    def _stt_loop(self) -> None:
        # Làm ấm kết nối TLS Google Speech để câu đầu tiên hiện nhanh nhất
        try:
            recognize_pcm(b"\x00" * 3200, lang=self.cfg.get("voice_lang", "en-US"))
        except Exception:
            pass
        while self._running:
            try:
                item = self._stt_q.get(timeout=0.4)
            except queue.Empty:
                continue
            if item is None or not self._running:
                break
            pool = self._stt_pool
            if pool is not None:
                pool.submit(self._process_chunk, item)
        pool = self._stt_pool
        if pool is not None:
            pool.shutdown(wait=False)
            self._stt_pool = None

    def _process_chunk(self, item) -> None:
        if not self._running:
            return
        seq, pcm, start_s, end_s, is_interim = item
        lang = self.cfg.get("voice_lang", "en-US")
        if self.on_status and not is_interim:
            self.on_status("Đang nhận dạng & dịch…")
        try:
            text = recognize_pcm(pcm, lang=lang, sample_rate=TARGET_SR)
        except Exception as e:
            if not is_interim:
                self._emit_error(str(e))
            return
        if not text or not self._running:
            if self.on_status and self._running and not is_interim:
                self.on_status(f"Đang nghe: {self.active_device}")
            return
        translated = self._translate_text(text)
        if not self._running:
            return
        if self.on_status and not is_interim:
            self.on_status(f"Đang nghe: {self.active_device}")
        with self._lock:
            if seq < self._last_emitted_seq:
                return  # bỏ qua câu cũ đến trễ hơn câu mới
            self._last_emitted_seq = seq
            entry = SubtitleEntry(
                index=self._next_idx,
                start_s=start_s,
                end_s=end_s,
                original=text,
                translated=translated,
                lang=lang,
                interim=is_interim,
            )
            if not is_interim:
                self._next_idx += 1
                self._history.append(entry)
                if len(self._history) > 500:
                    self._history = self._history[-500:]
        if self.on_subtitle:
            self.on_subtitle(entry)
        if not is_interim and self.cfg.get("voice_tts", False) and translated:
            self.dubbing.speak(translated, target_lang=self.cfg.get("target_lang", "vi"))

    def _translate_text(self, text: str) -> str:
        cached = self.translator.cache.get(text)
        if cached is not None:
            return text if cached == SKIP else cached
        try:
            res = self.translator.translate_now([text])
            val = res.get(text)
            if val is not None:
                self.translator.cache.put(text, val)
                return text if val == SKIP else val
        except Exception as e:
            log.warning("Lỗi dịch giọng nói: %s", e)
        return text
