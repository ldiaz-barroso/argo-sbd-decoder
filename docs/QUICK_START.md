# Argo SBD Decoder — Quick Start Guide

---

## Slide 1: What is this?

**Argo SBD Decoder** decodes Iridium SBD data from Argo profiling floats and generates quick-look products.

- No proprietary software needed
- Works on Windows, macOS and Linux
- Supports 87 float types across all major manufacturers
- Based on the Coriolis MATLAB decoder (DOI: 10.17882/45589)

---

## Slide 2: Installation

### Windows
1. Unzip `Argo_SBD_Decoder_v5.0.0.zip`
2. Double-click `Launch_Argo_SBD_Decoder.bat`
3. Click "Install deps" in the GUI

### macOS / Linux
```bash
unzip Argo_SBD_Decoder_v5.0.0.zip
cd Argo_SBD_Decoder_Products
./launch.sh install
python3 app/gui.py
```

---

## Slide 3: Step 1 — Select your float type

- Open the GUI
- Select your float from the dropdown (e.g., `ARVOR_ARN_ICE_5900A03_5900A04 (v5.45)`)
- Enter the IMEI (15-digit Iridium number)
- Select the root folder where SBD files are stored

---

## Slide 4: Step 2 — Download SBD files (optional)

If you need to download SBDs from your email:

- Select your **email provider** from the dropdown (Gmail, Outlook/Hotmail, Yahoo, iCloud, or Other)
  - Server, port and IMAP folder are auto-filled based on your selection
  - If you choose "Other", enter the IMAP server and port manually
- Enter your email address and App Password
- Enter the IMEI of your float
- Set the **date range** using the calendar date pickers (From / Until)
- Verify the **sender** field (default: `sbdservice@sbd.iridium.com`)
- Click **Download SBDs**

The files will be saved to `<root folder>/sbd_raw/`

> **Note:** You need an App Password (not your regular password).
> - **Gmail:** https://myaccount.google.com/apppasswords
> - **Outlook:** https://account.microsoft.com/security → App Passwords
> - **Yahoo:** https://login.yahoo.com/account/security → App Password
> - **iCloud:** https://appleid.apple.com → App-Specific Passwords

---

## Slide 5: Step 3 — Decode

- Click **Decode SBDs**
- The decoder reads all `.sbd` files and creates CSV tables in `<root>/decoded/`

Output files:
- `Technical Message.csv` — GPS positions, timestamps, internal vacuum
- `Ascent profile CTD Message.csv` — Pressure, Temperature, Salinity

---

## Slide 6: Step 4 — Generate products

Click individual buttons or **All products** to run everything:

| Button | Output |
|--------|--------|
| TS + Sections | TS diagram + temperature/salinity sections |
| Map | Trajectory map (profiles = red circles, GPS-only = gray dots) |
| Forecast Position | Forecast position map, KMZ, internal vacuum plot, speed, range |

---

## Slide 7: Output structure

```
<float_root>/
├── sbd_raw/          Raw .sbd files
├── decoded/          Decoded CSV tables
└── products/
    ├── profiles/     TS diagram
    ├── sections/     Temperature/salinity sections
    ├── maps/         Trajectory maps
    ├── kmz/          Google Earth files
    └── forecast/     Forecast position, health plots
```

---

## Slide 8: Preview

The right panel of the GUI shows generated plots in real time.
Use the dropdown to browse all PNG files in the products folder.

---

## Slide 9: CLI usage (macOS/Linux)

```bash
# Full pipeline in one command:
./launch.sh pipeline /path/to/float_folder --decoder_id 211

# Or step by step:
./launch.sh decode --root /path/to/float --decoder_id 211
./launch.sh quicklook --root /path/to/float --outdir /path/to/float/products --imei 300534065460740 --technical_csv "Technical Message.csv"
./launch.sh forecast --root /path/to/float --outdir /path/to/float/products --imei 300534065460740 --technical_csv "Technical Message.csv"
```

---

## Slide 10: Important notes

- **Only Iridium SBD** transmission is supported (not RUDICS or Argos)
- Python 3.8+ required (auto-detected on Windows)
- GEBCO bathymetry (optional): add a `.nc` file for ocean depth on maps
- All processing runs locally — no internet needed after download

---

## Slide 11: Credits

**Developed by:** SOCIB and IEO-CSIC

**Based on:** Rannou Jean-Philippe, Carval Thierry, Fontaine Laure, Bernard Vincent, Coatanoan Christine (2025). Coriolis Argo floats data processing chain. SEANOE. https://doi.org/10.17882/45589

**Funded by:** Euro-Argo ONE project (Grant Agreement No. 101188133)

---
