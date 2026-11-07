"""OCR hai lượt: chữ nhỏ đọc lại ở 2×; giãn nhịp quét khi màn hình đứng yên lâu."""

import time

import numpy as np

from glossa.engine import IDLE_AFTER_S, REFINE_SCALE, Engine, Target, refine_rects
from glossa.layout import Segment


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class _ScaleOcr:
    """OCR giả: chữ nhỏ đọc ở 1× bị sai, ở 2× thì đúng (như đo thật trên máy)."""

    def __init__(self, scene):
        self.scene = scene  # [(chữ 1×, chữ 2×, x, y, w, h)]
        self.frame = None
        self.calls = []     # (w, h, scale)

    def recognize(self, img, scale=1.0):
        h, w = img.shape[:2]
        self.calls.append((w, h, scale))
        off = img.__array_interface__["data"][0] - self.frame.__array_interface__["data"][0]
        oy, rem = divmod(off, self.frame.strides[0])
        ox = rem // self.frame.strides[1]
        return [Segment(t2 if scale >= 1.5 else t1, x - ox, y - oy, ww, hh, hh)
                for t1, t2, x, y, ww, hh in self.scene
                if x >= ox and y >= oy and x + ww <= ox + w and y + hh <= oy + h]


class _Sink:
    def __init__(self):
        self.emitted = []

    def blocks(self, ov, key, blocks):
        self.emitted.append(sorted(b.text for b in blocks))

    def ocr_info(self, info):
        pass

    def status(self, info):
        pass


class _Tr:
    def __init__(self):
        self.requested = []

    def request(self, texts):
        self.requested.append(list(texts))


def _engine(ocr, **cfg):
    eng = Engine(_Cfg(style="dark", **cfg), _Tr(), _Sink(), ocr_factory=lambda *a: ocr,
                 capturer_factory=lambda: None)
    eng.ocr = ocr
    return eng


def test_refine_rects_groups_only_small_text():
    segs = [Segment("Small menu item", 10, 10, 100, 11, 11),        # ~14px: nhỏ
            Segment("Another small one", 10, 26, 110, 11, 11),      # sát dòng trên => gộp
            Segment("Big heading here", 10, 200, 300, 24, 24),      # ~31px: không đọc lại
            Segment("Far away small", 500, 300, 90, 11, 11)]
    rects = refine_rects(segs, 800, 600)
    assert len(rects) == 2
    assert all(r[3] <= 330 and not (r[1] <= 200 <= r[3] and r[0] <= 10 <= r[2]) for r in rects)
    hi = Segment("Small menu item", 10, 10, 100, 11, 11, hi=True)
    assert refine_rects([hi], 800, 600) == []  # đã đọc 2× rồi


def test_small_text_is_reread_at_2x_and_fixed():
    W, H = 640, 360
    f = np.full((H, W, 4), 255, np.uint8)
    f[40:51, 20:160, :3] = 0
    f[120:144, 20:400, :3] = 0
    ocr = _ScaleOcr([("Smal1 labe1 tcxt", "Small label text", 20, 40, 140, 11),
                     ("Big heading text", "Big heading text", 20, 120, 380, 24)])
    eng = _engine(ocr)
    tgt = Target("s0", "s0", (0, 0, W, H))
    ocr.frame = f
    eng._process(tgt, f)
    assert ocr.calls[0] == (W, H, 1.0), "lượt đầu đọc nhanh cả màn hình ở 1×"
    refine = [c for c in ocr.calls[1:] if c[2] == REFINE_SCALE]
    assert refine and all(c[0] < W / 2 and c[1] < H / 3 for c in refine), "chỉ đọc lại vùng chữ nhỏ"
    assert eng.sink.emitted[0] == ["Big heading text", "Smal1 labe1 tcxt"]
    assert eng.sink.emitted[-1] == ["Big heading text", "Small label text"]
    n = len(ocr.calls)
    eng._process(tgt, f.copy())  # không đổi, không còn gì chờ đọc lại => không OCR
    assert len(ocr.calls) == n and not eng.refine_pending


def test_small_changing_text_is_read_directly_at_2x():
    W, H = 640, 360
    f1 = np.full((H, W, 4), 255, np.uint8)
    f1[300:311, 500:600, :3] = 0
    ocr = _ScaleOcr([("10:4l PM", "10:41 PM", 500, 300, 100, 11)])
    eng = _engine(ocr)
    tgt = Target("s0", "s0", (0, 0, W, H))
    ocr.frame = f1
    eng._process(tgt, f1)
    f2 = f1.copy()
    f2[300:311, 560:600, :3] = 60  # đồng hồ đổi phút
    ocr.scene = [("10:42 PN", "10:42 PM", 500, 300, 100, 11)]
    ocr.frame = f2
    n = len(ocr.calls)
    eng._process(tgt, f2)
    assert len(ocr.calls) == n + 1 and ocr.calls[-1][2] == REFINE_SCALE, "đọc thẳng 2×, không cần lượt 1×"
    assert any(s.text == "10:42 PM" and s.hi for s in eng._states["s0"].segments)


def test_refinement_can_be_disabled_and_region_mode_skips_it():
    W, H = 320, 200
    f = np.full((H, W, 4), 255, np.uint8)
    f[40:51, 20:160, :3] = 0
    ocr = _ScaleOcr([("Smal1 labe1", "Small label", 20, 40, 140, 11)])
    eng = _engine(ocr, refine_small_text=False)
    ocr.frame = f
    eng._process(Target("s0", "s0", (0, 0, W, H)), f)
    assert [c[2] for c in ocr.calls] == [1.0]
    ocr2 = _ScaleOcr([("Smal1 labe1", "Small label", 20, 40, 140, 11)])
    eng2 = _engine(ocr2)
    ocr2.frame = f
    eng2._process(Target("r", "s0", (0, 0, W, H), 2.0), f)  # chế độ vùng đã phóng 2× sẵn
    assert [c[2] for c in ocr2.calls] == [2.0]


def test_idle_screen_scans_less_often():
    eng = _engine(_ScaleOcr([]), interval_ms=500)
    assert eng.next_period(0.02) == 0.5
    eng._last_change = time.monotonic() - IDLE_AFTER_S - 1
    assert abs(eng.next_period(0.02) - 0.8) < 1e-9
    eng._burst_until = time.monotonic() + 3  # có chữ mới => nhanh ngay
    assert eng.next_period(0.02) == Engine.BURST_INTERVAL
