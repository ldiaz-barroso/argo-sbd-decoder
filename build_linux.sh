#!/bin/bash
# ============================================================
# Build Linux standalone for Argo SBD Decoder
# Run this on a Linux machine with Python 3.8+ and dependencies installed.
#
# Usage:
#   chmod +x build_linux.sh
#   ./build_linux.sh
#
# Output: ArgoSBDDecoder_Linux.tar.gz
# ============================================================

set -e

echo "=== Argo SBD Decoder — Linux build ==="
echo ""

if [[ "$(uname)" != "Linux" ]]; then
    echo "ERROR: This script must be run on Linux."
    exit 1
fi

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 8 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.8+ not found."
    exit 1
fi

echo "Using Python: $PYTHON ($($PYTHON --version 2>&1))"
echo ""

if ! $PYTHON -m PyInstaller --version &>/dev/null 2>&1; then
    echo "Installing PyInstaller..."
    $PYTHON -m pip install pyinstaller --quiet
fi

echo "Step 1/3: Building standalone..."
$PYTHON build_exe.py

if [ ! -d "dist/ArgoSBDDecoder" ]; then
    echo "ERROR: Build failed."
    exit 1
fi

echo ""
echo "Step 2/3: Creating .tar.gz..."
rm -f ArgoSBDDecoder_Linux.tar.gz
tar -czf ArgoSBDDecoder_Linux.tar.gz -C dist ArgoSBDDecoder

echo ""
echo "Step 3/3: Cleanup..."
rm -rf build/ dist/ ArgoSBDDecoder.spec

echo ""
echo "============================================================"
echo "BUILD COMPLETE!"
echo ""
echo "Output: ArgoSBDDecoder_Linux.tar.gz"
echo ""
echo "To distribute:"
echo "  1. Send ArgoSBDDecoder_Linux.tar.gz to users"
echo "  2. Users: tar -xzf ArgoSBDDecoder_Linux.tar.gz"
echo "  3. Run: ./ArgoSBDDecoder/ArgoSBDDecoder"
echo "============================================================"
