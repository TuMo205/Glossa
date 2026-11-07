"""Đóng gói Glossa cho Windows: Glossa.exe (PyInstaller) -> bộ cài Setup.exe (Inno Setup) + bản di động .zip.

Trên máy Windows, trong thư mục mã nguồn:
    python -m pip install -r requirements.txt pyinstaller
    python packaging\\build.py                 (làm tất cả các bước)
    python packaging\\build.py exe smoke       (chỉ vài bước)

Các bước: test, icon, exe, smoke, installer, portable, checksums  (python packaging\\build.py version: in số phiên bản).
Kết quả ở thư mục dist\\, nhật ký từng bước ở ci-logs\\ (máy chủ build GitHub đẩy lên nhánh ci-logs).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
LOGS = os.path.join(ROOT, "ci-logs")
APP_ID = "Glossa"
STEPS = ["test", "icon", "exe", "smoke", "installer", "portable", "checksums"]
ISL_URL = "https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/Vietnamese.isl"


def version() -> str:
    with open(os.path.join(ROOT, "glossa", "__init__.py"), encoding="utf-8") as f:
        return re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)


VERSION = version()
APP_DIR = os.path.join(DIST, APP_ID)
EXE = os.path.join(APP_DIR, APP_ID + ".exe")


class StepError(RuntimeError):
    pass


def say(log: str, msg: str) -> None:
    print(msg, flush=True)
    with open(os.path.join(LOGS, log), "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run(cmd: list, log: str, check: bool = True, env=None, timeout: float = 1800) -> int:
    """Chạy lệnh, vừa in ra vừa ghi vào ci-logs/<log>."""
    say(log, "> " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    with open(os.path.join(LOGS, log), "a", encoding="utf-8") as lf:
        p = subprocess.Popen([str(c) for c in cmd], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        t0 = time.time()
        for line in p.stdout:
            sys.stdout.write(line)
            lf.write(line)
            if time.time() - t0 > timeout:
                p.kill()
                break
        rc = p.wait()
    if check and rc != 0:
        raise StepError(f"{os.path.basename(str(cmd[0]))} trả về mã lỗi {rc}")
    return rc


# --- các bước -------------------------------------------------------------------------------------

def step_test() -> None:
    """Chạy bộ kiểm thử. Lỗi kiểm thử chỉ ghi lại (bản .exe còn được kiểm riêng ở bước smoke)."""
    try:
        import pytest  # noqa: F401
    except ImportError:
        say("test.txt", "Bỏ qua: chưa cài pytest (python -m pip install pytest)")
        return
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    rc = run([sys.executable, "-m", "pytest", "-v", "-rf", "--tb=short", "-p", "no:cacheprovider"], "test.txt",
             check=False, env=env, timeout=900)
    if rc != 0:
        say("summary.txt", f"CẢNH BÁO: có kiểm thử lỗi (mã {rc}) - xem test.txt")
        with open(os.path.join(LOGS, "TESTS_FAILED"), "w", encoding="utf-8") as f:
            f.write(str(rc))

def step_icon() -> None:
    """build/glossa.ico - biểu tượng chữ V đỏ (vẽ bằng Qt, giống biểu tượng ở khay hệ thống)."""
    os.makedirs(BUILD, exist_ok=True)
    env = dict(os.environ)
    if sys.platform != "win32":
        env["QT_QPA_PLATFORM"] = "offscreen"
    code = ("import sys; sys.path.insert(0, r'%s'); from PySide6.QtGui import QGuiApplication; "
            "app = QGuiApplication([]); from glossa.icons import save_ico; "
            "sys.exit(0 if save_ico(r'%s') else 1)") % (ROOT, os.path.join(BUILD, "glossa.ico"))
    run([sys.executable, "-c", code], "icon.txt", env=env)
    say("icon.txt", f"OK: {os.path.join(BUILD, 'glossa.ico')} ({os.path.getsize(os.path.join(BUILD, 'glossa.ico'))} byte)")


def step_exe() -> None:
    if not os.path.exists(os.path.join(BUILD, "glossa.ico")):
        step_icon()
    shutil.rmtree(APP_DIR, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"),
         os.path.join(HERE, "Glossa.spec")], "exe.txt")
    if not os.path.exists(EXE if sys.platform == "win32" else EXE[:-4]):
        raise StepError("PyInstaller không tạo ra Glossa.exe")
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(APP_DIR) for f in fs)
    say("exe.txt", f"OK: {APP_DIR} ({size / 1e6:.0f} MB)")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def step_smoke() -> None:
    """Chạy thử bản .exe: đủ thành phần, đọc được OCR, mở được giao diện mà không bị tắt đột ngột."""
    exe = EXE if sys.platform == "win32" else EXE[:-4]
    home = os.path.join(BUILD, "smoke-home")
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home)
    env = dict(os.environ, GLOSSA_HOME=home)
    app_log = os.path.join(home, "logs", "glossa.log")
    log = "smoke.txt"

    rc = subprocess.run([exe, "--check-imports"], env=env, timeout=180).returncode
    say(log, f"--check-imports: mã {rc}")
    say(log, _read(app_log))
    if rc != 0:
        raise StepError("bản .exe thiếu thành phần (xem dòng check-imports ở trên)")

    rc = subprocess.run([exe, "--check-ocr"], env=env, timeout=180).returncode
    say(log, f"--check-ocr: mã {rc} (0 = có OCR, 2 = máy chưa cài gói OCR, 3 = không gọi được Windows OCR)")
    if rc == 3:
        say(log, "CẢNH BÁO: không gọi được Windows OCR trên máy build")

    # mở giao diện thật (OCR, dịch, lớp phủ) 15 giây rồi tự thoát như khi bấm Kết thúc
    p = subprocess.Popen([exe], env=dict(env, GLOSSA_AUTOQUIT_S="15"))
    try:
        rc = p.wait(60)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = None
    text = _read(app_log)
    say(log, f"chạy giao diện 15 giây: mã thoát {rc}")
    say(log, "---- nhật ký khi chạy thử ----\n" + text[-6000:])
    if "Khởi động Glossa" not in text:
        raise StepError("Glossa.exe không ghi nhật ký khởi động")
    if rc is None:
        raise StepError("Glossa.exe không tự thoát được (treo khi đóng)")
    if rc != 0 or "Thoát (0)" not in text:
        raise StepError(f"Glossa.exe thoát bất thường (mã {rc}, {rc & 0xFFFFFFFF:#x})" if rc else
                        "Glossa.exe thoát mà không qua bước đóng chương trình")
    if "CRITICAL" in text:
        say(log, "CẢNH BÁO: có lỗi CRITICAL trong nhật ký (xem trên)")
    say(log, "OK: chạy thử thành công")


def find_iscc() -> str | None:
    cands = [os.environ.get("ISCC", ""), shutil.which("iscc") or "", shutil.which("ISCC") or ""]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            cands.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    return next((c for c in cands if c and os.path.isfile(c)), None)


def step_installer() -> None:
    iscc = find_iscc()
    if not iscc:
        raise StepError("chưa có Inno Setup 6 (https://jrsoftware.org/isdl.php hoặc: choco install innosetup)")
    isl = os.path.join(HERE, "Vietnamese.isl")
    if not os.path.exists(isl):
        try:
            urllib.request.urlretrieve(ISL_URL, isl)
        except Exception as e:
            say("installer.txt", f"CẢNH BÁO: không tải được Vietnamese.isl ({e}) - bộ cài sẽ dùng tiếng Anh")
    run([iscc, f"/DAppVersion={VERSION}", "/Qp", os.path.join(HERE, "Glossa.iss")], "installer.txt")
    out = os.path.join(DIST, f"{APP_ID}-Setup-{VERSION}.exe")
    if not os.path.exists(out):
        raise StepError("Inno Setup không tạo ra bộ cài")
    say("installer.txt", f"OK: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


PORTABLE_NOTE = """Glossa - bản di động (không cần cài đặt)

Chạy Glossa.exe. Cài đặt, nhật ký và bộ nhớ bản dịch được lưu trong thư mục "data" ngay cạnh file này,
nên có thể chép cả thư mục sang USB / máy khác. Xoá file portable.txt này nếu muốn lưu dữ liệu vào
%APPDATA%\\Glossa như bản cài đặt.
"""


def step_portable() -> None:
    if not os.path.isdir(APP_DIR):
        raise StepError("chưa có dist/Glossa - chạy bước exe trước")
    out = os.path.join(DIST, f"{APP_ID}-Portable-{VERSION}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d, _dirs, files in os.walk(APP_DIR):
            for f in files:
                full = os.path.join(d, f)
                z.write(full, os.path.join(APP_ID, os.path.relpath(full, APP_DIR)))
        z.writestr(f"{APP_ID}/portable.txt", PORTABLE_NOTE)
    say("portable.txt", f"OK: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


def step_checksums() -> None:
    lines = []
    for f in sorted(os.listdir(DIST)):
        if f.endswith((".exe", ".zip")):
            h = hashlib.sha256()
            with open(os.path.join(DIST, f), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            lines.append(f"{h.hexdigest()}  {f}")
    with open(os.path.join(DIST, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    say("checksums.txt", "\n".join(lines))


def main(argv: list) -> int:
    if argv == ["version"]:
        print(VERSION)
        return 0
    steps = argv or STEPS
    bad = [s for s in steps if s not in STEPS]
    if bad:
        print("Bước không hợp lệ:", ", ".join(bad), "| có:", ", ".join(STEPS))
        return 2
    os.makedirs(LOGS, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    say("summary.txt", f"Glossa {VERSION} - Python {sys.version.split()[0]} - {sys.platform}")
    for s in steps:
        t0 = time.time()
        try:
            globals()["step_" + s]()
        except (StepError, subprocess.SubprocessError, OSError) as e:
            say("summary.txt", f"[LỖI] {s}: {e}")
            return 1
        say("summary.txt", f"[OK] {s} ({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
