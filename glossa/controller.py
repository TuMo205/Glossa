"""Điều phối giao diện: lớp phủ, khay hệ thống, phím tắt, luồng OCR và luồng dịch."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QActionGroup, QCursor, QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import __version__, winutil
from . import paths
from .config import DEFAULTS
from .engine import Engine, Target
from .hotkeys import HotkeyThread
from .icons import make_icon, save_ico
from .ocr_windows import lang_display_name
from .overlay import OsdWindow, OverlayWindow
from .panel import INTERVALS, STYLES, ControlPanel
from .selector import SelectionSession
from .langdialog import SCRIPT_NAMES, LanguageDialog
from .translator import PROVIDER_NAMES, TARGETS, TranslationCache, Translator

log = logging.getLogger(__name__)

# Bridge không bao giờ bị huỷ: luồng nền (OCR, dịch, phím tắt) có thể còn phát tín hiệu thêm một nhịp
# sau khi Controller đóng; phát tín hiệu từ đối tượng Qt đã huỷ làm chương trình sập (access violation).
_BRIDGES: list = []


class Bridge(QObject):
    """Tín hiệu Qt để chuyển dữ liệu từ các luồng nền về luồng giao diện."""

    blocks = Signal(str, str, object)
    ocr_info = Signal(object)
    status = Signal(object)
    translated = Signal()
    tr_error = Signal(str)
    hotkey = Signal(str)
    hide_req = Signal()
    show_req = Signal()


class Sink:
    """Đầu ra của Engine (được gọi từ luồng engine). Sau close() thì im lặng."""

    def __init__(self, bridge: Bridge):
        self.b = bridge
        self.hidden = threading.Event()
        self.closed = False

    def close(self):
        self.closed = True
        self.hidden.set()

    def blocks(self, overlay_key, target_key, blocks):
        if not self.closed:
            self.b.blocks.emit(overlay_key, target_key, blocks)

    def ocr_info(self, info):
        if not self.closed:
            self.b.ocr_info.emit(info)

    def status(self, st):
        if not self.closed:
            self.b.status.emit(st)

    def hide_for_capture(self):
        if self.closed:
            return
        self.hidden.clear()
        self.b.hide_req.emit()
        self.hidden.wait(0.6)
        time.sleep(0.045)  # chờ DWM vẽ lại khung hình

    def show_after_capture(self):
        if not self.closed:
            self.b.show_req.emit()


class Controller(QObject):
    def __init__(self, app, cfg, app_py: str):
        super().__init__()
        self.app = app
        self.cfg = cfg
        self.app_py = app_py
        self.active = not cfg.get("start_paused", False)
        self.hidden = False
        self.ocr_state = {"available": [], "active": [], "error": None}
        self.status_info: dict = {}
        self.overlays: dict = {}
        self._valid_targets: set = set()
        self._sel = None
        self._last_err_notice = 0.0
        self._connected_screens: set = set()

        self.cache = TranslationCache()
        self.lang_dialog = None
        self.cache_path = self._cache_file(cfg.get("target_lang", "vi"))
        if cfg.get("persist_cache", True):
            n = self.cache.load(self.cache_path, cfg.get("target_lang", "vi"))
            if n:
                log.info("Đã nạp %d bản dịch đã nhớ", n)
        self.bridge = b = Bridge()
        _BRIDGES.append(b)
        self._shut = False
        self.translator = Translator(self.cache, cfg.get("target_lang", "vi"), cfg.get("provider", "google"),
                                     on_update=b.translated.emit, on_error=b.tr_error.emit)
        self.sink = Sink(b)
        self.engine = Engine(cfg, self.translator, self.sink)
        self.hotkeys = HotkeyThread(self.hotkey_map(), b.hotkey.emit)
        self.panel = None
        self.osd = OsdWindow()

        b.blocks.connect(self._on_blocks)
        b.ocr_info.connect(self._on_ocr_info)
        b.status.connect(self._on_status)
        b.translated.connect(self._schedule_relayout)
        b.tr_error.connect(self._on_tr_error)
        b.hotkey.connect(self._on_hotkey)
        b.hide_req.connect(self._hide_for_capture)
        b.show_req.connect(self._show_after_capture)

        self._relayout_timer = QTimer(self, singleShot=True, interval=50, timeout=self._relayout_all)
        self._rebuild_timer = QTimer(self, singleShot=True, interval=900, timeout=self._rebuild_overlays)
        self._topmost_timer = QTimer(self, interval=3000, timeout=self._reassert)
        self._hover_timer = QTimer(self, interval=120, timeout=self._hover_tick)
        self._save_timer = QTimer(self, interval=120_000, timeout=self.save_cache)
        self._save_timer.start()

        self._build_tray()
        if not paths.FROZEN:  # biểu tượng của lối tắt trên Desktop (bản .exe đã có sẵn biểu tượng)
            try:
                save_ico(paths.app_path("glossa.ico"))
            except Exception:
                pass
        self._build_overlays()
        app.screenAdded.connect(self._rebuild_soon)
        app.screenRemoved.connect(self._rebuild_soon)

        self.translator.start()
        self.engine.set_active(self.active)
        self.engine.start()
        self.hotkeys.start()
        self._topmost_timer.start()
        if cfg.get("hover_reveal"):
            self._hover_timer.start()
        self._update_targets()
        self._refresh_ui()
        self._start_ipc()
        hk = self.hotkey_map()
        if cfg.get("show_panel_on_start", True):
            QTimer.singleShot(400, self.show_panel)
        else:
            self.tray.showMessage(
                "Glossa đang chạy ngầm",
                f"{hk.get('toggle', '')}: bật/tắt • {hk.get('panel', '')}: bảng điều khiển • "
                f"{hk.get('region', '')}: chọn vùng • {hk.get('quit', '')}: thoát",
                QSystemTrayIcon.MessageIcon.Information, 6000)
        QTimer.singleShot(2000, self._report_hotkeys)

    # ------------------------------------------------------------ lớp phủ
    def _build_overlays(self) -> None:
        all_excluded = True
        for i, s in enumerate(QGuiApplication.screens()):
            key = f"{i}:{s.name()}"
            ov = OverlayWindow(s, key, self.cfg, self.cache)
            ov.place()
            if not (self.active and not self.hidden):
                ov.hide()
            self.overlays[key] = ov
            all_excluded = all_excluded and ov.exclusion_ok
            if id(s) not in self._connected_screens:
                self._connected_screens.add(id(s))
                s.geometryChanged.connect(self._rebuild_soon)
                s.availableGeometryChanged.connect(self._rebuild_soon)
        self.engine.capture_hide = not all_excluded
        if not all_excluded:
            log.warning("Không ẩn được lớp phủ khỏi ảnh chụp - dùng chế độ ẩn tạm khi chụp (có thể nháy)")

    def _rebuild_soon(self, *_a) -> None:
        self._rebuild_timer.start()

    def _rebuild_overlays(self) -> None:
        for ov in self.overlays.values():
            ov.close()
            ov.deleteLater()
        self.overlays = {}
        self._build_overlays()
        self._update_targets()

    def _update_targets(self) -> None:
        for ov in self.overlays.values():
            ov.clear()
            ov.set_region(None)
            if self.active and not self.hidden:
                ov.ensure_shown()
            else:
                ov.hide()
        targets = []
        if self.active and self.overlays:
            region = self.cfg.get("region")
            if self.cfg.get("mode") == "region" and region and len(region) == 4:
                x, y, w, h = region
                cx, cy = x + w / 2, y + h / 2
                ov = next((o for o in self.overlays.values() if o.contains_native(cx, cy)),
                          next(iter(self.overlays.values())))
                scale = float(self.cfg.get("region_upscale", 2.0)) if w * h <= 1_300_000 else 1.0
                targets.append(Target(ov.key + "#region", ov.key, (x, y, w, h), scale))
                ov.set_region((x, y, w, h))
            else:
                for ov in self.overlays.values():
                    targets.append(Target(ov.key, ov.key, ov.native, 1.0))
        self._valid_targets = {t.key for t in targets}
        self.engine.set_targets(targets)

    def _on_blocks(self, overlay_key: str, target_key: str, blocks) -> None:
        if target_key not in self._valid_targets or not self.active:
            return
        ov = self.overlays.get(overlay_key)
        if ov is not None:
            ov.set_blocks(target_key, blocks)
        self._refresh_tooltip()

    def _schedule_relayout(self) -> None:
        if not self._relayout_timer.isActive():
            self._relayout_timer.start()

    def _relayout_all(self) -> None:
        for ov in self.overlays.values():
            ov.relayout()
        self._refresh_tooltip()

    def _reassert(self) -> None:
        for ov in self.overlays.values():
            ov.reassert_topmost()

    def _hover_tick(self) -> None:
        pos = QCursor.pos()
        for ov in self.overlays.values():
            if ov.isVisible():
                ov.check_hover(pos)

    def _hide_for_capture(self) -> None:
        for ov in self.overlays.values():
            if ov.isVisible():
                ov.setWindowOpacity(0.0)
                ov.repaint()
        self.sink.hidden.set()

    def _show_after_capture(self) -> None:
        for ov in self.overlays.values():
            if ov.isVisible():
                ov.setWindowOpacity(1.0)
                ov.repaint()

    def _osd(self, text: str) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.osd.show_text(text, screen)

    # ------------------------------------------------------------ hành động
    def set_active(self, on: bool, osd: bool = True) -> None:
        self.active = bool(on)
        self.engine.set_active(self.active)
        self._update_targets()
        self._refresh_ui()
        if osd:
            self._osd("V  Đang dịch sang tiếng Việt" if self.active else "E  Tạm dừng — đang hiện chữ gốc")

    def toggle_active(self) -> None:
        self.set_active(not self.active)

    def toggle_hidden(self) -> None:
        self.hidden = not self.hidden
        for ov in self.overlays.values():
            if self.hidden or not self.active:
                ov.hide()
            else:
                ov.ensure_shown()
        self._refresh_ui()
        self._osd("Đã ẩn bản dịch" if self.hidden else "Đã hiện bản dịch")

    def start_region_select(self) -> None:
        if self._sel is not None:
            return
        self.engine.set_active(False)
        for ov in self.overlays.values():
            ov.hide()
        self._sel = SelectionSession(QGuiApplication.screens(), self._region_chosen)

    def _region_chosen(self, rect) -> None:
        self._sel = None
        if rect:
            self.cfg.set("region", list(rect))
            self.cfg.set("mode", "region")
            self.active = True
        self.engine.set_active(self.active)
        self._update_targets()
        self._refresh_ui()
        if rect:
            hk = self.cfg.get("hotkeys", {}).get("fullscreen", "")
            self._osd(f"Đang dịch vùng đã chọn  •  {hk}: dịch toàn màn hình")

    def set_fullscreen(self) -> None:
        self.cfg.set("mode", "fullscreen")
        if not self.active:
            self.active = True
            self.engine.set_active(True)
        self._update_targets()
        self._refresh_ui()
        self._osd("Đang dịch toàn màn hình")

    def set_region_mode(self) -> None:
        """Dịch lại vùng đã chọn lần trước (không cần chọn lại)."""
        if not self.cfg.get("region"):
            self.start_region_select()
            return
        self.cfg.set("mode", "region")
        if not self.active:
            self.active = True
            self.engine.set_active(True)
        self._update_targets()
        self._refresh_ui()
        self._osd("Đang dịch vùng đã chọn")

    def set_option(self, key: str, value) -> None:
        """Đổi một tuỳ chọn từ bảng điều khiển: lưu lại và áp dụng ngay."""
        if key == "style":
            self.set_style(value)
            return
        self.cfg.set(key, value)
        if key in ("font_scale", "box_opacity", "font_family"):
            for ov in self.overlays.values():
                ov.restyle()
        elif key == "hover_reveal":
            if value:
                self._hover_timer.start()
            else:
                self._hover_timer.stop()
                for ov in self.overlays.values():
                    ov.check_hover(None)
        elif key in ("include_taskbar", "show_region_border"):
            self._update_targets()
        elif key == "persist_cache" and value:
            self.cache.dirty = True

    def hotkey_map(self) -> dict:
        hk = dict(DEFAULTS["hotkeys"])
        hk.update(self.cfg.get("hotkeys", {}) or {})
        return hk

    def set_hotkeys(self, new: dict) -> list:
        """Đổi phím tắt ngay khi đang chạy. Trả về các phím không đăng ký được."""
        self.cfg.set("hotkeys", dict(new))
        self.hotkeys.stop(join=1.5)
        self.hotkeys = HotkeyThread(self.hotkey_map(), self.bridge.hotkey.emit)
        self.hotkeys.start()
        self.hotkeys.wait_ready(2.0)
        self._build_menu()
        self._refresh_ui()
        return list(self.hotkeys.failed)

    def reset_defaults(self) -> None:
        """Đưa tuỳ chọn hiển thị/hành vi về mặc định (giữ phím tắt, vùng dịch)."""
        for key in ("box_opacity", "font_family", "font_scale", "hover_reveal", "show_region_border",
                    "include_taskbar", "refine_small_text", "persist_cache", "start_paused", "show_panel_on_start"):
            self.set_option(key, DEFAULTS[key])
        self.set_style(DEFAULTS["style"])
        self.set_interval(DEFAULTS["interval_ms"])
        self.set_provider(DEFAULTS["provider"])
        if self.cfg.get("ocr_languages") != DEFAULTS["ocr_languages"]:
            self.set_ocr_languages(DEFAULTS["ocr_languages"])
        self._osd("Đã khôi phục mặc định")

    def autostart_enabled(self) -> bool:
        return winutil.get_autostart()

    def open_help(self) -> None:
        self._open(paths.app_path("HUONG_DAN.md"))

    def open_logs(self) -> None:
        os.makedirs(paths.data_path("logs"), exist_ok=True)
        self._open(paths.data_path("logs"))

    def status_text(self) -> str:
        if not self.active:
            state = "Tạm dừng — đang hiện chữ gốc."
        elif self.hidden:
            state = "Đang ẩn bản dịch."
        else:
            n = sum(ov.item_count for ov in self.overlays.values())
            region = self.cfg.get("mode") == "region" and self.cfg.get("region")
            state = f"Đang dịch {'vùng đã chọn' if region else 'toàn màn hình'}: {n} đoạn."
        langs = ", ".join(self.ocr_state.get("active") or []) or "chưa có OCR"
        seen = [SCRIPT_NAMES.get(sc, sc) for sc in self.engine.seen_scripts()]
        err = self.translator.last_error
        tr = f"lỗi dịch ({err[:30]})" if err else PROVIDER_NAMES.get(self.translator.active_provider, "")
        return (f"{state}\nĐọc được: {langs}" + (f" • đang thấy: {', '.join(seen)}" if seen else "")
                + f"\n{tr} • nhớ {len(self.cache)} bản dịch")

    def show_panel(self) -> None:
        if self.panel is None:
            self.panel = ControlPanel(self)
        self.panel.show_panel()

    def _start_ipc(self) -> None:
        """Mở lối tắt lần nữa khi đang chạy => bản đang chạy hiện bảng điều khiển (như Unikey)."""
        name = winutil.ipc_name()
        self._ipc = QLocalServer(self)
        QLocalServer.removeServer(name)  # dọn kênh cũ nếu lần trước bị tắt đột ngột
        if not self._ipc.listen(name):
            log.warning("Không mở được kênh nội bộ: %s", self._ipc.errorString())
            return
        self._ipc.newConnection.connect(self._on_ipc)

    def _on_ipc(self) -> None:
        while self._ipc.hasPendingConnections():
            sock = self._ipc.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._on_ipc_data(s))
            sock.disconnected.connect(sock.deleteLater)

    def _on_ipc_data(self, sock) -> None:
        data = bytes(sock.readAll())
        if b"show" in data:
            self.show_panel()
        sock.disconnectFromServer()

    def set_ocr_languages(self, value) -> None:
        self.cfg.set("ocr_languages", value)
        self.engine.reload_ocr()
        for ov in self.overlays.values():
            ov.clear()

    def set_provider(self, name: str) -> None:
        self.cfg.set("provider", name)
        self.translator.set_provider(name)
        self._refresh_ui()

    def set_interval(self, ms: int) -> None:
        self.cfg.set("interval_ms", ms)
        self._refresh_ui()

    def set_style(self, style: str) -> None:
        self.cfg.set("style", style)
        self.engine.refresh()
        self._refresh_ui()

    def set_autostart(self, on: bool) -> None:
        if not winutil.set_autostart(on):
            self.tray.showMessage("Glossa", "Không đổi được chế độ khởi động cùng Windows.",
                                  QSystemTrayIcon.MessageIcon.Warning, 4000)
        self._refresh_ui()

    def reload_config(self) -> None:
        self.cfg.load()
        self.translator.set_provider(self.cfg.get("provider", "google"))
        self.engine.reload_ocr()
        if self.cfg.get("hover_reveal"):
            self._hover_timer.start()
        else:
            self._hover_timer.stop()
        self._rebuild_overlays()
        self._refresh_ui()
        self._osd("Đã tải lại cấu hình")

    def _open(self, path: str) -> None:
        try:
            if path.lower().endswith((".json", ".md", ".txt", ".log")):
                subprocess.Popen(["notepad.exe", path])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning("Không mở được %s: %s", path, e)

    def install_ocr_languages(self) -> None:
        self.show_languages()

    def show_languages(self) -> None:
        if self.lang_dialog is None:
            self.lang_dialog = LanguageDialog(self)
        self.lang_dialog.show_dialog()

    def _cache_file(self, target: str) -> str:
        return paths.data_path("cache", f"translations_{target}.json")

    def set_target(self, target: str) -> None:
        """Đổi ngôn ngữ đích (dịch sang tiếng gì)."""
        old = self.cfg.get("target_lang", "vi")
        if target == old:
            return
        self.save_cache()
        self.cfg.set("target_lang", target)
        self.translator.set_target(target)
        self.cache.clear()
        self.cache_path = self._cache_file(target)
        if self.cfg.get("persist_cache", True):
            self.cache.load(self.cache_path, target)
        for ov in self.overlays.values():
            ov.clear()
        self.engine.refresh()
        self._refresh_ui()
        self._osd("Dịch sang " + dict(TARGETS).get(target, target))

    def save_cache(self) -> None:
        if not self.cfg.get("persist_cache", True) or not self.cache.dirty:
            return
        try:
            n = self.cache.save(self.cache_path, self.cfg.get("target_lang", "vi"))
            log.debug("Đã lưu %d bản dịch", n)
        except OSError as e:
            log.warning("Không lưu được bộ nhớ bản dịch: %s", e)

    def clear_cache(self) -> None:
        self.cache.clear()
        try:
            os.remove(self.cache_path)
        except OSError:
            pass
        self._osd("Đã xoá bộ nhớ bản dịch")

    def shutdown(self) -> None:
        """Lưu bộ nhớ, dừng hẳn các luồng nền, đóng lớp phủ. Gọi nhiều lần không sao."""
        if self._shut:
            return
        self._shut = True
        self._save_timer.stop()
        self.save_cache()
        self.sink.close()
        self.translator.on_update = self.translator.on_error = None
        self.engine.stop()
        self.translator.stop()
        self.hotkeys.stop()
        for t, sec in ((self.engine, 3.0), (self.translator.thread, 1.0), (self.hotkeys, 1.5)):
            if t is not None and t.is_alive():
                t.join(sec)
                if t.is_alive():
                    log.warning("Luồng %s chưa dừng kịp", t.name)
        for ov in self.overlays.values():
            ov.close()
        self.tray.hide()

    def quit(self) -> None:
        self.shutdown()
        self.app.quit()

    # ------------------------------------------------------------ tín hiệu nền
    def _on_hotkey(self, name: str) -> None:
        if self._sel is not None and name != "quit":
            return
        {
            "toggle": self.toggle_active,
            "hide": self.toggle_hidden,
            "region": self.start_region_select,
            "fullscreen": self.set_fullscreen,
            "panel": self.show_panel,
            "quit": self.quit,
        }.get(name, lambda: None)()

    def _on_ocr_info(self, info) -> None:
        prev_err = self.ocr_state.get("error")
        self.ocr_state = info
        self._rebuild_lang_menu()
        self._refresh_ui()
        if self.panel is not None and self.panel.isVisible():
            self.panel.refresh()
        if self.lang_dialog is not None and self.lang_dialog.isVisible():
            self.lang_dialog.refresh()
        err = info.get("error")
        if err == "no_language":
            self.tray.showMessage(
                "Chưa có gói nhận dạng chữ (OCR)",
                "Windows chưa cài ngôn ngữ OCR nào. Mở Bảng điều khiển → 'Thêm tiếng…'.",
                QSystemTrayIcon.MessageIcon.Warning, 10000)
        elif err and err != prev_err:
            self.tray.showMessage("Lỗi nhận dạng chữ (OCR)", err[:200], QSystemTrayIcon.MessageIcon.Critical, 10000)

    def _on_status(self, st) -> None:
        self.status_info.update(st)
        self._refresh_tooltip()

    def _on_tr_error(self, msg: str) -> None:
        self._refresh_tooltip()
        now = time.monotonic()
        if now - self._last_err_notice > 300:
            self._last_err_notice = now
            self.tray.showMessage("Không dịch được", f"{msg[:160]}\nKiểm tra kết nối Internet.",
                                  QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _report_hotkeys(self) -> None:
        if self.hotkeys.failed:
            self.tray.showMessage("Một số phím tắt bị trùng",
                                  "Không dùng được: " + ", ".join(self.hotkeys.failed)
                                  + ". Đổi trong Bảng điều khiển → Mở rộng → Phím tắt.",
                                  QSystemTrayIcon.MessageIcon.Warning, 8000)

    # ------------------------------------------------------------ khay hệ thống
    def _build_tray(self) -> None:
        self.icon_on, self.icon_off = make_icon(True), make_icon(False)
        self.tray = QSystemTrayIcon(self.icon_on)
        self.tray.activated.connect(self._on_tray_activated)
        self.menu = None
        self._build_menu()
        self.tray.show()

    def _build_menu(self) -> None:
        """Menu khay kiểu Unikey (dựng lại được khi đổi phím tắt)."""
        hk = self.hotkey_map()
        old = self.menu
        m = self.menu = QMenu()
        panel = m.addAction(f"Bảng điều khiển…\t{hk.get('panel', '')}")
        f = panel.font()
        f.setBold(True)
        panel.setFont(f)
        panel.triggered.connect(lambda _c: self.show_panel())
        m.setDefaultAction(panel)
        m.addSeparator()
        self.act_active = m.addAction(f"Bật dịch tiếng Việt\t{hk.get('toggle', '')}")
        self.act_active.setCheckable(True)
        self.act_active.triggered.connect(lambda checked: self.set_active(checked))
        self.act_hidden = m.addAction(f"Ẩn bản dịch\t{hk.get('hide', '')}")
        self.act_hidden.setCheckable(True)
        self.act_hidden.triggered.connect(lambda _c: self.toggle_hidden())
        m.addSeparator()
        mode_group = QActionGroup(m)
        self.act_full = m.addAction(f"Dịch toàn màn hình\t{hk.get('fullscreen', '')}")
        self.act_full.setCheckable(True)
        self.act_full.setActionGroup(mode_group)
        self.act_full.triggered.connect(lambda _c: self.set_fullscreen())
        self.act_region = m.addAction(f"Chọn vùng cần dịch…\t{hk.get('region', '')}")
        self.act_region.setCheckable(True)
        self.act_region.setActionGroup(mode_group)
        self.act_region.triggered.connect(lambda _c: self.start_region_select())
        m.addSeparator()

        self.lang_menu = m.addMenu("Đọc chữ tiếng")
        self._rebuild_lang_menu()

        dm = m.addMenu("Dịch sang")
        g = QActionGroup(dm)
        self.target_actions = {}
        for code, name in TARGETS:
            a = dm.addAction(name)
            a.setCheckable(True)
            a.setActionGroup(g)
            a.triggered.connect(lambda _c, c=code: self.set_target(c))
            self.target_actions[code] = a

        im = m.addMenu("Tốc độ quét")
        g = QActionGroup(im)
        self.interval_actions = {}
        for ms, name in INTERVALS:
            a = im.addAction(name)
            a.setCheckable(True)
            a.setActionGroup(g)
            a.triggered.connect(lambda _c, v=ms: self.set_interval(v))
            self.interval_actions[ms] = a

        sm = m.addMenu("Màu nền bản dịch")
        g = QActionGroup(sm)
        self.style_actions = {}
        for key, name in STYLES:
            a = sm.addAction(name)
            a.setCheckable(True)
            a.setActionGroup(g)
            a.triggered.connect(lambda _c, k=key: self.set_style(k))
            self.style_actions[key] = a

        tm = m.addMenu("Công cụ")
        tm.addAction("Xoá bộ nhớ bản dịch").triggered.connect(self.clear_cache)
        tm.addAction("Ngôn ngữ nhận dạng (cài thêm tiếng)…").triggered.connect(self.show_languages)
        tm.addAction("Mở file cấu hình (config.json)").triggered.connect(lambda: self._open(self.cfg.path))
        tm.addAction("Tải lại cấu hình").triggered.connect(self.reload_config)
        tm.addAction("Mở thư mục nhật ký (log)").triggered.connect(self.open_logs)
        m.addSeparator()

        self.act_autostart = m.addAction("Khởi động cùng Windows")
        self.act_autostart.setCheckable(True)
        self.act_autostart.triggered.connect(lambda checked: self.set_autostart(checked))
        m.addAction("Hướng dẫn sử dụng").triggered.connect(self.open_help)
        m.addAction(f"Thông tin (Glossa {__version__})").triggered.connect(self._about)
        m.addSeparator()
        m.addAction(f"Kết thúc\t{hk.get('quit', '')}").triggered.connect(self.quit)
        m.aboutToShow.connect(self._refresh_ui)
        self.tray.setContextMenu(m)
        if old is not None:
            old.deleteLater()

    def _about(self) -> None:
        self.show_panel()
        self.panel.show_about()

    def _rebuild_lang_menu(self) -> None:
        lm = self.lang_menu
        lm.clear()
        g = QActionGroup(lm)
        current = self.cfg.get("ocr_languages", "auto")
        active = self.ocr_state.get("active") or []
        label = "Tự động (nhiều ngôn ngữ)"
        if active and current == "auto":
            label += ": " + ", ".join(active)
        a = lm.addAction(label)
        a.setCheckable(True)
        a.setActionGroup(g)
        a.setChecked(current == "auto")
        a.triggered.connect(lambda _c: self.set_ocr_languages("auto"))
        for tag in self.ocr_state.get("available") or []:
            a = lm.addAction(lang_display_name(tag))
            a.setCheckable(True)
            a.setActionGroup(g)
            a.setChecked(isinstance(current, list) and current == [tag])
            a.triggered.connect(lambda _c, t=tag: self.set_ocr_languages([t]))
        lm.addSeparator()
        lm.addAction("Thêm tiếng khác (cài gói nhận dạng)…").triggered.connect(self.show_languages)
        lm.addAction("Làm mới danh sách ngôn ngữ").triggered.connect(self.engine.reload_ocr)

    def _on_tray_activated(self, reason) -> None:
        R = QSystemTrayIcon.ActivationReason
        if reason == R.Trigger:          # nhấp 1 lần: chuyển V <-> E như Unikey
            self._toggled_by_click = time.monotonic()
            self.toggle_active()
        elif reason == R.DoubleClick:    # nhấp đúp: mở bảng điều khiển (bỏ lần chuyển của cú nhấp đầu)
            if time.monotonic() - getattr(self, "_toggled_by_click", 0.0) < 1.0:
                self.set_active(not self.active, osd=False)
            self.show_panel()
        elif reason == R.MiddleClick:
            self.toggle_hidden()

    def _refresh_ui(self) -> None:
        self.tray.setIcon(self.icon_on if self.active else self.icon_off)
        self.act_active.setChecked(self.active)
        self.act_hidden.setChecked(self.hidden)
        region_mode = self.cfg.get("mode") == "region" and bool(self.cfg.get("region"))
        self.act_region.setChecked(region_mode)
        self.act_full.setChecked(not region_mode)
        iv = int(self.cfg.get("interval_ms", 500))
        for ms, a in self.interval_actions.items():
            a.setChecked(ms == iv)
        for k, a in self.style_actions.items():
            a.setChecked(k == self.cfg.get("style", "auto"))
        for k, a in self.target_actions.items():
            a.setChecked(k == self.cfg.get("target_lang", "vi"))
        self.act_autostart.setChecked(winutil.get_autostart())
        self._refresh_tooltip()
        if self.panel is not None and self.panel.isVisible():
            self.panel.refresh()

    def _refresh_tooltip(self) -> None:
        if not self.active:
            state = "Tạm dừng"
        elif self.hidden:
            state = "Đang ẩn bản dịch"
        else:
            n = sum(ov.item_count for ov in self.overlays.values())
            state = f"Đang dịch ({n} đoạn)"
        langs = ", ".join(self.ocr_state.get("active") or []) or "chưa có OCR"
        err = self.translator.last_error
        tip = f"Glossa — {state}\nOCR: {langs}\n"
        tip += f"Lỗi dịch: {err[:40]}" if err else f"Dịch: {PROVIDER_NAMES.get(self.translator.active_provider, '')}"
        self.tray.setToolTip(tip[:120])
