@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 app\server.py
) else (
  python app\server.py
)
pause
