@echo off
REM ============================================================
REM Build Windows standalone for Argo SBD Decoder
REM Run this on Windows with Python 3.8+ and dependencies installed.
REM
REM Usage: double-click this file or run from command prompt
REM
REM Output: ArgoSBDDecoder_Windows.zip
REM ============================================================

echo === Argo SBD Decoder - Windows build ===
echo.

python build_exe.py
if errorlevel 1 (
    echo BUILD FAILED
    pause
    exit /b 1
)

echo.
echo Compressing...
if exist "ArgoSBDDecoder_Windows.zip" del "ArgoSBDDecoder_Windows.zip"
powershell -Command "Compress-Archive -Path 'dist\ArgoSBDDecoder\*' -DestinationPath 'ArgoSBDDecoder_Windows.zip' -Force"

echo.
echo Cleanup...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del ArgoSBDDecoder.spec 2>nul

echo.
echo ============================================================
echo BUILD COMPLETE!
echo.
echo Output: ArgoSBDDecoder_Windows.zip
echo.
echo To distribute:
echo   1. Send ArgoSBDDecoder_Windows.zip to users
echo   2. Users unzip and run ArgoSBDDecoder.exe
echo ============================================================
pause
