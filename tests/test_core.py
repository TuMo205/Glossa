"""Kiểm thử phần lõi (chạy được trên mọi hệ điều hành): python -m pytest tests"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glossa import textutil as tu  # noqa: E402
from glossa.changes import ChangeDetector, expand_with_segments  # noqa: E402
from glossa.engine import Engine, Target  # noqa: E402
from glossa.layout import Segment, Word, group_segments, merge_engine_results, split_line  # noqa: E402
from glossa.translator import (  # noqa: E402
    SKIP, GoogleDictProvider, GoogleProvider, ProviderError, TranslationCache, Translator, parse_google,
    parse_google_dict)


# ---------------------------------------------------------------- textutil
def test_vietnamese_detection():
    assert tu.looks_vietnamese("Cài đặt hệ thống")
    assert tu.looks_vietnamese("Tiếng Việt")
    assert not tu.looks_vietnamese("Settings")
    assert not tu.looks_vietnamese("café crème")


# Câu tiếng Việt do Windows OCR (en-US) đọc méo - lấy từ máy thật
OCR_GARBLED_VI = ["Cåi dät dä dLfdc Idu.", "Ban c6 muön Idu thay d6i khöng?", "Höm nay trdi dep quå, di chdi khöng em?",
                  "Thöng båo: Hé thöng sé båo tri våo lüc 22 gid töi nay.", "Däng nhäp bäng tåi khoån Google",
                  "Ngddi düng dä rdi khåi nh6m.", "Tim kiém trén Facebook", "Dä xem", "Binh luån", "Xem thém"]
OCR_EUROPEAN = ["Paramétres enregistrés avec succés.", "Möchten Sie die Änderungen speichern?", "Größe ändern",
                "zQuieres guardar los cambios?", "Configuraqöes salvas com sucesso", "Näo foi possivel conectar",
                "Nguyen Van An sent you a message", "Nhå hång Phd Hä Nöi opens today", "Go to the top", "Rich text"]


def test_garbled_vietnamese_is_skipped():
    for t in OCR_GARBLED_VI:
        assert tu.looks_unaccented_vietnamese(t), t
        assert not tu.should_translate(t), t
    for t in OCR_EUROPEAN:
        assert not tu.looks_unaccented_vietnamese(t), t


@pytest.mark.parametrize("text,expected", [
    ("Hello world", True), ("OK", True), ("你好", True), ("12:45", False), ("x", False),
    ("https://example.com/a/b", False), ("C:\\Users\\me", False), ("report.pdf", False),
    ("my_variable_name", False), ("Cài đặt", False), ("{ a = b(); }", False),
    ("Visit https://example.com for more information", True),
])
def test_should_translate(text, expected):
    assert tu.should_translate(text) is expected


def test_join():
    assert tu.join_words(["你", "好", "世界"]) == "你好世界"
    assert tu.join_words(["Hello", "world"]) == "Hello world"
    assert tu.join_words(["日本", "Windows", "版"]) == "日本 Windows 版"
    assert tu.join_lines(["This is trans-", "lation test"]) == "This is translation test"
    assert tu.join_lines(["你好", "世界"]) == "你好世界"
    assert tu.same_text("Tieng Viet", "Tiếng Việt!")
    assert not tu.same_text("Hello", "Xin chào")
    assert tu.dominant_script("こんにちは世界") == "kana"
    assert tu.dominant_script("你好世界") == "han"


# ---------------------------------------------------------------- layout
def _line(words, y, h=16, x=10, char_w=8, gap=6):
    out = []
    for w in words:
        if w == "|":  # khoảng trống lớn
            x += 200
            continue
        out.append(Word(w, x, y, len(w) * char_w, h))
        x += len(w) * char_w + gap
    return out


def test_split_line_on_big_gap():
    segs = split_line(_line(["File", "|", "Edit"], 0))
    assert [s.text for s in segs] == ["File", "Edit"]
    segs = split_line(_line(["Hello", "world"], 0))
    assert [s.text for s in segs] == ["Hello world"]


def test_group_paragraph_and_menu():
    para = [
        split_line(_line("The quick brown fox jumps over the lazy dog and keeps".split(), 0))[0],
        split_line(_line("running through the forest until night falls.".split(), 20))[0],
    ]
    blocks = group_segments(para)
    assert len(blocks) == 1 and blocks[0].n_lines == 2
    assert blocks[0].text.endswith("until night falls.")

    menu = [split_line(_line([w], i * 22))[0] for i, w in enumerate(["Open", "Save", "Close"])]
    assert len(group_segments(menu)) == 3

    cols = split_line(_line(["Name", "|", "Size"], 0)) + split_line(_line(["readme", "|", "large"], 20))
    assert len(group_segments(cols)) == 4


def test_merge_engines_prefers_script():
    latin = [Segment("I.!", 10, 10, 80, 20, 20), Segment("Hello", 10, 50, 60, 20, 20)]
    zh = [Segment("你好世界", 12, 10, 78, 20, 20), Segment("Hel1o", 10, 50, 60, 20, 20)]
    merged = merge_engine_results([("latn", latin), ("zh", zh)])
    texts = sorted(s.text for s in merged)
    assert texts == ["Hello", "你好世界"]


# ---------------------------------------------------------------- changes
def test_change_detector_regions():
    det = ChangeDetector()
    a = np.full((400, 600, 4), 255, np.uint8)
    b = a.copy()
    assert not det.cell_grid(det.diff_mask(det.small(a), det.small(b))).any()
    b[100:140, 300:420, :3] = 0
    mask = det.diff_mask(det.small(a), det.small(b))
    regions = det.regions(det.cell_grid(mask), 600, 400)
    assert len(regions) == 1
    x0, y0, x1, y1 = regions[0]
    assert x0 <= 300 and y0 <= 100 and x1 >= 420 and y1 >= 140
    assert det.changed_fraction(mask, (300, 100, 420, 140)) > 0.9
    assert det.changed_fraction(mask, (0, 300, 100, 380)) == 0.0
    r = expand_with_segments((300, 100, 420, 140), [Segment("long line", 50, 105, 500, 20, 20)], 600, 400)
    assert r[0] <= 50 and r[2] >= 550


# ---------------------------------------------------------------- translator
class _Resp:
    def __init__(self, data, status=200, text=None):
        self._data, self.status_code = data, status
        self.text = text if text is not None else ""

    def json(self):
        return self._data


class _Session:
    def __init__(self, handler):
        self.handler = handler
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append(("GET", url, params))
        return self.handler("GET", url, params, kw)

    def post(self, url, params=None, **kw):
        self.calls.append(("POST", url, params))
        return self.handler("POST", url, params, kw)


FAKE_VI = {"Hello world": "Xin chào thế giới", "Settings": "Cài đặt", "Open file": "Mở tập tin",
           "Google": "Google", "Tieng Viet": "Tiếng Việt"}


def _google_handler(method, url, params, kw):
    q = dict(params)["q"]
    lines = q.split("\n")
    trans = "\n".join(FAKE_VI.get(t, "[vi]" + t) for t in lines)
    return _Resp({"sentences": [{"trans": trans, "orig": q}], "src": "en"})


def test_parse_google_formats():
    assert parse_google({"sentences": [{"trans": "Xin "}, {"trans": "chào"}], "src": "en"}) == ("Xin chào", "en")
    assert parse_google([[["Xin chào", "Hello", None]], None, "en"]) == ("Xin chào", "en")
    with pytest.raises(ProviderError):
        parse_google("oops")


def test_google_batch_and_fallback():
    g = GoogleProvider(_Session(_google_handler))
    res = g.translate(["Hello world", "Settings", "Open file"], "vi")
    assert [r[0] for r in res] == ["Xin chào thế giới", "Cài đặt", "Mở tập tin"]
    assert len(g.s.calls) == 1  # gom thành 1 yêu cầu

    def merging_handler(method, url, params, kw):  # Google gộp mất dòng => dịch lẻ
        q = dict(params)["q"]
        return _Resp({"sentences": [{"trans": FAKE_VI.get(q, q.replace("\n", " "))}], "src": "en"})

    g2 = GoogleProvider(_Session(merging_handler))
    res = g2.translate(["Hello world", "Settings"], "vi")
    assert [r[0] for r in res] == ["Xin chào thế giới", "Cài đặt"]


def test_google_dict_provider():
    def handler(method, url, params, kw):
        qs = [v for k, v in params if k == "q"]
        return _Resp([[FAKE_VI.get(q, "[vi]" + q), "vi" if q == "Xin chào" else "en"] for q in qs])

    g = GoogleDictProvider(_Session(handler))
    assert g.translate(["Settings", "Hello world", "Xin chào"], "vi") == [
        ("Cài đặt", "en"), ("Xin chào thế giới", "en"), ("[vi]Xin chào", "vi")]
    assert len(g.s.calls) == 1
    assert parse_google_dict(["Mở tệp"], 1) == [("Mở tệp", None)]
    with pytest.raises(ProviderError):
        parse_google_dict([["a", "en"]], 2)


def test_translator_postprocess_and_failover():
    tr = Translator(TranslationCache())

    class Broken:
        def translate(self, texts, target):
            raise ProviderError("down")

    class Fake:
        def translate(self, texts, target):
            return [(FAKE_VI.get(t, "[vi]" + t), "vi" if t == "Xin chào" else "en") for t in texts]

    tr.providers = {"google": Broken(), "google_gtx": Fake()}
    out = tr.translate_now(["Hello world", "Google", "Tieng Viet", "Xin chào", "你好"])
    assert out["Hello world"] == "Xin chào thế giới"
    assert out["Google"] == SKIP          # giống bản gốc
    assert out["Tieng Viet"] == SKIP      # chỉ thêm dấu
    assert out["Xin chào"] == SKIP        # đã là tiếng Việt
    assert out["你好"] == "[vi]你好"
    assert tr.active_provider == "google_gtx"


# ---------------------------------------------------------------- engine (OCR & chụp giả lập)
class _FakeOcr:
    """OCR giả: biết trước vị trí chữ trong khung hình; tính offset của ảnh cắt bằng con trỏ numpy."""

    available = ["en-US"]
    active_tags = ["en-US"]

    def __init__(self, scene):
        self.scene = scene  # [(text, x, y, w, h)]
        self.frame = None
        self.calls = []

    def recognize(self, img, scale=1.0):
        h, w = img.shape[:2]
        self.calls.append((w, h))
        base = self.frame
        off = img.__array_interface__["data"][0] - base.__array_interface__["data"][0]
        oy, rem = divmod(off, base.strides[0])
        ox = rem // base.strides[1]
        return [Segment(t, x - ox, y - oy, ww, hh, hh) for t, x, y, ww, hh in self.scene
                if x >= ox and y >= oy and x + ww <= ox + w and y + hh <= oy + h]


class _Sink:
    def __init__(self):
        self.emitted = []

    def blocks(self, ov, key, blocks):
        self.emitted.append([b.text for b in blocks])

    def ocr_info(self, info):
        pass

    def status(self, st):
        pass


class _Tr:
    def __init__(self):
        self.requested = []

    def request(self, texts):
        self.requested.append(list(texts))


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def test_engine_incremental_flow():
    W, H = 640, 360
    f1 = np.full((H, W, 4), 255, np.uint8)
    f1[40:56, 20:220, :3] = 0       # "Hello world"
    f1[200:216, 300:420, :3] = 0    # "Settings"
    f2 = f1.copy()
    f2[200:216, 300:440, :3] = 90   # "Settings" -> "Open file" (đổi pixel ngay tại dòng chữ)
    ocr = _FakeOcr([("Hello world", 20, 40, 200, 16), ("Settings", 300, 200, 120, 16)])
    sink, tr = _Sink(), _Tr()
    eng = Engine(_Cfg(style="dark", refine_small_text=False), tr, sink, ocr_factory=lambda *a: ocr,
                 capturer_factory=lambda: None)
    eng.ocr = ocr
    tgt = Target("s0", "s0", (0, 0, W, H))

    ocr.frame = f1
    eng._process(tgt, f1)            # lần đầu: OCR toàn màn hình
    assert ocr.calls[-1] == (W, H)
    assert sink.emitted[-1] == ["Hello world", "Settings"]

    n = len(ocr.calls)
    same = f1.copy()
    ocr.frame = same
    eng._process(tgt, same)          # không đổi: không OCR
    assert len(ocr.calls) == n

    ocr.scene = [("Hello world", 20, 40, 200, 16), ("Open file", 300, 200, 140, 16)]
    ocr.frame = f2
    eng._process(tgt, f2)            # đổi một chỗ nhỏ: chỉ OCR quanh đó
    w, h = ocr.calls[-1]
    assert len(ocr.calls) == n + 1
    assert w < W / 2 and h < H / 2, "phải OCR một phần nhỏ, không phải toàn màn hình"
    assert sink.emitted[-2] == ["Hello world"], "bản dịch của chỗ đã đổi phải gỡ ngay"
    assert sorted(sink.emitted[-1]) == ["Hello world", "Open file"]
    assert sorted(tr.requested[-1]) == ["Hello world", "Open file"]

    # cuộn cả trang => OCR lại toàn bộ
    f3 = np.roll(f2, 37, axis=0)
    ocr.scene = [("Hello world", 20, 77, 200, 16), ("Open file", 300, 237, 140, 16)]
    ocr.frame = f3
    eng._process(tgt, f3)
    assert ocr.calls[-1] == (W, H)
    assert sorted(sink.emitted[-1]) == ["Hello world", "Open file"]


def test_translator_sends_mixed_scripts_in_one_request():
    tr = Translator(TranslationCache())
    calls = []

    class Fake:
        def translate(self, texts, target):
            calls.append(list(texts))
            vi = {"Hello world": "Xin chào thế giới", "你好世界": "Chào thế giới", "Battery is low": "Pin yếu"}
            return [(vi[t], "en") for t in texts]

    tr.providers = {"google": Fake(), "google_gtx": Fake()}
    out = tr.translate_now(["Hello world", "你好世界", "Battery is low"])
    assert len(calls) == 1, "dịch vụ chính nhận diện từng câu => không cần chia nhóm"
    assert out["你好世界"] == "Chào thế giới" and out["Battery is low"] == "Pin yếu"


def test_gtx_groups_by_script_and_keeps_order():
    g = GoogleProvider(_Session(_google_handler))
    res = g.translate(["Hello world", "你好", "Settings"], "vi")
    assert [r[0] for r in res] == ["Xin chào thế giới", "[vi]你好", "Cài đặt"]
    assert len(g.s.calls) == 2  # một lô Latin + một lô chữ Hán


def test_engine_burst_after_new_text_and_merged_regions():
    import time as _t

    W, H = 640, 360
    f1 = np.full((H, W, 4), 255, np.uint8)
    ocr = _FakeOcr([])
    sink, tr = _Sink(), _Tr()
    eng = Engine(_Cfg(style="dark", refine_small_text=False), tr, sink, ocr_factory=lambda *a: ocr,
                 capturer_factory=lambda: None)
    eng.ocr = ocr
    tgt = Target("s0", "s0", (0, 0, W, H))
    ocr.frame = f1
    eng._process(tgt, f1)
    assert eng._burst_until < _t.monotonic(), "chưa có chữ => không quét nhanh"
    # hai dòng chữ mới xuất hiện gần nhau => OCR một lần trên vùng bao, bật quét nhanh
    f2 = f1.copy()
    f2[100:116, 40:200, :3] = 0
    f2[140:156, 40:260, :3] = 0
    ocr.scene = [("New message", 40, 100, 160, 16), ("Battery is low", 40, 140, 220, 16)]
    ocr.frame = f2
    n = len(ocr.calls)
    eng._process(tgt, f2)
    assert len(ocr.calls) == n + 1
    assert eng._burst_until > _t.monotonic()
    assert sorted(sink.emitted[-1]) == ["Battery is low", "New message"]


def test_resize_qt_matches_numpy():
    pytest.importorskip("PySide6")
    from glossa.ocr_windows import _resize_numpy, _resize_qt

    rng = np.random.default_rng(0)
    img = np.zeros((60, 90, 4), np.uint8)
    img[..., :3] = rng.integers(0, 255, (60, 90, 3))
    img[10:30, 20:70, :3] = 0  # khối tối kiểu nét chữ
    img[..., 3] = 0            # alpha = 0 như ảnh GDI: không được làm đen ảnh
    a, b = _resize_qt(img, 2.0), _resize_numpy(img, 2.0)
    assert a.shape == b.shape == (120, 180, 4)
    assert np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).mean() < 12
    assert a[..., :3].mean() > 60


def test_engine_cpu_budget_caps_scan_rate():
    import time as _t

    eng = Engine(_Cfg(interval_ms=500), _Tr(), _Sink(), ocr_factory=lambda *a: None, capturer_factory=lambda: None)
    assert eng.cpu_budget() == 0.3
    assert eng.next_period(0.03) == 0.5                      # bình thường: theo chu kỳ đã chọn
    eng._burst_until = _t.monotonic() + 5
    assert abs(eng.next_period(0.03) - Engine.BURST_INTERVAL) < 1e-9  # vừa có chữ mới: quét dày
    assert abs(eng.next_period(0.3) - 1.0) < 1e-9            # OCR nặng: giãn ra để tải <= 30% một lõi
    eng.cfg["interval_ms"] = 1200
    assert eng.cpu_budget() == 0.15
    eng.cfg["cpu_budget"] = 0.6
    assert eng.cpu_budget() == 0.6


def test_garbled_vietnamese_with_codes_is_skipped():
    # chữ Việt bị OCR tiếng Anh méo dấu, xen mã số (gặp thật trên máy người dùng)
    assert not tu.should_translate("HOI Khung • H02 Nåp • H03 C6tay dét 1 x4 • d6t 2 x4 • con trugt x4 • thanh däy x4")
    assert tu.should_translate("Battery x4 is low, please charge it")
    assert tu.should_translate("Model H02 Pro: new firmware available")


def test_debug_logging_never_records_request_urls(tmp_path, monkeypatch):
    import logging as _lg

    import app as _app

    monkeypatch.setattr(_app, "ROOT", str(tmp_path))
    _app.setup_logging("DEBUG")
    assert not _lg.getLogger("urllib3").isEnabledFor(_lg.DEBUG)
