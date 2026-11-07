@echo off
cd /d "%~dp0"
title Glossa - Tu kiem tra
if not exist ".venv\Scripts\python.exe" (
  echo Chua cai dat. Hay chay CAI_DAT.bat truoc.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py --selftest
echo.
pause
