#!/bin/bash
# ============================================================
# Build macOS .dmg installer for Argo SBD Decoder
# Run this on a Mac with Python 3.8+ and dependencies installed.
#
# Usage:
#   chmod +x build_dmg.sh
#   ./build_dmg.sh
#
# Output: ArgoSBDDecoder_macOS.dmg
# ============================================================

set -e

echo "=== Argo SBD Decoder — macOS build ==="
echo ""

# Check we're on macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This script must be run on macOS."
    exit 1
fi

# Find Python
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
    echo "Install with: brew install python"
    exit 1
fi

echo "Using Python: $PYTHON ($($PYTHON --version 2>&1))"
echo ""

# Install PyInstaller if needed
if ! $PYTHON -m PyInstaller --version &>/dev/null 2>&1; then
    echo "Installing PyInstaller..."
    $PYTHON -m pip install pyinstaller --quiet
fi

# Build with PyInstaller
echo "Step 1/3: Building standalone app..."
$PYTHON build_exe.py

if [ ! -d "dist/ArgoSBDDecoder" ]; then
    echo "ERROR: Build failed. dist/ArgoSBDDecoder not found."
    exit 1
fi

echo ""
echo "Step 2/3: Creating .dmg..."

# Remove old .dmg if exists
rm -f ArgoSBDDecoder_macOS.dmg

# Create .dmg
hdiutil create \
    -volname "Argo SBD Decoder" \
    -srcfolder dist/ArgoSBDDecoder \
    -ov \
    -format UDZO \
    ArgoSBDDecoder_macOS.dmg

echo ""
echo "Step 3/3: Cleanup..."
rm -rf build/ dist/ ArgoSBDDecoder.spec

echo ""
echo "============================================================"
echo "BUILD COMPLETE!"
echo ""
echo "Output: ArgoSBDDecoder_macOS.dmg"
echo ""
echo "To distribute:"
echo "  1. Send ArgoSBDDecoder_macOS.dmg to users"
echo "  2. Users double-click the .dmg"
echo "  3. Drag ArgoSBDDecoder to Applications"
echo "  4. Run from Applications folder"
echo "============================================================"
