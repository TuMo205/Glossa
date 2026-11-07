@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Glossa - Cai dat
echo.
echo  ==========================================================
echo    Glossa - Dich man hinh sang tieng Viet
echo    Cai dat lan dau: can Internet, tai khoang 150 MB
echo  ==========================================================
echo.

rem ---- 1. Tim Python 3.10 - 3.14 (khong dung ban free-threaded) ----
set "PYEXE="
for %%V in (3.13 3.12 3.11 3.14 3.10) do (
  if not defined PYEXE (
    for /f "usebackq delims=" %%P in (`py -%%V -c "import sys,sysconfig;print('' if sysconfig.get_config_var('Py_GIL_DISABLED') else sys.executable)" 2^>nul`) do set "PYEXE=%%P"
  )
)
if not defined PYEXE (
  for /f "usebackq delims=" %%P in (`python -c "import sys,sysconfig;ok=(3,10)<=sys.version_info[:2]<=(3,14) and not sysconfig.get_config_var('Py_GIL_DISABLED');print(sys.executable if ok else '')" 2^>nul`) do set "PYEXE=%%P"
)
if not defined PYEXE (
  echo [!] Khong tim thay Python 3.10 - 3.14 tren may.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo     Hay cai Python 3.13 tu https://www.python.org/downloads/ roi chay lai file nay.
    pause
    exit /b 1
  )
  echo     Dang cai Python 3.13 bang winget...
  winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
  set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  if not exist "!PYEXE!" (
    echo [!] Cai Python khong thanh cong. Hay cai thu cong roi chay lai.
    pause
    exit /b 1
  )
)
echo [1/5] Python: !PYEXE!

rem ---- 2. Moi truong rieng ----
echo [2/5] Tao moi truong rieng .venv ...
if not exist ".venv\Scripts\python.exe" (
  "!PYEXE!" -m venv .venv
  if errorlevel 1 (
    echo [!] Loi tao .venv
    pause
    exit /b 1
  )
)
set "VPY=%~dp0.venv\Scripts\python.exe"

rem ---- 3. Thu vien ----
echo [3/5] Cai thu vien: PySide6, numpy, mss, winrt, requests ...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
  echo [!] Cai thu vien that bai. Kiem tra Internet roi chay lai CAI_DAT.bat
  pause
  exit /b 1
)

rem ---- 4. Goi OCR cua Windows ----
echo [4/5] Kiem tra goi nhan dang chu OCR cua Windows ...
"%VPY%" app.py --check-ocr
if errorlevel 2 (
  echo.
  echo [!] Windows chua co goi OCR. Mo trinh cai goi OCR, can quyen Admin ...
  call "%~dp0CAI_NGON_NGU_OCR.bat"
)

rem ---- 5. Loi tat + khoi dong cung Windows ----
echo [5/5] Tao loi tat tren Desktop ...
"%VPY%" app.py --shortcut
echo.
choice /c YN /n /m "Cho Glossa tu chay moi khi bat may? [Y/N]: "
if errorlevel 2 (
  "%VPY%" app.py --autostart off >nul
) else (
  "%VPY%" app.py --autostart on
)

echo.
echo  XONG! Dang mo Glossa - bieu tuong chu V (do) / E (xanh) o khay he thong.
echo    Ctrl+Alt+T : bat / tat dich       Ctrl+Alt+R : chon vung can dich
echo    Ctrl+Alt+F : dich toan man hinh   Ctrl+Alt+H : an / hien ban dich
echo    Ctrl+Alt+Q : thoat
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
echo.
pause
