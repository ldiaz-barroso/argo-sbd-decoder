# Building Standalone Executables

The Argo SBD Decoder can be packaged as a standalone executable that users can run **without installing Python or any dependencies**.

## Prerequisites (build machine only)

1. Python 3.8+ with all dependencies installed:
   ```bash
   pip install -r requirements.txt
   pip install pyinstaller
   ```

2. The build **must be done on the target OS** — you cannot cross-compile:

| Build on | Generates for | Script to run |
|----------|---------------|---------------|
| Windows | Windows (.exe) | `build_windows.bat` |
| macOS | macOS (.dmg) | `./build_dmg.sh` |
| Linux | Linux (.tar.gz) | `./build_linux.sh` |

## Building

### Windows

Double-click `build_windows.bat` or from command prompt:
```cmd
build_windows.bat
```

Output: `ArgoSBDDecoder_Windows.zip` (~78 MB)

Users unzip and run `ArgoSBDDecoder.exe`.

### macOS

```bash
chmod +x build_dmg.sh
./build_dmg.sh
```

Output: `ArgoSBDDecoder_macOS.dmg` (~80 MB)

Users open the .dmg and drag to Applications.

### Linux

```bash
chmod +x build_linux.sh
./build_linux.sh
```

Output: `ArgoSBDDecoder_Linux.tar.gz` (~75 MB)

Users extract and run `./ArgoSBDDecoder/ArgoSBDDecoder`.

## What gets packaged

The standalone includes:
- Python runtime (embedded)
- All Python dependencies (numpy, pandas, matplotlib, gsw, etc.)
- The decoder scripts (`scripts/argofluxdecoder/`)
- Configuration files (`config/`)
- Documentation (`docs/`)
- Assets (logo)

## User experience (after distribution)

| OS | What user does |
|----|----------------|
| Windows | Unzip → double-click `ArgoSBDDecoder.exe` |
| macOS | Open .dmg → drag to Applications → launch |
| Linux | `tar -xzf` → `./ArgoSBDDecoder/ArgoSBDDecoder` |

No Python installation, no pip, no terminal commands needed by end users.

## Notes

- First launch may be slow (antivirus scanning on Windows)
- macOS may require: System Preferences → Security → "Allow" (unsigned app)
- File size is ~75-80 MB because Python + scientific libraries are bundled
- User settings are stored in `~/.argo_sbd_decoder/` (not inside the app folder)
