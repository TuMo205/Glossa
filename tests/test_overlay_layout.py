"""Kiểm thử bố trí bản dịch: đúng cỡ chữ gốc, cỡ đồng đều, không giật."""

import sys

import pytest

from glossa.engine import RenderBlock
from glossa.layout import Segment, estimate_baseline, estimate_font_px, group_segments
from glossa.translator import SKIP, TranslationCache


# ---------------------------------------------------------------- ước lượng cỡ & baseline
def test_font_estimate_matches_calibration():
    # số đo thật trên Segoe UI 20px (tools/calibrate_font.py)
    assert estimate_font_px("Settings", 19.8) == pytest.approx(20, rel=0.03)
    assert estimate_font_px("This window", 15.5) == pytest.approx(20, rel=0.03)
    assert estimate_font_px("summer cocoa", 10.2) == pytest.approx(20, rel=0.03)
    assert estimate_font_px("HELLO WORLD", 14.5) == pytest.approx(20, rel=0.03)
    assert estimate_font_px("你好世界", 19.5, 19.5) == pytest.approx(20, rel=0.03)
    # baseline: đáy khung trừ phần nét xuống
    assert estimate_baseline("Settings", 100.0, 20) == pytest.approx(100 - 4.7, abs=0.2)
    assert estimate_baseline("This window", 100.0, 20) == pytest.approx(100, abs=0.5)


def test_block_baseline_and_pitch():
    # dòng 1 có nét xuống (p, g): khung cao ~0.99 cỡ; dòng 2 không có: khung ~0.775 cỡ
    segs = [Segment("This paragraph wraps onto a second line of text", 10, 100, 400, 15.5, 15.5),
            Segment("and continues here with more words", 10, 126, 300, 12.0, 12.0)]
    (b,) = group_segments(segs)
    assert b.n_lines == 2
    assert b.font_px == pytest.approx(15.6, abs=0.3)
    assert b.pitch == pytest.approx(26, abs=0.5)
    assert b.baselines()[0] == pytest.approx(115.5 - 0.235 * 15.6, abs=0.3)


# ---------------------------------------------------------------- gom cỡ chữ
def test_unify_sizes_groups_only_measurement_noise():
    from glossa.textlayout import unify_sizes

    out = unify_sizes([15.6, 16.2, 16.4, 24.0, 23.1, 12.0])
    assert out[0] == out[1] == out[2] == 16          # cùng kiểu chữ, chỉ lệch do sai số đo
    assert out[3] == out[4] in (23, 24)
    assert out[5] == 12
    assert unify_sizes([15.0, 15.7], [100, 1]) == [15, 15]   # đoạn văn dài quyết định cỡ chung
    assert unify_sizes([15.0, 17.0]) == [15, 17]             # lệch > 6%: hai cỡ khác nhau thật
    assert unify_sizes([14.1, 16.2], [1, 100]) == [14, 16]   # nhãn 14px không bị đoạn văn 16px kéo theo


# ---------------------------------------------------------------- đặt chữ
def test_place_text_keeps_original_size_and_baseline(qapp):
    from PySide6.QtCore import QRectF

    from glossa.textlayout import place_text

    bounds = QRectF(0, 0, 1200, 800)
    # nhãn một dòng: bản dịch dài hơn => nới sang phải, KHÔNG thu nhỏ
    pl = place_text("Quá trình tải xuống của bạn đã hoàn tất", 100, 200, 120, 15, 212.0, 0, 1, 16,
                    "Segoe UI", (0, 1200), bounds)
    assert pl.font.pixelSize() == 16
    assert len(pl.lines) == 1 and pl.lines[0][1] == 212.0 and pl.lines[0][0] == 100
    assert pl.rect.width() > 120 and pl.rect.top() <= 200 and pl.rect.bottom() >= 215
    # chạm mép màn hình => xuống dòng, cỡ vẫn giữ nguyên
    narrow = QRectF(0, 0, 240, 800)
    pl = place_text("Quá trình tải xuống của bạn đã hoàn tất", 100, 200, 120, 15, 212.0, 0, 1, 16,
                    "Segoe UI", (100, 240), narrow)
    assert pl.font.pixelSize() == 16 and len(pl.lines) >= 2 and pl.rect.right() <= 240
    # bản dịch đứng trước trên cùng hàng => bắt đầu sau nó, nền không che nó
    pl = place_text("Biên tập", 60, 10, 30, 14, 21.0, 0, 1, 14, "Segoe UI", (0, 1200), bounds, min_left=80)
    assert pl.rect.left() >= 80 and pl.lines[0][0] > 80
    # đoạn văn nhiều dòng: theo đúng khoảng cách dòng gốc
    pl = place_text("Đây là một đoạn văn khá dài cần xuống dòng nhiều lần để vừa với khung",
                    50, 300, 260, 40, 312.0, 24.0, 2, 15, "Segoe UI", (50, 1200), bounds)
    assert pl.font.pixelSize() == 15
    ys = [y for _x, y, _t in pl.lines]
    assert ys[0] == 312.0 and all(abs((b - a) - 24.0) < 1e-6 for a, b in zip(ys, ys[1:]))
    # cạnh khung bám lưới pixel
    r = pl.rect
    assert r.left() == int(r.left()) and r.top() == int(r.top())


def test_horizontal_room_stops_at_neighbors():
    from glossa.textlayout import horizontal_room

    rooms = horizontal_room([(10, 10, 40, 16), (120, 12, 40, 16), (10, 60, 40, 16)], 0, 1000)
    assert rooms[0] == (0, 116)       # bị chặn bởi khối bên phải cùng hàng
    assert rooms[1][0] == 54          # bị chặn bên trái
    assert rooms[2] == (0, 1000)      # hàng khác: không bị chặn


# ---------------------------------------------------------------- chống giật trên lớp phủ
class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def _overlay(qapp, cache):
    from glossa.overlay import OverlayWindow

    ov = OverlayWindow(qapp.primaryScreen(), "s", _Cfg(), cache)
    ov.resize(1600, 900)
    ov.native, ov.dpr = (0, 0, 1600, 900), 1.0
    return ov


def _rb(text, x, y, w, h, px=16.0, bg=(255, 255, 255), fg=(20, 20, 20)):
    return RenderBlock(text, x, y, w, h, h, 1, bg, fg, px, y + h - 0.2 * px, 0.0)


def test_overlay_is_stable_under_ocr_jitter(qapp):
    cache = TranslationCache()
    cache.put("Your download is complete", "Đã tải xong")
    cache.put("Settings", "Cài đặt")
    cache.put("Account settings", "Cài đặt tài khoản")
    ov = _overlay(qapp, cache)
    ov.set_blocks("t", [_rb("Your download is complete", 100, 100, 220, 16, 16.2),
                        _rb("Settings", 100, 140, 70, 16, 15.8),
                        _rb("Account settings", 100, 180, 140, 16, 16.5)])
    first = [(it.rect.getRect(), it.tr, it.px) for it in ov._items]
    assert len(first) == 3
    assert len({px for _r, _t, px in first}) == 1, "các dòng cùng cỡ phải dùng chung một cỡ chữ"
    # OCR lần sau lệch 1px, đọc sai 1 ký tự, cỡ ước lượng dao động, màu nền lệch nhẹ
    ov.set_blocks("t", [_rb("Your download is cornplete", 101, 99, 221, 17, 16.9, bg=(250, 252, 255)),
                        _rb("Settings", 100, 141, 70, 16, 15.0),
                        _rb("Account settings", 99, 180, 141, 15, 17.1)])
    second = [(it.rect.getRect(), it.tr, it.px) for it in ov._items]
    assert second == first, "rung do OCR không được làm bản dịch nhảy"
    assert ov._items[0].bg == (255, 255, 255)


def test_overlay_moves_with_real_scroll_and_holds_pending_translation(qapp):
    cache = TranslationCache()
    cache.put("New message from Anna", "Tin nhắn mới từ Anna")
    ov = _overlay(qapp, cache)
    ov.set_blocks("t", [_rb("New message from Anna", 100, 300, 200, 16)])
    y0 = ov._items[0].rect.top()
    ov.set_blocks("t", [_rb("New message from Anna", 100, 240, 200, 16)])   # cuộn 60px
    assert ov._items[0].rect.top() == pytest.approx(y0 - 60, abs=1)
    # chữ đổi nhẹ, bản dịch mới chưa về => tạm giữ bản cũ thay vì biến mất
    ov.set_blocks("t", [_rb("New messages from Anna", 100, 240, 205, 16)])
    assert ov.item_count == 1 and ov._items[0].tr == "Tin nhắn mới từ Anna" and ov._items[0].held
    cache.put("New messages from Anna", "Các tin nhắn mới từ Anna")
    ov.relayout()
    assert ov._items[0].tr == "Các tin nhắn mới từ Anna"
    # số liệu đổi => không được giữ bản dịch cũ coi như "cùng câu"
    cache.put("3 new messages", "3 tin nhắn mới")
    ov.set_blocks("t", [_rb("3 new messages", 100, 400, 130, 16)])
    ov.set_blocks("t", [_rb("4 new messages", 100, 400, 130, 16)])
    assert ov._items[0].held  # chỉ tạm giữ trong lúc chờ dịch
    cache.put("4 new messages", "4 tin nhắn mới")
    ov.relayout()
    assert ov._items[0].tr == "4 tin nhắn mới"


def test_overlay_skips_vietnamese_and_fades_in_only_new_items(qapp):
    cache = TranslationCache()
    cache.put("Hello world", "Xin chào thế giới")
    cache.put("Xin chào", SKIP)
    ov = _overlay(qapp, cache)
    ov.set_blocks("t", [_rb("Hello world", 10, 10, 90, 16), _rb("Xin chào", 10, 40, 60, 16)])
    assert ov.item_count == 1
    born = ov._items[0].born
    ov.set_blocks("t", [_rb("Hello world", 10, 10, 90, 16)])
    assert ov._items[0].born == born, "khối đang hiện không được hiện mờ lại"


def test_overlay_ignores_ocr_box_height_drift_for_unchanged_text(qapp):
    # vùng cắt OCR khác => khung chữ của CÙNG dòng chữ cao hơn 2-3px nhiều lần liên tiếp
    cache = TranslationCache()
    cache.put("Delete the file", "Xóa tập tin")
    ov = _overlay(qapp, cache)
    ov.set_blocks("t", [_rb("Delete the file", 40, 600, 110, 14, 17.6)])
    first = (ov._items[0].rect.getRect(), ov._items[0].px)
    for h, px in [(16, 20.0), (17, 21.0), (18, 21.5), (15, 18.5)]:
        ov.set_blocks("t", [_rb("Delete the file", 40, 600 - (h - 14) / 2, 111, h, px)])
        assert (ov._items[0].rect.getRect(), ov._items[0].px) == first


def test_menu_items_flow_instead_of_overlapping(qapp):
    cache = TranslationCache()
    for a, b in [("File", "Tài liệu"), ("Edit", "Biên tập"), ("View", "Xem"), ("Window", "Cửa sổ")]:
        cache.put(a, b)
    ov = _overlay(qapp, cache)
    ov.set_blocks("t", [_rb("File", 14, 10, 24, 12, 14), _rb("Edit", 74, 10, 26, 12, 14),
                        _rb("View", 134, 10, 30, 12, 14), _rb("Window", 194, 10, 50, 12, 14)])
    rects = sorted((it.rect for it in ov._items), key=lambda r: r.left())
    assert len(rects) == 4
    for a, b in zip(rects, rects[1:]):
        assert a.right() <= b.left(), "các mục menu đã dịch không được đè lên nhau"
    assert len({it.px for it in ov._items}) == 1


def test_bold_detection(qapp):
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    from glossa.colors import is_bold, stroke_ratio

    def render(bold):
        img = QImage(600, 60, QImage.Format.Format_RGB32)
        img.fill(QColor(255, 255, 255))
        p = QPainter(img)
        f = QFont("Segoe UI" if sys.platform == "win32" else "DejaVu Sans")
        f.setPixelSize(32)
        f.setBold(bold)
        p.setFont(f)
        p.setPen(QColor(20, 20, 20))
        p.drawText(QPointF(10, 44), "Welcome to the screen")
        p.end()
        a = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * 60).reshape(60, -1, 4)
        return a[:, :600].copy()

    reg, bold = render(False), render(True)
    box, bg, fg = (8, 10, 420, 52), (255, 255, 255), (20, 20, 20)
    assert stroke_ratio(bold, box, bg, fg, 32) > stroke_ratio(reg, box, bg, fg, 32)
    assert is_bold(bold, box, bg, fg, 32) and not is_bold(reg, box, bg, fg, 32)
    assert not is_bold(bold, box, bg, fg, 14)  # chữ nhỏ: không đoán
