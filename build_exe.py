#!/usr/bin/env python3
"""
Build standalone executable with PyInstaller.

Run this script on each target platform:
  - Windows: generates ArgoSBDDecoder.exe
  - macOS:   generates ArgoSBDDecoder.app
  - Linux:   generates ArgoSBDDecoder binary

Prerequisites:
    pip install pyinstaller

Usage:
    python build_exe.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
APP = ROOT / "app" / "gui.py"
SCRIPTS = ROOT / "scripts"
CONFIG = ROOT / "config"
ASSETS = ROOT / "assets"
DOCS = ROOT / "docs"

SEP = ";" if sys.platform == "win32" else ":"

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--name", "ArgoSBDDecoder",
    "--onedir",
    "--windowed",
    "--noconfirm",
    f"--add-data={CONFIG}{SEP}config",
    f"--add-data={ASSETS}{SEP}assets",
    f"--add-data={DOCS}{SEP}docs",
    f"--add-data={SCRIPTS}{SEP}scripts",
    "--hidden-import=numpy",
    "--hidden-import=pandas",
    "--hidden-import=matplotlib",
    "--hidden-import=matplotlib.backends.backend_tkagg",
    "--hidden-import=gsw",
    "--hidden-import=pyproj",
    "--hidden-import=simplekml",
    "--hidden-import=xarray",
    "--hidden-import=netCDF4",
    str(APP),
]

print(f"Building standalone for {sys.platform}...")
print(f"Python: {sys.executable}")
print()

result = subprocess.run(cmd, cwd=str(ROOT))

if result.returncode == 0:
    print()
    print("=" * 60)
    print("BUILD SUCCESSFUL!")
    print(f"Output: {ROOT / 'dist' / 'ArgoSBDDecoder'}")
    print()
    if sys.platform == "win32":
        print("Run: dist/ArgoSBDDecoder/ArgoSBDDecoder.exe")
    elif sys.platform == "darwin":
        print("Run: open dist/ArgoSBDDecoder/ArgoSBDDecoder.app")
    else:
        print("Run: ./dist/ArgoSBDDecoder/ArgoSBDDecoder")
    print()
    print("Zip the dist/ArgoSBDDecoder/ folder to distribute.")
    print("=" * 60)
else:
    print(f"BUILD FAILED (exit code {result.returncode})")
    sys.exit(1)
