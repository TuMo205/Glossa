"""Bản cài / bản di động: thư mục dữ liệu, lệnh khởi động, chuyển cài đặt từ bản cũ."""

import importlib
import json
import os
import subprocess
import sys

import pytest

from glossa import legacy, paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Giả lập Glossa.exe đã cài ở tmp_path/app."""
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    exe = app / "Glossa.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(app / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("GLOSSA_HOME", raising=False)

    def reload():
        return importlib.reload(paths)

    yield app, reload
    monkeypatch.undo()
    importlib.reload(paths)


def test_source_checkout_keeps_everything_next_to_app_py():
    assert not paths.FROZEN
    assert paths.APP_DIR == paths.DATA_DIR == ROOT
    exe, args = paths.launch_command()
    assert args == [os.path.join(ROOT, "app.py")]


def test_installed_exe_stores_data_in_appdata(frozen, tmp_path):
    app, reload = frozen
    p = reload()
    assert p.FROZEN and p.APP_DIR == str(app / "_internal")
    assert p.DATA_DIR == str(tmp_path / "Roaming" / "Glossa")
    assert p.launch_command() == (str(app / "Glossa.exe"), [])
    assert p.launch_command_line() == f'"{app / "Glossa.exe"}"'


def test_portable_zip_keeps_data_next_to_exe(frozen):
    app, reload = frozen
    (app / "portable.txt").write_text("x")
    assert reload().DATA_DIR == str(app / "data")


def test_data_dir_can_be_overridden(frozen, tmp_path, monkeypatch):
    _app, reload = frozen
    monkeypatch.setenv("GLOSSA_HOME", str(tmp_path / "custom"))
    assert reload().DATA_DIR == str(tmp_path / "custom")


def test_config_is_created_in_data_dir(monkeypatch, tmp_path):
    from glossa.config import Config

    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path / "new" / "Glossa"))
    cfg = Config()
    assert cfg.path == str(tmp_path / "new" / "Glossa" / "config.json")
    assert os.path.isfile(cfg.path), "lần đầu chạy phải tự tạo thư mục dữ liệu và config.json"


def _old_install(tmp_path):
    old = tmp_path / "home" / "ViTranslator"
    (old / "cache").mkdir(parents=True)
    (old / "config.json").write_text(json.dumps({"style": "dark", "target_lang": "vi"}), encoding="utf-8")
    (old / "cache" / "translations_vi.json").write_text("{}", encoding="utf-8")
    return old


def test_old_install_is_found_from_autostart_or_home(tmp_path):
    old = _old_install(tmp_path)
    cmd = f'"C:\\Py\\pythonw.exe" "{old / "app.py"}"'
    assert legacy.old_dirs(cmd, home=str(tmp_path / "nobody")) == [str(old)]
    assert legacy.old_dirs(None, home=str(tmp_path / "home")) == [str(old)]
    assert legacy.old_dirs(None, home=str(tmp_path / "nobody")) == []


def test_old_settings_are_copied_once(tmp_path):
    old = _old_install(tmp_path)
    data = tmp_path / "Roaming" / "Glossa"
    assert legacy.copy_settings(str(old), str(data))
    assert json.loads((data / "config.json").read_text(encoding="utf-8"))["style"] == "dark"
    assert (data / "cache" / "translations_vi.json").exists()
    (data / "config.json").write_text(json.dumps({"style": "light"}), encoding="utf-8")
    assert not legacy.copy_settings(str(old), str(data)), "không ghi đè cài đặt mới"
    assert json.loads((data / "config.json").read_text(encoding="utf-8"))["style"] == "light"


def test_migrate_moves_old_autostart_to_new_app(monkeypatch, tmp_path):
    old = _old_install(tmp_path)
    data = tmp_path / "Roaming" / "Glossa"
    monkeypatch.setattr(paths, "DATA_DIR", str(data))
    monkeypatch.setattr(paths, "SRC_DIR", str(tmp_path / "src"))
    calls = []
    monkeypatch.setattr(legacy.winutil, "get_run_value", lambda name: f'"py.exe" "{old / "app.py"}"')
    monkeypatch.setattr(legacy.winutil, "delete_run_value", lambda name: calls.append(("del", name)) or True)
    monkeypatch.setattr(legacy.winutil, "set_autostart", lambda on, *a: calls.append(("on", on)) or True)
    done = legacy.migrate()
    assert len(done) == 2 and (data / "config.json").exists()
    assert calls == [("del", "ViTranslator"), ("on", True)]
    assert legacy.migrate() == [], "chỉ chuyển một lần"


def test_source_checkout_never_migrates(monkeypatch):
    monkeypatch.setattr(legacy.winutil, "get_run_value", lambda name: pytest.fail("không được đụng registry"))
    assert legacy.migrate() == []


def test_check_imports_and_version(tmp_path):
    env = dict(os.environ, GLOSSA_HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "app.py"), "--version"], capture_output=True,
                       text=True, env=env, timeout=60)
    from glossa import __version__

    assert r.returncode == 0 and __version__ in r.stdout
    r = subprocess.run([sys.executable, os.path.join(ROOT, "app.py"), "--check-imports"], capture_output=True,
                       text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr   # ngoài Windows: không kiểm winrt
    assert os.path.isfile(tmp_path / "logs" / "glossa.log")
