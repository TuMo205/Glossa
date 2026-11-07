@echo off
cd /d "%~dp0"
title Glossa - Go cai dat
echo Tat khoi dong cung Windows va xoa loi tat tren Desktop...
if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" app.py --autostart off
powershell -NoProfile -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Desktop') + '\Glossa.lnk')"
echo.
echo Xong. Hay thoat Glossa (Ctrl+Alt+Q) roi xoa thu muc nay de go hoan toan:
echo   %~dp0
pause
