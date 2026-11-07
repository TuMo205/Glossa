"""Bảng điều khiển kiểu Unikey + khay hệ thống (chạy controller thật ở chế độ không màn hình)."""

import os
import time

import pytest


@pytest.fixture
def ctl(qapp, tmp_path, monkeypatch):
    import glossa.controller as ctlmod
    from glossa.config import Config

    monkeypatch.setattr(ctlmod.paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ctlmod.winutil, "ipc_name", lambda: f"Glossa.Test.{os.getpid()}.{time.time_ns()}")
    cfg = Config(str(tmp_path / "config.json"))
    cfg.set("show_panel_on_start", False)
    c = ctlmod.Controller(qapp, cfg, "app.py")
    yield c
    c.quit()


def _pump(qapp, sec=0.2):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_panel_changes_apply_immediately_and_persist(ctl, qapp):
    ctl.show_panel()
    p = ctl.panel
    p.sp_scale.setValue(115)
    p.sl_opacity.setValue(80)
    p.checks["hover_reveal"].setChecked(True)
    p.cb_speed.setCurrentIndex(p.cb_speed.findData(1200))
    p.cb_style.setCurrentIndex(p.cb_style.findData("dark"))
    assert (ctl.cfg.get("font_scale"), ctl.cfg.get("box_opacity")) == (1.15, 0.8)
    assert ctl.cfg.get("hover_reveal") and ctl._hover_timer.isActive()
    assert ctl.cfg.get("interval_ms") == 1200 and ctl.cfg.get("style") == "dark"
    from glossa.config import Config

    saved = Config(ctl.cfg.path)  # đã ghi xuống config.json
    assert saved.get("font_scale") == 1.15 and saved.get("style") == "dark"
    ctl.reset_defaults()
    p.refresh()
    assert p.sp_scale.value() == 100 and not p.checks["hover_reveal"].isChecked()
    assert ctl.cfg.get("interval_ms") == 500 and not ctl._hover_timer.isActive()


def test_toggle_button_close_and_expand(ctl, qapp):
    ctl.show_panel()
    p = ctl.panel
    assert p.isVisible() and p.bt_toggle.text().startswith("V")
    p.bt_toggle.click()
    assert not ctl.active and p.bt_toggle.text().startswith("E")
    p.bt_toggle.click()
    assert ctl.active
    expanded = p.ext.isVisible()
    p.toggle_expanded()
    assert p.ext.isVisible() != expanded and ctl.cfg.get("panel_expanded") == p.ext.isVisible()
    p.close()                         # nút X = Đóng: chỉ ẩn bảng, chương trình vẫn chạy
    assert not p.isVisible() and ctl.active


def test_hotkeys_can_be_changed_live(ctl, qapp):
    from PySide6.QtGui import QKeySequence

    ctl.show_panel()
    p = ctl.panel
    p.key_edits["panel"].setKeySequence(QKeySequence("Ctrl+Alt+K"))
    p._on_hotkeys()
    assert ctl.hotkey_map()["panel"] == "Ctrl+Alt+K"
    assert "Ctrl+Alt+K" in ctl.menu.actions()[0].text()          # menu khay hiện phím mới
    p.key_edits["hide"].setKeySequence(QKeySequence("Ctrl+Alt+K"))
    p._on_hotkeys()
    assert "trùng" in p.lb_keys.text() and ctl.hotkey_map()["hide"] == "Ctrl+Alt+H"


def test_tray_click_toggles_and_double_click_opens_panel(ctl, qapp):
    from PySide6.QtWidgets import QSystemTrayIcon

    R = QSystemTrayIcon.ActivationReason
    ctl._on_tray_activated(R.Trigger)
    assert not ctl.active                       # nhấp 1 lần: V -> E
    ctl._on_tray_activated(R.Trigger)
    assert ctl.active                           # E -> V
    ctl._on_tray_activated(R.Trigger)           # nhấp đúp = Trigger rồi DoubleClick
    ctl._on_tray_activated(R.DoubleClick)
    assert ctl.active and ctl.panel is not None and ctl.panel.isVisible()


def test_second_launch_shows_panel_of_running_instance(ctl, qapp):
    from PySide6.QtNetwork import QLocalSocket

    assert ctl.panel is None or not ctl.panel.isVisible()
    s = QLocalSocket()
    s.connectToServer(ctl._ipc.serverName())
    assert s.waitForConnected(1000)
    s.write(b"show\n")
    s.flush()
    for _ in range(50):
        _pump(qapp, 0.02)
        if ctl.panel is not None and ctl.panel.isVisible():
            break
    assert ctl.panel is not None and ctl.panel.isVisible()


def test_tray_menu_is_unikey_like(ctl):
    texts = [a.text().split("\t")[0] for a in ctl.menu.actions() if not a.isSeparator()]
    assert texts[0] == "Bảng điều khiển…" and ctl.menu.defaultAction().text().startswith("Bảng điều khiển")
    assert texts[-1] == "Kết thúc"
    assert "Công cụ" in texts and "Bật dịch tiếng Việt" in texts
