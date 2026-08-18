"""
csv_writer.py
=============
Write decoded float data to CSV files compatible with the NKE parser output.

Produces:
  - Technical Message.csv
  - Ascent profile CTD Message.csv
  - Descent profile CTD Message.csv (if data exists)
  - Drift measurements.csv (if data exists)
  - Hydraulic actions.csv (if data exists)
"""

import csv
import math
from pathlib import Path
from typing import List

from ..floats.base import DecodedCycle


def write_technical_csv(cycles: List[DecodedCycle], outdir: Path, filename: str = "Technical Message.csv") -> Path:
    """
    Write technical message CSV.

    Format matches NKE parser output:
    Cycle;Hour;Minute;Second;Latitude;Longitude;...
    """
    outpath = outdir / filename
    outdir.mkdir(parents=True, exist_ok=True)

    with open(outpath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")

        # Header
        writer.writerow([
            "Cycle number", "Date", "Hour", "Minute", "Second",
            "Latitude", "Longitude",
            "GPS valid", "Pressure offset (dbar)",
            "Internal pressure (mbar)",
            "Has profile",
            "SBD file",
        ])

        for cycle in cycles:
            # Determine if this cycle has actual CTD profile data
            cycle_has_profile = len(cycle.ctd_ascent) > 0 or len(cycle.ctd_descent) > 0

            # Write one row per GPS fix in this cycle
            if cycle.gps_fixes:
                for gps in cycle.gps_fixes:
                    date_str = gps.date.strftime("%Y-%m-%d %H:%M:%S") if gps.date else ""
                    hour_str = str(gps.date.hour) if gps.date else ""
                    min_str = str(gps.date.minute) if gps.date else ""
                    sec_str = str(gps.date.second) if gps.date else ""

                    # Get internal pressure from technical data if available
                    int_pres = ""
                    pres_offset = ""
                    for tech in cycle.technical:
                        d = tech.data
                        if d.get("internal_pressure_mbar"):
                            int_pres = _fmt(d["internal_pressure_mbar"])
                        if d.get("pressure_offset"):
                            pres_offset = _fmt(d["pressure_offset"])

                    sbd_file = ""
                    for tech in cycle.technical:
                        if tech.data.get("sbd_file"):
                            sbd_file = tech.data["sbd_file"]
                            break

                    writer.writerow([
                        cycle.cycle,
                        date_str,
                        hour_str,
                        min_str,
                        sec_str,
                        _fmt_coord(gps.latitude),
                        _fmt_coord(gps.longitude),
                        1 if gps.valid else 0,
                        pres_offset,
                        int_pres,
                        1 if cycle_has_profile else 0,
                        sbd_file,
                    ])
            else:
                # Cycle with no GPS — still write technical data if available
                for tech in cycle.technical:
                    d = tech.data
                    if d.get("packet_type") in ("tech2", "param1", "param2"):
                        continue

                    float_time = d.get("float_time")
                    date_str = float_time.strftime("%Y-%m-%d %H:%M:%S") if float_time else ""
                    hour_str = str(float_time.hour) if float_time else ""
                    min_str = str(float_time.minute) if float_time else ""
                    sec_str = str(float_time.second) if float_time else ""

                    writer.writerow([
                        cycle.cycle,
                        date_str,
                        hour_str,
                        min_str,
                        sec_str,
                        _fmt_coord(d.get("gps_lat")),
                        _fmt_coord(d.get("gps_lon")),
                        1 if d.get("gps_valid") else 0,
                        _fmt(d.get("pressure_offset")),
                        _fmt(d.get("internal_pressure_mbar")),
                        1 if cycle_has_profile else 0,
                        d.get("sbd_file", ""),
                    ])

    return outpath


def write_ctd_csv(cycles: List[DecodedCycle], outdir: Path,
                  direction: str = "ascent",
                  filename: str = "Ascent profile CTD Message.csv") -> Path:
    """
    Write CTD profile CSV.

    Format matches NKE parser output with added Date column for plotting.
    """
    outpath = outdir / filename
    outdir.mkdir(parents=True, exist_ok=True)

    with open(outpath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")

        writer.writerow([
            "", "Cycle number", "Date",
            "CTD - Pressure (dbar)",
            "CTD - Temperature (degC)",
            "CTD - Salinity (PSU)",
        ])

        for cycle in cycles:
            if direction == "ascent":
                measurements = cycle.ctd_ascent
            elif direction == "descent":
                measurements = cycle.ctd_descent
            else:
                measurements = cycle.ctd_drift

            # Get cycle date from GPS fix or technical data
            cycle_date = ""
            if cycle.gps_fixes:
                gps = cycle.gps_fixes[0]
                if gps.date:
                    cycle_date = gps.date.strftime("%Y-%m-%d %H:%M:%S")
            if not cycle_date:
                for tech in cycle.technical:
                    d = tech.data
                    if d.get("float_time"):
                        cycle_date = d["float_time"].strftime("%Y-%m-%d %H:%M:%S")
                        break
                    if d.get("sbd_file") and not cycle_date:
                        # Extract date from SBD filename YYYYMMDD_...
                        parts = d["sbd_file"].split("_")
                        if parts and len(parts[0]) == 8:
                            try:
                                from datetime import datetime
                                dt = datetime.strptime(parts[0], "%Y%m%d")
                                cycle_date = dt.strftime("%Y-%m-%d")
                            except ValueError:
                                pass

            for m in measurements:
                if math.isnan(m.pressure_dbar):
                    continue

                writer.writerow([
                    "",
                    m.cycle,
                    cycle_date,
                    _fmt(m.pressure_dbar),
                    _fmt(m.temperature_degc),
                    _fmt(m.salinity_psu),
                ])

    return outpath


def write_hydraulic_csv(cycles: List[DecodedCycle], outdir: Path,
                        filename: str = "Hydraulic actions.csv") -> Path:
    """Write hydraulic actions CSV."""
    outpath = outdir / filename
    outdir.mkdir(parents=True, exist_ok=True)

    has_data = any(len(c.hydraulics) > 0 for c in cycles)
    if not has_data:
        return outpath

    with open(outpath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Cycle number", "Action type", "Pressure (dbar)", "Duration (s)"])

        for cycle in cycles:
            for h in cycle.hydraulics:
                writer.writerow([
                    h.cycle,
                    h.action_type,
                    _fmt(h.pressure_dbar),
                    h.duration_sec,
                ])

    return outpath


def write_all_csvs(cycles: List[DecodedCycle], outdir: Path) -> List[Path]:
    """
    Write all output CSVs for a decoded float.

    Returns list of created file paths.
    """
    outdir = Path(outdir)
    created = []

    created.append(write_technical_csv(cycles, outdir))
    created.append(write_ctd_csv(cycles, outdir, "ascent", "Ascent profile CTD Message.csv"))

    # Only write descent/drift if data exists
    if any(len(c.ctd_descent) > 0 for c in cycles):
        created.append(write_ctd_csv(cycles, outdir, "descent", "Descent profile CTD Message.csv"))

    if any(len(c.ctd_drift) > 0 for c in cycles):
        created.append(write_ctd_csv(cycles, outdir, "drift", "Drift measurements.csv"))

    if any(len(c.hydraulics) > 0 for c in cycles):
        created.append(write_hydraulic_csv(cycles, outdir))

    return created


def _fmt(val) -> str:
    """Format a numeric value for CSV output."""
    if val is None:
        return ""
    if isinstance(val, float):
        if math.isnan(val):
            return ""
        return f"{val:.3f}"
    return str(val)


def _fmt_coord(val) -> str:
    """Format a GPS coordinate with full precision (at least 10 decimal places)."""
    if val is None:
        return ""
    if isinstance(val, float):
        if math.isnan(val):
            return ""
        return f"{val:.10f}".rstrip("0").rstrip(".")
    return str(val)
