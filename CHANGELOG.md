# Changelog

## 5.2.0 (2026-08-17)

### Fixed
- ARVOR-C decoder (IDs 219, 220, 233): orphan CTD packets from retransmissions were incorrectly assigned to the previous cycle, inflating profile data. Now limits accepted CTD packets per cycle to `exp_nb_asc` from the tech packet.

### Removed
- "Fill pressure" button and pipeline step removed from the GUI. The Coriolis-based decoder already computes all pressure values during decoding — the fill step was redundant and produced incorrect split files (`unknown_*_filled_*.csv`).

## 5.1.0 (2026-08-13)

### Added
- Multi-provider IMAP support: download SBDs from Gmail, Outlook, Yahoo, iCloud or any custom IMAP server
- Email provider dropdown in GUI with auto-fill of server, port and IMAP folder
- Configurable IMAP server (`--imap_server`) and port (`--imap_port`) in `download_sbd_imap.py`
- IMAP folder field visible in GUI (editable, auto-filled by provider)
- Scrollable controls panel — buttons no longer disappear when resizing
- Flow layout for processing buttons — buttons wrap to next row when panel is narrow

### Changed
- "Gmail" field renamed to "Email" (now supports any email address)
- Environment variable `IMAP_APP_PASSWORD` replaces `GMAIL_APP_PASSWORD` (backward compatible)
- Default IMAP label changed from `[Gmail]/All Mail` to provider-specific default
- Documentation updated (README, USER_MANUAL, QUICK_START, ABOUT)

## 5.0.0 (2026-07-29)

Major rewrite: pure Python decoder replaces NKE proprietary parser.

### Added
- Pure Python SBD decoder (`argofluxdecoder` package) based on the Coriolis MATLAB decoder v085h (DOI: 10.17882/45589)
- Support for 29 NKE Iridium SBD float types (decoder IDs 201-232)
- Cross-platform GUI in Python tkinter (replaces Windows-only PowerShell GUI)
- Plot preview panel in the GUI
- Internal pressure (vacuum) extraction and health plot
- GPS coordinates with full precision (10 decimal places)
- GPS timestamps from tech1 packet
- Navigation products: KMZ, drift speed, range, trajectory map with profile/GPS-only distinction
- TS diagram, temperature/salinity sections (upper + full depth)
- CLI launcher for Linux/macOS (`launch.sh`)
- User manual in English (`docs/USER_MANUAL.md`)
- Float type selector based on official `_CoriolisArgoFloatVersions.xlsx`

### Fixed
- Sensor conversions verified against Coriolis MATLAB for all decoder groups:
  - Pressure: three offset groups (A: +30000, B: +10000, C: 0)
  - Temperature: twos_complement/1000 for all IDs
  - Salinity: (raw+10000)/1000 for Group A, raw/1000 for Groups B+C
- ArvorDeep tech1 bit layout corrected (GPS at indices 49-56, not 52-59)
- `nke_to_decoder_id.json` rebuilt from official Coriolis spreadsheet

### Changed
- Application renamed from "SOCIB Argo NKE Decoder" to "Argo SBD Decoder"
- GUI rewritten from PowerShell WinForms to Python tkinter
- SVG output removed (caused Edge to open on Windows)
- QC/NetCDF/reports generation removed from default pipeline
- Euro-Argo ERIC branding applied (navy header, logo, credits)

### Removed
- Dependency on NKE Instrumentation Parser (proprietary)
- PowerShell GUI (`SOCIB_Argo_NKE_Decoder_GUI.ps1`)
- `build_scientific_products.py` from default workflow

## 4.0.0 (2026-07-28)

- Initial v4 release with GUI and NKE parser integration.
- Added normalized profile/navigation data models with QC flags.
- Added CF-1.10 NetCDF export.
- Added TEOS-10 density contours (GSW).
