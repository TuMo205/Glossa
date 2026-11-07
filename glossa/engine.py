"""Luồng xử lý chính: chụp màn hình -> phát hiện thay đổi -> OCR -> gom khối -> yêu cầu dịch."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from . import winutil
from .changes import (
    ChangeDetector,
    ensure_min_size,
    expand_with_segments,
    merge_overlapping,
    rect_area,
    rects_intersect,
)
from .colors import DARK_STYLE, LIGHT_STYLE, estimate_colors, is_bold
from .layout import group_segments
from .textutil import should_translate

log = logging.getLogger(__name__)

# OCR hai lượt: chữ nhỏ đọc ở 1× hay sai (đo trên máy: 11px đúng 48%, 13px 74% ở 1× so với 76%, 98% ở 2×),
# còn chữ 18-20px lại đọc 1× tốt hơn. Lượt đầu đọc 1× cho nhanh, sau đó đọc lại riêng chữ nhỏ ở 2×.
SMALL_FONT_PX = 17.0       # cỡ chữ (px thật) dưới mức này thì đọc lại ở 2×
REFINE_SCALE = 2.0
REFINE_MAX_AREA = 200_000  # px thật cho mỗi vùng đọc lại
DIRECT_HI_AREA = 120_000   # vùng thay đổi nhỏ toàn chữ nhỏ: đọc thẳng 2× luôn
REFINE_TICK_S = 0.15       # thời gian tối đa dành cho đọc lại trong một lượt quét
IDLE_AFTER_S = 15.0        # màn hình đứng yên lâu hơn => giãn nhịp quét
IDLE_FACTOR = 1.6
# Nhiều ngôn ngữ: mỗi hệ chữ một bộ OCR. Đọc cả màn hình thì chạy mọi bộ; đọc từng phần thì chỉ chạy bộ của
# hệ chữ vừa thấy trên màn hình, thỉnh thoảng cho mọi bộ đọc thử để phát hiện thứ tiếng mới.
SCRIPT_TTL_S = 90.0
PROBE_S = 20.0


def _is_small(seg) -> bool:
    return not seg.hi and seg.font_px < SMALL_FONT_PX


def refine_rects(segments, W: int, H: int, max_area: int = REFINE_MAX_AREA) -> list:
    """Gom các dòng chữ nhỏ (chưa đọc 2×) thành vài vùng (x0, y0, x1, y1) để đọc lại."""
    rects = []
    for s in sorted((s for s in segments if _is_small(s)), key=lambda s: (s.y, s.x)):
        m = max(3.0, 0.6 * s.h)
        r = (max(0, int(s.x - m)), max(0, int(s.y - m)), min(W, int(s.right + m) + 1), min(H, int(s.bottom + m) + 1))
        for i, q in enumerate(rects):
            if rects_intersect((q[0] - 4, q[1] - 4, q[2] + 4, q[3] + 4), r):
                u = (min(q[0], r[0]), min(q[1], r[1]), max(q[2], r[2]), max(q[3], r[3]))
                if rect_area(u) <= max_area and rect_area(u) <= 1.5 * (rect_area(q) + rect_area(r)) + 5000:
                    rects[i] = u
                    break
        else:
            rects.append(r)
    return rects


@dataclass(frozen=True)
class Target:
    key: str          # khoá trạng thái riêng của vùng chụp
    overlay: str      # lớp phủ sẽ hiển thị kết quả
    rect: tuple       # (x, y, w, h) pixel thật trên màn hình ảo
    scale: float = 1.0


@dataclass
class RenderBlock:
    text: str         # chữ gốc (cũng là khoá tra bản dịch)
    x: float          # toạ độ pixel thật tuyệt đối
    y: float
    w: float
    h: float
    line_h: float
    n_lines: int
    bg: tuple
    fg: tuple
    font_px: float = 0.0  # cỡ chữ gốc ước lượng (pixel thật)
    baseline: float = 0.0  # đường chân chữ của dòng đầu (toạ độ tuyệt đối, pixel thật)
    pitch: float = 0.0    # khoảng cách giữa các đường chân chữ (0 nếu một dòng)
    bold: bool = False    # chữ gốc in đậm (tiêu đề...)


class _State:
    def __init__(self, target: Target, shape):
        self.target = target
        self.shape = shape
        self.ref = None
        self.segments: list = []
        self.blocks: list = []
        self.last_texts: set = set()  # các câu của lần OCR trước (không bị ảnh hưởng khi gỡ bản dịch cũ)
        self.refine: list = []        # các vùng chữ nhỏ đang chờ đọc lại ở 2×


def _default_ocr(languages, max_engines):
    from .ocr_windows import WindowsOcr

    return WindowsOcr(languages, max_engines)


def _default_capturer():
    from .capture import Capturer

    return Capturer()


class Engine(threading.Thread):
    """sink cần có: blocks(overlay_key, target_key, list), ocr_info(dict), status(dict),
    hide_for_capture(), show_after_capture()."""

    BURST_INTERVAL = 0.15   # chu kỳ quét ngay sau khi có chữ mới
    BURST_SECONDS = 1.5     # giữ chu kỳ nhanh bao lâu sau lần có chữ mới gần nhất

    def __init__(self, cfg, translator, sink, ocr_factory=None, capturer_factory=None):
        super().__init__(name="engine", daemon=True)
        self.cfg = cfg
        self.translator = translator
        self.sink = sink
        self._ocr_factory = ocr_factory or _default_ocr
        self._cap_factory = capturer_factory or _default_capturer
        self.det = ChangeDetector()
        self._lock = threading.Lock()
        self._targets: list = []
        self._states: dict = {}
        self._active = True
        self._wake = threading.Event()
        self._stop_ev = threading.Event()
        self._reload = True
        self._reset = False
        self.ocr = None
        self.capture_hide = False  # True: phải ẩn lớp phủ trước khi chụp (Windows cũ)
        self.last_ocr_ms = 0.0
        self._burst_until = 0.0
        self._last_change = time.monotonic()
        self._script_seen: dict = {}
        self._last_probe = 0.0
        self.stats = self._new_stats()

    # --- API an toàn đa luồng ---
    def set_targets(self, targets: list) -> None:
        with self._lock:
            self._targets = list(targets)
            self._reset = True
        self._wake.set()

    def set_active(self, on: bool) -> None:
        self._active = bool(on)
        if on:
            with self._lock:
                self._reset = True
        self._wake.set()

    def reload_ocr(self) -> None:
        self._reload = True
        self._wake.set()

    def refresh(self) -> None:
        with self._lock:
            self._reset = True
        self._wake.set()

    def stop(self) -> None:
        self._stop_ev.set()
        self._wake.set()

    # --- vòng lặp ---
    def _sleep(self, sec: float) -> None:
        self._wake.wait(max(0.0, sec))
        self._wake.clear()

    def run(self) -> None:
        winutil.com_init_mta()
        cap = None
        errors = 0
        while not self._stop_ev.is_set():
            if self._reload:
                self._load_ocr()
            with self._lock:
                targets = list(self._targets)
                if self._reset:
                    self._states = {}
                    self._reset = False
            if not self._active or self.ocr is None or not targets:
                self._sleep(0.5)
                continue
            t0 = time.monotonic()
            try:
                if cap is None:
                    cap = self._cap_factory()
                for tgt, frame in self._grab(cap, targets):
                    if self._stop_ev.is_set() or self._reset or not self._active:
                        break
                    self._process(tgt, frame)
                errors = 0
            except Exception as e:
                errors += 1
                if errors <= 2:
                    log.exception("Lỗi vòng xử lý")
                else:
                    log.warning("Lỗi vòng xử lý (%d): %s", errors, e)
                self.sink.status({"error": str(e)})
                if cap is not None:
                    cap.close()
                    cap = None
                self._sleep(min(5.0, 0.5 * errors))
                continue
            work = time.monotonic() - t0
            period = self.next_period(work)
            self._account(work)
            self._sleep(period - work)
        if cap is not None:
            cap.close()

    def _ocr(self, img, scale: float, full: bool = False) -> list:
        """Gọi OCR với đúng các bộ ngôn ngữ cần thiết."""
        o = self.ocr
        if not getattr(o, "supports_scripts", False):
            return o.recognize(img, scale)
        now = time.monotonic()
        scripts = None
        if full or now - self._last_probe >= PROBE_S:
            self._last_probe = now
        elif len(o.scripts) > 2:
            scripts = {sc for sc in o.scripts if now - self._script_seen.get(sc, -1e9) <= SCRIPT_TTL_S}
        segs = o.recognize(img, scale, scripts=scripts)
        for sg in segs:
            if sg.script:
                self._script_seen[sg.script] = now
        return segs

    def seen_scripts(self) -> list:
        """Các hệ chữ thấy trên màn hình gần đây (để hiện trong bảng điều khiển)."""
        now = time.monotonic()
        return [sc for sc, t in self._script_seen.items() if now - t <= SCRIPT_TTL_S]

    @property
    def refine_pending(self) -> bool:
        return bool(self.cfg.get("refine_small_text", True)) and any(st.refine for st in self._states.values())

    def cpu_budget(self) -> float:
        """Tỉ lệ tối đa của MỘT lõi CPU dành cho chụp + OCR (theo tốc độ quét đã chọn)."""
        b = self.cfg.get("cpu_budget")
        if b:
            return min(1.0, max(0.05, float(b)))
        ms = int(self.cfg.get("interval_ms", 500))
        return 0.5 if ms <= 300 else (0.3 if ms <= 700 else 0.15)

    def next_period(self, work: float) -> float:
        """Thời gian từ đầu lượt này tới đầu lượt sau."""
        interval = max(0.15, float(self.cfg.get("interval_ms", 500)) / 1000.0)
        if self.capture_hide:
            interval = max(interval, 1.0)
        elif time.monotonic() < self._burst_until or self.refine_pending:
            # vừa có chữ mới (cuộn trang, tin nhắn, phụ đề...) hoặc còn chữ nhỏ chờ đọc lại: quét dày hơn
            interval = min(interval, self.BURST_INTERVAL)
        elif time.monotonic() - self._last_change > IDLE_AFTER_S:
            interval *= IDLE_FACTOR  # đứng yên lâu: chụp thưa hơn, có thay đổi là quay lại nhịp thường
        # trần CPU: màn hình đổi liên tục (video, chữ chạy) cũng không ngốn quá ngân sách
        return max(interval, work / self.cpu_budget())

    def _account(self, work: float) -> None:
        s = self.stats
        s["ticks"] += 1
        s["work"] += work
        now = time.monotonic()
        if now - s["t0"] >= 60.0:
            wall = now - s["t0"]
            nb, ns = getattr(self.translator, "n_batches", 0), getattr(self.translator, "n_sentences", 0)
            log.info("Hiệu năng 60 giây: %d lượt quét, %d lần OCR (%.0f ms TB, %d lần đọc lại chữ nhỏ), "
                     "tải %.0f%% một lõi, %d lần có chữ mới, %d lần gọi dịch (%d câu)", s["ticks"], s["ocr_calls"],
                     s["ocr_ms"] / max(1, s["ocr_calls"]), s["refined"], 100.0 * s["work"] / wall,
                     s["requests"], nb - s["tr_b"], ns - s["tr_s"])
            self.stats = self._new_stats()
            self.stats["tr_b"], self.stats["tr_s"] = nb, ns

    @staticmethod
    def _new_stats() -> dict:
        return {"t0": time.monotonic(), "ticks": 0, "work": 0.0, "ocr_calls": 0, "ocr_ms": 0.0, "requests": 0,
                "refined": 0, "tr_b": 0, "tr_s": 0}

    def _load_ocr(self) -> None:
        self._reload = False
        self.ocr = None
        info = {"available": [], "active": [], "error": None}
        try:
            self.ocr = self._ocr_factory(self.cfg.get("ocr_languages", "auto"),
                                         int(self.cfg.get("max_ocr_engines", 8)))
            info["available"] = list(self.ocr.available)
            info["active"] = list(self.ocr.active_tags)
            with self._lock:  # bộ ngôn ngữ mới: đọc lại cả màn hình bằng mọi bộ OCR
                self._reset = True
            self._script_seen = {}
            self._last_probe = 0.0
        except Exception as e:
            name = e.__class__.__name__
            info["error"] = "no_language" if name == "NoOcrLanguage" else f"{name}: {e}"
            log.error("Không khởi tạo được OCR: %s", info["error"])
        with self._lock:
            self._states = {}
        self.sink.ocr_info(info)

    def _grab(self, cap, targets: list) -> list:
        hide = self.capture_hide
        if hide:
            self.sink.hide_for_capture()
        try:
            return [(t, cap.grab(t.rect)) for t in targets]
        finally:
            if hide:
                self.sink.show_after_capture()

    def _process(self, tgt: Target, frame) -> None:
        H, W = frame.shape[:2]
        st = self._states.get(tgt.key)
        if st is None or st.shape != frame.shape or st.target != tgt:
            st = _State(tgt, frame.shape)
            self._states[tgt.key] = st
        det = self.det
        small = det.small(frame)
        regions: list = []
        if st.ref is None:
            full = True
        else:
            mask = det.diff_mask(st.ref, small)
            grid = det.cell_grid(mask)
            if not grid.any():
                self._refine_step(tgt, st, frame)  # màn hình yên: tranh thủ đọc lại chữ nhỏ
                return
            regions = det.regions(grid, W, H)
            # gỡ ngay các bản dịch có nội dung bên dưới đã đổi (cuộn trang, đổi cửa sổ)
            ox, oy = tgt.rect[0], tgt.rect[1]
            # chỉ gỡ khi phần lớn khối đã đổi; thay đổi nhỏ (rê chuột, con trỏ nhấp nháy, hiệu ứng)
            # thì giữ nguyên bản dịch cho tới khi OCR lại xác nhận => không nháy
            stale = float(self.cfg.get("stale_fraction", 0.25))
            keep = [
                b for b in st.blocks
                if det.changed_fraction(mask, (b.x - ox, b.y - oy, b.x - ox + b.w, b.y - oy + b.h)) < stale
            ]
            if len(keep) != len(st.blocks):
                st.blocks = keep
                self.sink.blocks(tgt.overlay, tgt.key, list(keep))
            area = sum(rect_area(r) for r in regions)
            full = area > 0.45 * W * H or len(regions) > 12
        if not full:
            rects = merge_overlapping([
                ensure_min_size(expand_with_segments(r, st.segments, W, H), 64, 40, W, H) for r in regions
            ])
            if len(rects) > 1:
                # nhiều vùng nhỏ gần nhau: OCR một lần trên vùng bao (mỗi lần gọi OCR tốn chi phí cố định)
                u = (min(r[0] for r in rects), min(r[1] for r in rects),
                     max(r[2] for r in rects), max(r[3] for r in rects))
                # chỉ gộp khi các vùng thật sự sát nhau: OCR lại cả những dòng không đổi nằm giữa vừa tốn
                # CPU vừa làm khung chữ của chúng dao động (vùng cắt khác => OCR trả khung khác)
                if rect_area(u) <= 1.25 * sum(rect_area(r) for r in rects) + 15_000:
                    rects = [expand_with_segments(u, st.segments, W, H)]
            full = sum(rect_area(r) for r in rects) > 0.45 * W * H
        self._last_change = time.monotonic()
        refine = bool(self.cfg.get("refine_small_text", True)) and tgt.scale < 1.5
        t0 = time.monotonic()
        if full:
            st.segments = self._ocr(frame, tgt.scale, full=True)
            st.ref = small
            st.refine = refine_rects(st.segments, W, H) if refine else []
            self.stats["ocr_calls"] += 1
        else:
            for r in rects:
                x0, y0, x1, y1 = r
                prev = [s for s in st.segments if rects_intersect(s.rect(), r)]
                # vùng đổi nhỏ chỉ chứa chữ nhỏ (đồng hồ, trạng thái, chữ giao diện): đọc thẳng 2×
                hi = refine and bool(prev) and rect_area(r) <= DIRECT_HI_AREA and \
                    all(s.font_px < SMALL_FONT_PX for s in prev)
                found = [s.offset(x0, y0) for s in
                         self._ocr(frame[y0:y1, x0:x1], REFINE_SCALE if hi else tgt.scale)]
                for s in found:
                    s.hi = hi
                st.segments = [s for s in st.segments if not rects_intersect(s.rect(), r)] + found
                st.refine = [q for q in st.refine if not rects_intersect(q, r)]
                if refine:
                    st.refine += refine_rects(found, W, H)
                det.update_ref(st.ref, small, r)
                self.stats["ocr_calls"] += 1
        self.last_ocr_ms = (time.monotonic() - t0) * 1000.0
        self.stats["ocr_ms"] += self.last_ocr_ms
        if log.isEnabledFor(logging.DEBUG):
            log.debug("OCR %s: %s, %.0f ms", tgt.key, "toàn bộ" if full else
                      " ".join(f"{r[2] - r[0]}x{r[3] - r[1]}@{r[0]},{r[1]}" for r in rects), self.last_ocr_ms)
        self._emit(st, frame)
        if self.last_ocr_ms < 120:
            self._refine_step(tgt, st, frame)

    def _refine_step(self, tgt: Target, st: _State, frame) -> bool:
        """Đọc lại ở 2× vài vùng chữ nhỏ đang chờ (trong giới hạn thời gian của lượt quét)."""
        if not st.refine or tgt.scale >= 1.5 or not self.cfg.get("refine_small_text", True):
            return False
        H, W = frame.shape[:2]
        t0 = time.monotonic()
        changed = False
        while st.refine and time.monotonic() - t0 < REFINE_TICK_S:
            r = expand_with_segments(st.refine.pop(0), st.segments, W, H)
            x0, y0, x1, y1 = r
            found = [s.offset(x0, y0) for s in self._ocr(frame[y0:y1, x0:x1], REFINE_SCALE)]
            self.stats["ocr_calls"] += 1
            self.stats["refined"] += 1
            old = [s for s in st.segments if rects_intersect(s.rect(), r)]
            if not found:  # đọc 2× không thấy gì: giữ kết quả 1×, không xếp hàng lại
                for s in old:
                    s.hi = True
                continue
            for s in found:
                s.hi = True
            st.segments = [s for s in st.segments if not rects_intersect(s.rect(), r)] + found
            st.refine = [q for q in st.refine if not rects_intersect(q, r)]
            changed = True
        self.stats["ocr_ms"] += (time.monotonic() - t0) * 1000.0
        if changed:
            self._emit(st, frame)
        return changed

    def _emit(self, st: _State, frame) -> None:
        ox, oy = st.target.rect[0], st.target.rect[1]
        style = self.cfg.get("style", "auto")
        target = self.cfg.get("target_lang", "vi")
        max_blocks = int(self.cfg.get("max_blocks", 250))
        out = []
        for b in group_segments(st.segments):
            text = b.text[:2000]
            if not should_translate(text, target=target):
                continue
            box = (b.x, b.y, b.right, b.bottom)
            fpx = b.font_px
            src_bg, src_fg = estimate_colors(frame, box)
            bold = is_bold(frame, box, src_bg, src_fg, fpx)
            if style == "auto":
                bg, fg = src_bg, src_fg
            elif style == "light":
                bg, fg = LIGHT_STYLE
            else:
                bg, fg = DARK_STYLE
            out.append(RenderBlock(text, b.x + ox, b.y + oy, b.w, b.h, b.line_h, b.n_lines, bg, fg, fpx,
                                   b.baselines(fpx)[0] + oy, b.pitch, bold))
            if len(out) >= max_blocks:
                break
        st.blocks = out
        texts = {rb.text for rb in out}
        if texts - st.last_texts:
            # có câu thật sự mới (không tính chữ rác OCR đọc nhầm từ video) => quét nhanh một lúc
            self._burst_until = time.monotonic() + self.BURST_SECONDS
            self.stats["requests"] += 1
        st.last_texts = texts
        self.translator.request([rb.text for rb in out])
        self.sink.blocks(st.target.overlay, st.target.key, list(out))
        self.sink.status({"blocks": len(out), "ocr_ms": round(self.last_ocr_ms)})
