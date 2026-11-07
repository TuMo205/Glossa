"""Bộ nhớ bản dịch lưu xuống đĩa."""

import json

from glossa.translator import SKIP, TranslationCache, persistable


def test_save_and_load_round_trip(tmp_path):
    path = str(tmp_path / "cache" / "translations_vi.json")
    c = TranslationCache()
    c.put("Settings", "Cài đặt")
    c.put("Xin chào", SKIP)
    assert c.dirty
    assert c.save(path) == 2 and not c.dirty
    c2 = TranslationCache()
    assert c2.load(path) == 2
    assert c2.get("Settings") == "Cài đặt" and c2.get("Xin chào") == SKIP


def test_private_and_long_texts_are_never_written(tmp_path):
    path = str(tmp_path / "t.json")
    c = TranslationCache()
    c.put("Open the folder", "Mở thư mục")
    c.put("Call me at 0912 345 678 tonight", "Gọi cho tôi")
    c.put("Email anna@example.com for details", "Gửi email")
    c.put("See https://example.com/reset?token=abc", "Xem")
    c.put("Your OTP code is 482913", "Mã OTP của bạn")
    c.put("Hi Minh, the meeting moved to Friday afternoon because the client asked us to wait", "Chào Minh...")
    c.save(path)
    with open(path, encoding="utf-8") as f:
        saved = {k for k, _v in json.load(f)["items"]}
    assert saved == {"Open the folder"}
    assert persistable("Version 2", "Phiên bản 2")       # số ngắn thì được
    assert not persistable("Invoice 2026-11-06", "Hoá đơn")


def test_load_ignores_missing_corrupt_or_other_language(tmp_path):
    c = TranslationCache()
    assert c.load(str(tmp_path / "missing.json")) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert c.load(str(bad)) == 0
    other = tmp_path / "en.json"
    other.write_text(json.dumps({"version": 1, "target": "en", "items": [["Cài đặt", "Settings"]]}), encoding="utf-8")
    assert c.load(str(other), "vi") == 0
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"version": 1, "target": "vi", "items": [["a", 1], "x", ["Help", "Trợ giúp"]]}),
                     encoding="utf-8")
    assert c.load(str(weird)) == 1 and c.get("Help") == "Trợ giúp"


def test_loaded_entries_do_not_override_fresh_ones(tmp_path):
    path = str(tmp_path / "t.json")
    old = TranslationCache()
    old.put("Help", "Giúp đỡ")
    old.save(path)
    c = TranslationCache()
    c.put("Help", "Trợ giúp")
    c.load(path)
    assert c.get("Help") == "Trợ giúp"
