@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel% equ 0 (
    python run.py
) else (
    py run.py
)
pause
