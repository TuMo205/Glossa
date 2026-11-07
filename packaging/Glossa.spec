# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: đóng gói Glossa thành thư mục dist/Glossa (Glossa.exe + _internal).
# Chạy qua packaging/build.py (tạo biểu tượng trước), hoặc:  pyinstaller --noconfirm packaging/Glossa.spec
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from glossa import APP_ID, APP_NAME, PUBLISHER, __version__  # noqa: E402

ICON = os.path.join(ROOT, "build", "glossa.ico")

# winrt (Windows OCR): nhiều module được nạp động (importlib / từ mã C) => khai báo hết
hidden = ["PySide6.QtNetwork"]
binaries = []
if sys.platform == "win32":
    import winrt._winrt

    winrt_dir = os.path.dirname(winrt._winrt.__file__)
    hidden += collect_submodules("winrt")
    hidden += [
        "winrt._winrt", "winrt.system", "winrt.runtime", "winrt.runtime._internals", "winrt.runtime.interop",
        "winrt.windows.foundation", "winrt.windows.foundation.collections", "winrt.windows.globalization",
        "winrt.windows.graphics.imaging", "winrt.windows.media.ocr", "winrt.windows.storage.streams",
        "winrt._winrt_windows_foundation", "winrt._winrt_windows_foundation_collections",
        "winrt._winrt_windows_globalization", "winrt._winrt_windows_graphics_imaging",
        "winrt._winrt_windows_media_ocr", "winrt._winrt_windows_storage_streams",
    ]
    for f in os.listdir(winrt_dir):
        if f.lower().endswith(".dll"):  # msvcp140.dll đi kèm winrt
            binaries.append((os.path.join(winrt_dir, f), "winrt"))

datas = [
    (os.path.join(ROOT, "HUONG_DAN.md"), "."),
    (os.path.join(ROOT, "install_ocr_languages.ps1"), "."),
]

a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    excludes=["tkinter", "unittest", "pytest", "pydoc_data", "PyInstaller", "cryptography", "OpenSSL", "yaml",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest"],
    noarchive=False,
)

# bỏ phần Qt không dùng: OpenGL phần mềm (~20 MB) và bản dịch giao diện Qt (chương trình tự viết tiếng Việt)
a.binaries = [b for b in a.binaries if "opengl32sw" not in b[0].lower()]
a.datas = [d for d in a.datas if not (d[0].replace("\\", "/").startswith("PySide6/")
                                      and "/translations/" in d[0].replace("\\", "/") and d[0].endswith(".qm"))]

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

    nums = tuple(int(x) for x in (__version__.split(".") + ["0", "0", "0"])[:4])
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("042A04B0", [
                StringStruct("CompanyName", PUBLISHER),
                StringStruct("FileDescription", f"{APP_NAME} - dịch màn hình sang tiếng Việt"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", APP_ID),
                StringStruct("OriginalFilename", f"{APP_ID}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", __version__),
                StringStruct("LegalCopyright", f"© 2026 {PUBLISHER}"),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x042A, 1200])]),
        ],
    )

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_ID,
    console=False,
    icon=ICON if os.path.exists(ICON) else None,
    version=version_info,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_ID, upx=False)
