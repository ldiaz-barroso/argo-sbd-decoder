#!/usr/bin/env bash
# =============================================================================
# Argo SBD Decoder - Cross-platform launcher (Linux / macOS)
# =============================================================================
# Usage:
#   ./launch.sh                          # Interactive mode (CLI menu)
#   ./launch.sh decode --root /path --decoder_id 212
#   ./launch.sh download --email x --imei y --since 20260101 --before 20260201
# =============================================================================
# Requirements:
#   Python 3.8+ with packages in requirements.txt
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$SCRIPT_DIR/scripts"
CONFIG_DIR="$SCRIPT_DIR/config"

# Find Python
find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" &>/dev/null; then
            local version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
            local major=$(echo "$version" | cut -d. -f1)
            local minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 8 ]; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    echo ""
    return 1
}

PYTHON=$(find_python)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.8+ not found. Install Python and try again."
    exit 1
fi

echo "Using Python: $PYTHON ($($PYTHON --version 2>&1))"
echo ""

# =============================================================================
# Commands
# =============================================================================

case "${1:-menu}" in

    decode)
        shift
        echo "=== Decoding SBD files ==="
        $PYTHON "$SCRIPTS_DIR/decode_sbd_batch.py" "$@"
        ;;

    download)
        shift
        echo "=== Downloading SBD from IMAP ==="
        $PYTHON "$SCRIPTS_DIR/download_sbd_imap.py" "$@"
        ;;

    quicklook)
        shift
        echo "=== Generating quick-look products ==="
        $PYTHON "$SCRIPTS_DIR/generate_quicklook_products.py" "$@"
        ;;

    navigation|forecast)
        shift
        echo "=== Generating forecast position products ==="
        $PYTHON "$SCRIPTS_DIR/generate_navigation_products.py" "$@"
        ;;

    fill)
        shift
        echo "=== Fill pressure: not needed ==="
        echo "The Coriolis-based decoder already computes all pressure values during decoding."
        echo "This command is kept for backward compatibility but does nothing."
        ;;

    install)
        echo "=== Installing dependencies ==="
        if $PYTHON -m pip --version &>/dev/null; then
            $PYTHON -m pip install --upgrade pip
            $PYTHON -m pip install -r "$SCRIPT_DIR/requirements.txt"
        elif command -v pip3 &>/dev/null; then
            pip3 install --upgrade pip
            pip3 install -r "$SCRIPT_DIR/requirements.txt"
        else
            echo "ERROR: pip not found. Install it with:"
            echo "  Ubuntu/Debian: sudo apt install python3-pip"
            echo "  macOS:         brew install python3 (pip included)"
            echo "  Fedora/RHEL:   sudo dnf install python3-pip"
            echo "  Or:            $PYTHON -m ensurepip --upgrade"
            exit 1
        fi
        echo "Done."
        ;;

    list-decoders)
        $PYTHON "$SCRIPTS_DIR/argofluxdecoder/decode_sbd.py" --list_decoders --sbd_dir . --outdir .
        ;;

    pipeline)
        # Full pipeline: decode -> quicklook -> navigation
        shift
        ROOT="${1:?ERROR: provide --root path}"
        shift
        echo "=== Full pipeline ==="
        echo ""
        echo "Step 1/3: Decoding SBD files..."
        $PYTHON "$SCRIPTS_DIR/decode_sbd_batch.py" --root "$ROOT" "$@"
        echo ""
        echo "Step 2/3: Generating quick-look products..."
        $PYTHON "$SCRIPTS_DIR/generate_quicklook_products.py" --root "$ROOT" --outdir "$ROOT/products"
        echo ""
        echo "Step 3/3: Generating forecast position products..."
        $PYTHON "$SCRIPTS_DIR/generate_navigation_products.py" --root "$ROOT" --outdir "$ROOT/products"
        echo ""
        echo "=== Pipeline complete ==="
        ;;

    menu|"")
        echo "=============================================="
        echo " Argo SBD Decoder v5.2"
        echo " 87 decoder IDs | All SBD float families"
        echo " Cross-platform (Linux/macOS/Windows)"
        echo "=============================================="
        echo ""
        echo "Commands:"
        echo "  ./launch.sh decode        Decode SBD files to CSV"
        echo "  ./launch.sh download      Download SBD from Gmail/IMAP"
        echo "  ./launch.sh quicklook     Generate TS diagrams and sections"
        echo "  ./launch.sh forecast      Forecast position, maps, KMZ, health"
        echo "  ./launch.sh pipeline      Run full pipeline"
        echo "  ./launch.sh install       Install Python dependencies"
        echo "  ./launch.sh list-decoders List supported float decoder IDs"
        echo ""
        echo "Example:"
        echo "  ./launch.sh decode --root ~/floats/300534065469590 --decoder_id 212 --wmo 6901477"
        echo "  ./launch.sh pipeline ~/floats/300534065469590 --decoder_id 212"
        echo ""
        ;;

    *)
        echo "Unknown command: $1"
        echo "Run './launch.sh' without arguments for help."
        exit 1
        ;;
esac
