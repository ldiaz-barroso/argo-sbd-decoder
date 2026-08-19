# Argo SBD Decoder

## What is it?

The **Argo SBD Decoder** is an open-source application that downloads, decodes and visualizes data transmitted by Argo profiling floats via Iridium Short Burst Data (SBD). It provides float operators with immediate access to decoded oceanographic profiles and float engineering data — without any proprietary software.

The application is a direct Python translation of the official **Coriolis data processing chain for Argo floats** (Rannou Jean-Philippe, Carval Thierry, Fontaine Laure, Bernard Vincent, Coatanoan Christine (2025). SEANOE. https://doi.org/10.17882/45589), ensuring full compatibility with the reference decoder used by the international Argo Data Assembly Centres.

## Why does it exist?

Float operators deploying and monitoring Argo floats need to:

1. **Verify float behaviour** immediately after deployment — is it profiling correctly? Is the CTD working?
2. **Monitor float health** over time — internal vacuum, battery, drift speed.
3. **Plan float recoveries** — knowing the precise position of the float at the surface is critical for ship recovery operations.

Previously, this required proprietary software or direct access to the Coriolis MATLAB decoder. Our tool eliminates both dependencies — it is built entirely in Python and runs on any platform.

## Key Features

### SBD Decoding

- Decodes binary SBD files into human-readable CSV tables
- Supports **87 float types** across NKE, APEX and NOVA/DOVA platforms
- Extracts CTD profiles (pressure, temperature, salinity), hydraulic actions, and engineering data
- Sensor conversions verified bit-by-bit against the Coriolis MATLAB decoder (version 085h, July 2026)

### High-Precision GPS Positions (critical for recoveries)

The decoder extracts GPS coordinates with **full manufacturer precision** — 10 decimal places of latitude/longitude, corresponding to sub-meter resolution. This is essential for:

- **Float recovery operations**: when a research vessel needs to locate a surfaced float in the open ocean, even 100 meters of error can mean hours of additional search time. Our decoder preserves the complete GPS resolution transmitted by the float (degrees + minutes + minutes_fraction/10000), avoiding the rounding that generic tools introduce.
- **Accurate trajectory reconstruction**: high-precision positions allow reliable computation of drift speed and bearing, which are used to predict where the float will surface on its next cycle.
- **GPS timestamps**: the exact time of each GPS fix is extracted from the technical packet (to the second), enabling precise speed calculations between consecutive positions.

### Quick-Look Products

- **TS diagrams** with potential density contours (TEOS-10 equation of state via GSW)
- **Temperature/salinity sections** — upper ocean (0–300 dbar) and full depth, showing the temporal evolution across cycles
- **Trajectory maps** with optional GEBCO bathymetry overlay, distinguishing profile positions (red circles) from GPS-only surface fixes (gray dots)
- **KMZ files** for Google Earth visualization of the float track
- **Navigation products**: drift speed, range between fixes, and bearing — with predicted next-surface position

### Float Health Monitoring

- **Internal vacuum (pressure)** time series — the most critical indicator of hull integrity for deep floats
- **Drift speed and range** — detects anomalous behaviour (grounding, strong currents)
- Pressure offset evolution across cycles

### Download from Email (IMAP)

- Downloads SBD attachments from any IMAP email provider (Gmail, Outlook, Yahoo, iCloud, or custom server)
- Select your provider from a dropdown — server, port and folder are auto-configured
- Filters by IMEI, date range and sender
- No need for separate email clients or manual file management

## How it works

```
Email/IMAP  →  Download SBDs  →  Decode  →  Quick-look products
                    │                │              │
              .sbd files      CSV tables       PNG plots
              (binary)       (decoded)        KMZ, maps
```

1. **Download**: SBD binary files are retrieved via IMAP from your email provider (sent by `sbdservice@sbd.iridium.com`)
2. **Decode**: Each 100-byte frame is parsed according to the Coriolis bit layouts. Sensor counts are converted to physical values using the exact formulas from the MATLAB reference.
3. **Visualize**: Decoded data is plotted with matplotlib and exported as maps, sections and navigation products.

## Based on the Coriolis Decoder

The decoding algorithms are a faithful translation of the Coriolis MATLAB decoder, the official reference used by all Argo Data Assembly Centres worldwide:

> Rannou Jean-Philippe, Carval Thierry, Fontaine Laure, Bernard Vincent, Coatanoan Christine (2025). Coriolis Argo floats data processing chain. SEANOE. https://doi.org/10.17882/45589

This ensures that:
- Bit layouts match the manufacturer specifications exactly
- Sensor conversion formulas are identical (pressure, temperature, salinity offsets per decoder group)
- GPS extraction uses the same field positions validated by Coriolis against thousands of operational floats
- New float firmware versions can be added following the same patterns documented in the MATLAB code

## Platform Support

| Platform | How to run |
|----------|-----------|
| **Windows** | Double-click `ArgoSBDDecoder.exe` (standalone, no installation) |
| **macOS** | Open `ArgoSBDDecoder.dmg` or `python3 app/gui.py` |
| **Linux** | Run `./ArgoSBDDecoder` (standalone) or `python3 app/gui.py` |

The graphical interface (built with Python tkinter) is identical on all platforms and includes a real-time plot preview panel.

## Supported Float Types

87 decoder IDs across 5 platforms, covering all Argo floats with Iridium SBD transmission:

- **ARVOR-ARN / Ice** (decoder IDs 210-212, 217, 222-227, 231, 232) — standard Argo floats with ice detection
- **ARVOR-Deep** (IDs 201-203, 215, 216, 218, 221, 228-230) — deep-capable floats profiling to 3500-4000m
- **ARVOR-C** (IDs 219, 220) — coastal floats with shallower profiles
- **PROVOR DO** (IDs 205, 206, 209, 213, 214, 225) — floats with dissolved oxygen sensors

The float type selector in the GUI shows the exact firmware version and family name for each supported float.

**Important:** Only Iridium SBD transmission is supported (100-byte fixed frames). Floats using RUDICS or Argos transmission are not covered.

## Who is it for?

- **Float operators** monitoring recently deployed floats
- **Recovery teams** planning ship operations to retrieve floats
- **Technical staff** verifying float programming and behaviour
- **Researchers** who need rapid access to profile data without waiting for DAC processing
- **Training** — understanding how Argo float data is transmitted and decoded

## Credits

Developed by **SOCIB** and **IEO-CSIC** within the **Euro-Argo ONE** project.

This software has received funding from the **Euro-Argo ONE** project (Grant Agreement No. 101188133).

Based on: Rannou Jean-Philippe, Carval Thierry, Fontaine Laure, Bernard Vincent, Coatanoan Christine (2025). Coriolis Argo floats data processing chain. SEANOE. https://doi.org/10.17882/45589

## License

MIT License.
