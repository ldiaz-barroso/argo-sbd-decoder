# Argo SBD Decoder — User Manual

## Introduction

The **Argo SBD Decoder** is a cross-platform tool for downloading, decoding and visualizing Iridium Short Burst Data (SBD) transmitted by Argo profiling floats. It includes a pure-Python decoder covering **all Argo SBD float families** (87 decoder IDs) and does not require any proprietary software.

**Key capabilities:**

- Download SBD files from any IMAP email provider (Gmail, Outlook, Yahoo, iCloud, or custom)
- Decode binary SBD files into human-readable CSV tables
- Generate TS diagrams, temperature/salinity sections, and trajectory maps
- **Forecast position** with next-surfacing predictions and KMZ output for Google Earth
- Float health monitoring: internal vacuum, drift speed, range between fixes
- Optional **GEBCO bathymetry** overlay on maps
- Support for **87 float types** across NKE, APEX (Teledyne/Webb), and NOVA/DOVA (NVS)

---

## Requirements

- **Python 3.8 or later** (automatically detected on Windows)
- Operating system: Windows, Linux, or macOS
- Internet connection (only for SBD download via IMAP)

---

## Installation

### Step 1: Extract the package

Unzip the distribution archive to a local folder, e.g.:

```
D:\Argo_decoder\Argo_SBD_Decoder_Products\
```

### Step 2: Install Python dependencies

**Windows (GUI):**
Open the application and click **Install deps**.

**Windows (command line):**
```powershell
cd Argo_SBD_Decoder_Products
python -m pip install -r requirements.txt
```

**Linux / macOS:**
```bash
cd Argo_SBD_Decoder_Products
chmod +x launch.sh
./launch.sh install
```

---

## Launching the application

### Windows (graphical interface)

Double-click **`Launch_Argo_SBD_Decoder.bat`**

The GUI will open with Python automatically detected. If Python is not found, click **Change...** to select your Python executable manually.

### Linux / macOS (command line)

```bash
./launch.sh
```

This displays the available commands. See the CLI Reference section below.

---

## GUI Workflow

The interface is split into two panels:

- **Left panel (top)**: Configuration, download, processing buttons
- **Left panel (bottom)**: Real-time log output
- **Right panel**: Plot preview — shows generated PNG images with a dropdown selector

You can drag the splitter between left and right to give more space to either the controls or the preview. The layout resizes properly when maximizing or resizing the window.

### Configuration

| Field | Description |
|-------|-------------|
| **Python** | Automatically detected. Click `...` only if needed. |
| **WMO** | Float WMO number (optional, for labelling). |
| **Float type** | Select your float firmware from the dropdown. |

### Download

| Field | Description |
|-------|-------------|
| **Email provider** | Select your email provider (Gmail, Outlook/Hotmail, Yahoo, iCloud, Other). Server, port and IMAP folder are auto-filled. |
| **Email** | Your email address. |
| **App password** | App-specific password (not your regular password). See provider setup below. |
| **IMEI** | The 15-digit Iridium IMEI of your float. |
| **From / Until** | Date range for the search. |
| **Sender** | Email sender to filter (default: sbdservice@sbd.iridium.com). |
| **IMAP folder** | Mailbox folder to search (auto-filled by provider, editable). |
| **IMAP server** | Only shown when provider is "Other". Enter your IMAP server hostname. |
| **Port** | Only shown when provider is "Other". Default: 993 (SSL). |

### Processing buttons

| Button | Action |
|--------|--------|
| **Decode** | Decode all .sbd files in the float root folder. |
| **TS+Sect** | Generate TS diagram and temperature/salinity sections. |
| **Map** | Generate trajectory map with profile/GPS-only markers. |
| **Forecast Position** | Forecast position map, KMZ, speed/range/vacuum plots. |
| **All products** | Run the full pipeline (decode + quicklook + forecast). |
| **Open output folder** | Open the products folder in file explorer. |
| **Install deps** | Install Python dependencies from requirements.txt. |

### Plot preview

After generating products, the right panel automatically shows the most recent plot. Use the dropdown at the bottom to browse all generated images. The preview updates after each processing step.

---

## Output files

After processing, outputs are organized as follows:

```
<float_root>/
├── sbd_raw/                     Downloaded .sbd files
├── decoded/                     Decoded CSV tables
│   ├── Technical Message.csv        GPS positions, timestamps, internal vacuum
│   ├── Ascent profile CTD Message.csv   Pressure, temperature, salinity profiles
│   ├── Descent profile CTD Message.csv  (if descent data exists)
│   ├── Drift measurements.csv          (if drift data exists)
│   └── Hydraulic actions.csv           Pump/valve events
└── products/
    ├── profiles/
    │   └── TS_IMEI_*.png            TS diagram with density contours
    ├── sections/
    │   ├── section_*_upper_*.png    Upper ocean section (0-300 dbar)
    │   └── section_*_full_depth_*.png  Full depth section
    ├── maps/
    │   ├── map_IMEI_*.png           Trajectory map (quicklook)
    │   └── forecast_map_*.png          Forecast position map with predictions
    ├── kmz/
    │   └── forecast_*.kmz              Google Earth track + predictions
    └── forecast/
        ├── navigation_raw_*.csv         All GPS fixes
        ├── navigation_summary_*.csv     With computed speed/range/prediction
        ├── internal_pressure_mbar_*.png Float vacuum health plot
        ├── speed_m_per_hour_*.png       Drift speed over time
        └── range_m_*.png                Distance between fixes
```

---

## CLI Reference (Linux / macOS / Windows)

### Full pipeline (recommended)

```bash
./launch.sh pipeline /path/to/float_folder --decoder_id 212
```

### Individual commands

```bash
# Decode SBD files
./launch.sh decode --root /path/to/float --decoder_id 211

# Download from any IMAP provider
./launch.sh download --email user@example.com --imei 300534065460740 \
    --since 20260701 --before 20260801 --outdir /path/to/float \
    --imap_server imap.gmail.com --imap_port 993

# Quick-look products (TS, sections, map)
./launch.sh quicklook --root /path/to/float --outdir /path/to/float/products \
    --imei 300534065460740 --technical_csv "Technical Message.csv"

# Forecast position products (map, KMZ, health plots)
./launch.sh forecast --root /path/to/float --outdir /path/to/float/products \
    --imei 300534065460740 --technical_csv "Technical Message.csv"

# Install dependencies
./launch.sh install

# List supported decoder IDs
./launch.sh list-decoders
```

### Windows CLI (without GUI)

```powershell
python scripts\decode_sbd_batch.py --root D:\floats\300534065460740 --decoder_id 211
python scripts\generate_quicklook_products.py --root D:\floats\300534065460740 --outdir D:\floats\300534065460740\products --imei 300534065460740 --technical_csv "Technical Message.csv"
```

---

## Supported float types

The decoder covers **all Argo floats with Iridium SBD transmission** — 87 decoder IDs across 5 manufacturers/platforms:

### NKE Instrumentation

| Family | Decoder IDs | Sensors | Frame |
|--------|-------------|---------|-------|
| PROVOR / ARVOR | 201–211, 213, 215 | CTD | 100-byte |
| ARVOR-ARN / Ice | 212, 214, 217, 222–227, 231, 232 | CTD + Ice detection | 100-byte |
| ARVOR-Deep | 216, 218, 221, 228–230 | CTD (4000 m) | 100-byte |
| ARVOR-C | 219, 220 | CTD (coastal) | 100-byte |
| PROVOR SBD2 | 301–303 | CTD + O₂ + FLBB/FLNTU | 140-byte |
| ARVOR PFV2 | 401–402 | CTD + O₂ (new platform) | SBD fragments |

### Teledyne/Webb (APEX)

| Family | Decoder IDs | Sensors | Format |
|--------|-------------|---------|--------|
| APEX APF9 | 1001–1016, 1314 | CTD (± O₂) | Text .msg/.log |
| APEX APF11 | 1101–1132, 1321–1323 | CTD + O₂ + FLBB + pH | Binary science_log |

### NVS (NOVA / DOVA)

| Family | Decoder IDs | Sensors | Format |
|--------|-------------|---------|--------|
| NOVA 1.0 | 2001 | CTD | Variable-length SBD |
| DOVA 2.0 | 2002 | CTD + O₂ | Variable-length SBD |
| NOVA 0.9 | 2003 | CTD | Variable-length SBD |

The GUI dropdown shows the float family and firmware version. The mapping is stored in `config/nke_to_decoder_id.json`. Use `./launch.sh list-decoders` to see all supported IDs.

> **Note:** NAVIS floats (IDs 1301–1323) use RUDICS transmission, not SBD, and are not supported.

---

## Email App Password setup

To download SBD files, you need an **App Password** from your email provider (not your regular password):

### Gmail
1. Go to https://myaccount.google.com/apppasswords
2. You must have 2-Step Verification enabled
3. Generate a new App Password for "Mail"
4. Copy the 16-character password into the GUI

### Outlook / Hotmail
1. Go to https://account.microsoft.com/security
2. Enable Two-step verification
3. Generate an App Password under "Additional security options"
4. Copy the generated password into the GUI

### Yahoo
1. Go to https://login.yahoo.com/account/security
2. Enable Two-step verification
3. Generate an App Password for "Other App"
4. Copy the generated password into the GUI

### iCloud
1. Go to https://appleid.apple.com/account/manage
2. Under "Sign-In and Security", select "App-Specific Passwords"
3. Generate a new password
4. Copy the generated password into the GUI

### Other providers
Contact your email administrator for IMAP access credentials. You may need to enable IMAP access in your account settings.

The application searches for emails from `sbdservice@sbd.iridium.com` containing `.sbd` attachments matching your IMEI.

---

## Position Prediction (Forecast)

The **Forecast Position** button generates a predicted next-surfacing position for each GPS fix. This is designed for **float recovery planning**. The prediction method was suggested by **Gene Massion (MBARI)**.

### How it works

For each pair of consecutive GPS fixes, the decoder computes the displacement vector (distance + heading) and projects it forward from the current position. The predicted point is where the float would surface if it continued drifting in the same direction at the same speed.

### Output

| Product | Content |
|---------|---------|
| `forecast_map_*.png` | Map with P (profile), G (GPS-only), Pred (predicted) markers |
| `forecast_*.kmz` | Google Earth file with tracks, P/G markers, and prediction vectors |
| `navigation_summary_*.csv` | CSV with `predicted_lat`, `predicted_lon`, `has_profile` columns |

### KMZ marker types

| Marker | Colour | Meaning |
|--------|--------|---------|
| **P1, P2, P3...** | Green | GPS fix with associated CTD profile |
| **G1, G2, G3...** | Blue | GPS fix without profile (EOL, surface test) |
| **Pred 1, Pred 2...** | Red | Predicted next-surfacing position |
| Red dashed line | Red | Vector from real fix to prediction |

### When predictions are reliable

**End-of-Life (EOL) mode**: fixes every few minutes to hours, float at surface — predictions are accurate (median error ~560 m).

**Short-cycle profiling (sampling depth < 500 m)**: cycle times of 12–48 hours — predictions remain accurate (median error ~440 m).

**Long-cycle profiling (deep-ocean)**: cycle times of 5–10 days, profiling to 2000–6000 m — accuracy decreases. Combine with ocean current forecasts for recovery planning.

### Validation (real data, ARVOR-C, Western Mediterranean Sea)

| Mode | N predictions | Median error | Mean error |
|------|--------------|-------------|------------|
| EOL | 10 | **27 m** | 31 m |
| Profiling | 29 | **263 m** | 345 m |

For recovery during profiling mode, combine predictions with ocean current forecasts (e.g. <a href="https://marine.copernicus.eu">Copernicus Marine Service</a>).

---

## GEBCO Bathymetry (optional)

Download the GEBCO 2024 grid from https://www.gebco.net (NetCDF format) and set the path in the GUI "GEBCO bathy" field. Maps will then include colour-shaded ocean depth contours.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Python not configured" | Click Change... and select your python.exe |
| Empty decoded folder | Check that .sbd files exist in `<root>/sbd_raw/` |
| Map has no bathymetry | Select a GEBCO NetCDF file in the GEBCO field |
| No forecast plots | Ensure Technical Message.csv exists in `decoded/` |
| Download fails | Verify App Password, IMEI, date range, and email provider selection |

---

## Credits and references

**Decoder:** Python translation of the Coriolis data processing chain for Argo floats.
> Rannou Jean-Philippe, Carval Thierry, Fontaine Laure, Bernard Vincent, Coatanoan Christine (2025). Coriolis Argo floats data processing chain. SEANOE. https://doi.org/10.17882/45589

**Application:** Developed by SOCIB and IEO-CSIC.

**Funding:** This software has received funding from the Euro-Argo ONE project (Grant Agreement No. 101188133).

---

## License

The Coriolis decoder source is licensed under CeCILL (GPL-compatible). This application follows the same license terms.
