# Argo SBD Decoder

A cross-platform Python application for downloading, decoding and visualizing Iridium Short Burst Data (SBD) messages transmitted by Argo profiling floats.

## Main features

- Download SBD attachments from any IMAP email provider (Gmail, Outlook, Yahoo, iCloud, custom)
- **Pure Python SBD decoder** covering 87 float types across all major manufacturers
- Cross-platform: **Linux, macOS, Windows** (GUI + CLI)
- Generate TS diagrams, temporal sections, trajectory maps and KMZ files
- Optional **GEBCO bathymetry** overlay on maps and sections
- Calculate density contours using TEOS-10 (`gsw`)
- Float health monitoring: internal vacuum, drift speed, range plots

> **Note:** The decoder covers 87 float types based on the [Coriolis MATLAB decoder](https://doi.org/10.17882/45589). Currently tested and validated with real SBD data for **ARVOR I** and **ARVOR C**. Other float types will be validated as we gain access to their SBD files.

## Supported float families

| Manufacturer | Decoder IDs | Float Type | Frame Format |
|---|---|---|---|
| **NKE** (2xx) | 201-232 | ARVOR, PROVOR, ARVOR-Deep, ARVOR-C | Binary 100-byte frames |
| **NKE SBD2** | 301-303 | PROVOR Remocean FLBB, ARVOR CM | Binary 140-byte frames |
| **NKE PFV2** | 401-402 | ARVOR PFV2 (new platform) | SBD fragment reassembly |
| **APEX APF9** | 1001-1016, 1314 | Teledyne/Webb APEX APF9 | Text .msg/.log parsing |
| **APEX APF11** | 1101-1132, 1321-1323 | Teledyne/Webb APEX APF11 | Binary science_log + text system_log |
| **NOVA/DOVA** | 2001-2003 | NVS NOVA (CTD) and DOVA (CTD+DO) | Variable-length SBD messages |

Total: **87 decoder IDs, 9 decoder classes, 5 platforms**.

## Supported platforms

| OS | Interface | Requirements |
|----|-----------|-------------|
| **Linux** | CLI (`./launch.sh`) or GUI (`python3 app/gui.py`) | Python 3.8+ |
| **macOS** | CLI (`./launch.sh`) or GUI (`python3 app/gui.py`) | Python 3.8+ |
| **Windows** | GUI (`Launch_Argo_SBD_Decoder.bat`) or CLI | Python 3.8+ |

## Quick start

### Windows (GUI)

Double-click **`Launch_Argo_SBD_Decoder.bat`** or run:
```
python app\gui.py
```

### Linux / macOS (GUI)

```bash
cd Argo_SBD_Decoder_Products
python3 app/gui.py
```

### Linux / macOS (CLI)

```bash
chmod +x launch.sh

# Install dependencies
./launch.sh install

# Full pipeline: decode + quicklook + forecast position
./launch.sh pipeline /path/to/float_folder --decoder_id 212

# Or step by step:
./launch.sh decode --root /path/to/float_folder --decoder_id 1314
./launch.sh quicklook --root /path/to/float_folder --outdir /path/to/float_folder/products
./launch.sh forecast --root /path/to/float_folder --outdir /path/to/float_folder/products

# List all supported decoder IDs
./launch.sh list-decoders
```

### Windows (CLI)

```powershell
python scripts\decode_sbd_batch.py --root D:\floats\my_float --decoder_id 212
python scripts\generate_quicklook_products.py --root D:\floats\my_float --outdir D:\floats\my_float\products
```

## Position Prediction (Recovery Support)

The forecast position module includes a **position prediction** that estimates where the float will surface next. The method was suggested by **Gene Massion (MBARI)** and uses simple linear extrapolation: the displacement vector from the previous fix to the current fix is projected forward from the current position.

### When it works well

**End-of-Life (EOL) mode:** The float stays at the surface transmitting GPS fixes every few minutes to hours. Surface drift is consistent, so linear extrapolation gives good estimates for recovery planning.

**Short-cycle profiling (sampling depth < 500 m):** Floats with cycle times of 12–48 hours and shallow profiling depth also benefit from accurate predictions because the net displacement between surfacings is well captured by the linear model.

### Validation

Using real data from an ARVOR-C float (IMEI 300534067324210, Western Mediterranean Sea):

| Mode | N predictions | Median error | Mean error | Max |
|------|--------------|-------------|------------|-----|
| **EOL** | 10 | **27 m** | 31 m | 66 m |
| **Profiling** | 29 | **263 m** | 345 m | 829 m |

In the EOL validation, the float transmitted a GPS fix every ~2 minutes while drifting at the surface. In profiling mode, fixes were separated by ~24 hours (one dive cycle).

The prediction is most reliable after two consecutive GPS fixes of the same type. The first prediction after a mode transition (e.g. from profiling to EOL) has higher error because the displacement vector mixes two different regimes. Once the float establishes a consistent pattern, accuracy improves significantly.

<p align="center">
  <img src="./assets/prediction_validation.png" width="100%" alt="Position prediction validation">
</p>

*Figure: Position prediction validation using real data from the Western Mediterranean Sea. (a) EOL mode map showing GPS positions (black dots), predicted positions (red crosses) and prediction errors (red lines with distance labels). (b) Individual prediction errors for each navigation point — profiling mode (blue) and EOL mode (green), with dashed lines indicating the median for each mode. (c) Error distribution boxplot comparing profiling and EOL modes.*

In steady EOL mode the median error is 27 m. In profiling mode (cycles of ~24h, depth < 500 m) the median is 263 m, effective for planning recovery operations.

### Limitations

For deep-ocean floats with long cycles (5–10 days, profiling to 2000–6000 m), the prediction accuracy decreases because subsurface currents can differ significantly from the surface drift captured between fixes. In such cases, combine predictions with ocean current forecasts (e.g. Copernicus Marine Service).

### Profile vs GPS-only positions

The forecast output distinguishes between two types of positions:
- **Profile points** (`has_profile = True`): GPS fix associated with a decoded CTD profile (the float surfaced, transmitted science data, and got a fix)
- **GPS-only points** (`has_profile = False`): Surface GPS fix without an associated profile (e.g. EOL transmissions, test fixes, or multiple fixes during one surfacing)

This distinction matters because:
- In profiling mode, consecutive profile points are separated by full dive cycles (days)
- In EOL mode, all points are GPS-only and closely spaced (minutes to hours)
- The prediction reliability depends on which type of points are being used

### Output

Predictions appear in:
- `navigation_summary_*.csv` (columns: `predicted_lat`, `predicted_lon`, `prediction_basis`, `has_profile`)
- KMZ files (red "Pred" markers + dashed vectors from real position to predicted)
- Map plots (blue X markers with dashed lines)

## GEBCO Bathymetry (recommended)

The application supports overlaying **GEBCO bathymetry** on trajectory maps and depth sections. This is optional but highly recommended for operational context.

### Setup

1. Download the GEBCO 2024 grid (or any recent version) from: https://www.gebco.net/data_and_products/gridded_bathymetry_data/
2. Use the NetCDF format (`.nc` file). You can download a regional subset or the full global grid.
3. In the GUI: set the path in the "GEBCO bathy" field, or place the file in `resources/gebco/`.
4. In CLI: pass `--bathymetry_file /path/to/GEBCO_2024.nc` to the quicklook script.

The application reads the GEBCO grid lazily (only the region around your float's trajectory is loaded into memory), so even the full global grid (~8 GB) works without issues.

### What it provides

- Colour-shaded ocean depth on trajectory maps
- Bathymetry profile along the float's track in section plots
- Coastline contours derived from the zero-depth line

Requirements: `xarray` + `netCDF4` (included in `requirements.txt`).

## Installation

1. Install Python 3.8 or later (3.10+ recommended).
2. Extract this package to a local directory.
3. Install dependencies:

```bash
# Linux / macOS
pip3 install -r requirements.txt
# or
./launch.sh install

# Windows
python -m pip install -r requirements.txt
```

### Cross-platform notes

- The application uses `pathlib.Path` throughout — all paths work on Linux, macOS, and Windows.
- The GUI uses `tkinter` which is included with Python on all platforms.
- On macOS: if `tkinter` is missing, install via `brew install python-tk`.
- On Linux: install `python3-tk` via your package manager if not present.
- All scripts use `#!/usr/bin/env python3` shebangs for Unix compatibility.
- Line endings: the repository uses LF. Git should handle CRLF conversion automatically.

## Output structure

```
<float_root>/
├── sbd_raw/                  Raw .sbd files from Iridium
├── decoded/                  Decoded CSVs (from Python decoder)
│   ├── Technical Message.csv
│   ├── Ascent profile CTD Message.csv
│   ├── Descent profile CTD Message.csv
│   ├── Drift measurements.csv
│   └── Hydraulic actions.csv
└── products/
    ├── profiles/             TS diagrams (per cycle)
    ├── sections/             Temperature/salinity sections
    ├── maps/                 Trajectory maps (with optional GEBCO bathymetry)
    ├── kmz/                  Google Earth KMZ files
    └── forecast/             Forecast position CSVs + health plots
```

## Dependencies

**Core decoding** (`scripts/argofluxdecoder/`) uses **only Python standard library** — no external packages needed.

**Visualization and products** require:

| Package | Purpose |
|---------|---------|
| `numpy` | Numerical arrays |
| `pandas` | Data processing |
| `matplotlib` | Plots and figures |
| `gsw` | TEOS-10 seawater equations (density for TS diagrams) |
| `simplekml` | Google Earth KMZ files |
| `pyproj` | UTM projections for forecast position |
| `xarray` | NetCDF reading (for GEBCO bathymetry) |
| `netCDF4` | NetCDF backend |

Install with: `pip install -r requirements.txt`

## Architecture

```
Argo_SBD_Decoder_Products/
├── app/gui.py                        Python tkinter GUI
├── config/nke_to_decoder_id.json     Float type → decoder ID mapping (51 entries)
├── scripts/
│   ├── argofluxdecoder/              Pure Python SBD decoder package
│   │   ├── core/                     bit_utils, sbd_reader, time_utils
│   │   ├── floats/
│   │   │   ├── base.py              BaseDecoder ABC + data classes
│   │   │   ├── registry.py          Decoder ID → class mapping (87 IDs)
│   │   │   ├── nke/                 NKE 2xx decoders (100-byte frames)
│   │   │   ├── nke_sbd2/            NKE SBD2 decoders (140-byte frames)
│   │   │   ├── nke_pfv2/            NKE PFV2 (SBD fragment → .hex)
│   │   │   ├── nova/                NOVA/DOVA decoders
│   │   │   └── apex/                APEX APF9 + APF11 decoders
│   │   └── output/                  CSV writer
│   ├── decode_sbd_batch.py           CLI batch decoder
│   ├── download_sbd_imap.py          IMAP SBD downloader
│   ├── generate_quicklook_products.py   TS diagrams, sections, maps
│   └── generate_navigation_products.py  Forecast position, KMZ, health plots
├── launch.sh                         Linux/macOS launcher
├── Launch_Argo_SBD_Decoder.bat       Windows launcher
└── requirements.txt
```

## References and credits

### Decoder

The SBD decoder is a Python translation of the **Coriolis data processing chain for Argo floats**:

> Rannou Jean-Philippe, Carval Thierry, Fontaine Laure, Bernard Vincent, Coatanoan Christine (2025). Coriolis Argo floats data processing chain. SEANOE. https://doi.org/10.17882/45589

### Bathymetry

GEBCO Compilation Group (2024). *GEBCO 2024 Grid.* DOI: [10.5285/1c44ce99-0a0d-5f4f-e063-7086abc0ea0f](https://doi.org/10.5285/1c44ce99-0a0d-5f4f-e063-7086abc0ea0f)

Available at: https://www.gebco.net/data_and_products/gridded_bathymetry_data/

### Contributors

- [@ldiaz-barroso](https://github.com/ldiaz-barroso) (SOCIB)
- [@Alberto-GS](https://github.com/Alberto-GS) (IEO-CSIC)
- [@maucarranza](https://github.com/maucarranza) (SOCIB)

## This software is developed by:

<p align="center">
  <a href="https://www.argoespana.es"><img src="./assets/logo_argo_espana.png" height="100" alt="Argo España"></a>
</p>

<p align="center">
  <a href="https://www.socib.es"><img src="./assets/logo_socib.png" height="60" alt="SOCIB"></a>
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://ieo.csic.es"><img src="./assets/logo_ieo_csic.png" height="60" alt="IEO-CSIC"></a>
</p>

## Funding

<p align="center">
  <a href="https://www.euro-argo.eu/EU-Projects/Euro-Argo-ONE-2025-2027"><img src="./assets/logo_euro_argo_one.png" height="80" alt="Euro-Argo ONE"></a>
</p>

This software has received funding from the **Euro-Argo ONE** project (Grant Agreement No. 101188133).

## License

[MIT License](LICENSE)
