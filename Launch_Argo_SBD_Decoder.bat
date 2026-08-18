@echo off
setlocal
cd /d "%~dp0"
python "%~dp0app\gui.py"
if errorlevel 1 (
    echo.
    echo Python not found or GUI failed. Trying py launcher...
    py -3 "%~dp0app\gui.py"
)
