#!/usr/bin/env python3
"""
generate_navigation_products.py

Navigation and forecast position products for Argo float Technical Message.csv files.

Reads Technical Message.csv files (both NKE transposed format and Python decoder
flat format), extracts GPS and health/status data, computes range/heading/speed
and predicted position, and creates:
- navigation summary CSV
- raw navigation CSV
- KMZ with gx:Track, placemarks and prediction
- map plot
- time-series plots for health/navigation variables

Designed for:
- normal mission mode
- End-of-Life mode
- multiple Technical Message files per day
- multiple GPS fixes per Technical Message

GPS parsing mode:
- always all valid GPS fixes from all useful columns.
- no last_per_message / last_per_day reduction is available.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

try:
    from common import load_settings, find_date_folders, discover_raw_sbd_dates, default_dataset_date
except Exception:
    def load_settings(*_, **__):
        return {}

    def find_date_folders(root: Path):
        return sorted([
            p for p in root.rglob("*")
            if p.is_dir() and p.name.isdigit() and len(p.name) == 8
        ])


def safe_float(x) -> float:
    s = str(x).strip().strip('"').replace(",", ".")
    if s == "" or s.lower() in ("nan", "none", "null"):
        return np.nan
    try:
        return float(s)
    except Exception:
        return np.nan


def parse_folder_date(date_text: str):
    """
    Strict YYYYMMDD only.

    We intentionally do NOT interpret DDMMYYYY because it is ambiguous and
    can silently reorder the trajectory. If no YYYYMMDD folder is found, the
    record is still included, but timestamp is left empty and sorting falls
    back to file path.
    """
    s = str(date_text).strip()
    if s.isdigit() and len(s) == 8 and 1900 <= int(s[:4]) <= 2100:
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    return pd.NaT


def read_semicolon_csv_raw(path: Path) -> pd.DataFrame:
    """
    Read a semicolon-separated NKE Technical Message preserving ALL columns.

    Some Technical Message.csv files are ragged: early rows may have fewer
    semicolon-separated fields than later GPS rows. pandas.read_csv can hide or
    fail on that structure depending on the file. The csv module lets us pad each
    row to the maximum row width, so every original data column remains available
    to the GPS parser.
    """
    last_error = None
    for encoding in ("latin-1", "utf-8-sig", "utf-8"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                rows = list(csv.reader(f, delimiter=";"))
            if not rows:
                return pd.DataFrame()
            max_width = max(len(row) for row in rows)
            padded = [row + [""] * (max_width - len(row)) for row in rows]
            return pd.DataFrame(padded)
        except Exception as exc:
            last_error = exc
    raise last_error


def infer_date_from_path(path: Path) -> str:
    """
    Return nearest YYYYMMDD folder/file prefix only.

    This deliberately ignores DDMMYYYY-like names.
    """
    for parent in [path.parent, *path.parents]:
        name = parent.name
        if name.isdigit() and len(name) == 8 and 1900 <= int(name[:4]) <= 2100:
            return name

    if path.name[:8].isdigit() and 1900 <= int(path.name[:4]) <= 2100:
        return path.name[:8]

    return "unknown"


def resolve_file_date(path: Path, root: Path) -> str:
    d = infer_date_from_path(path)
    return default_dataset_date(root) if d == "unknown" else d


def batch_message_times(df: pd.DataFrame, root: Path, n_source_columns: int) -> list[pd.Timestamp]:
    """Timestamp flat-batch columns from raw SBD dates and hour rollovers."""
    hours = row_raw_values(df, "Float's hour")
    minutes = row_raw_values(df, "Float's minute")
    seconds = row_raw_values(df, "Float's second")
    dates = discover_raw_sbd_dates(root)
    base = pd.to_datetime(dates[0], format="%Y%m%d", errors="coerce") if dates else pd.NaT
    out = []
    day_offset = 0
    previous_seconds = None
    for idx in range(n_source_columns):
        h = raw_at(hours, idx, np.nan)
        m = raw_at(minutes, idx, np.nan)
        sec = raw_at(seconds, idx, 0)
        if pd.isna(base) or not np.isfinite(h) or not np.isfinite(m):
            out.append(pd.NaT)
            continue
        current_seconds = int(h) * 3600 + int(m) * 60 + int(sec if np.isfinite(sec) else 0)
        if previous_seconds is not None and current_seconds < previous_seconds - 6 * 3600:
            day_offset += 1
        previous_seconds = current_seconds
        out.append(base + pd.Timedelta(days=day_offset, seconds=current_seconds))
    return out


def find_technical_files(root: Path, technical_csv: str = "Technical Message.csv") -> list[Path]:
    """Find one coherent set of Technical Message files.

    Batch and daily copies are never mixed. Mixing both layouts duplicates fixes
    and can make the track appear to return from its last point to its first.
    """
    root = Path(root).expanduser().resolve()
    decoded = root / "decoded"
    batch = decoded / "batch"
    raw = root / "sbd_raw"

    def collect(base: Path) -> list[Path]:
        if not base.exists():
            return []
        return (
            list(base.rglob("Technical Message*.csv"))
            + list(base.rglob("Technical message*.csv"))
        )

    batch_files = collect(batch)
    decoded_files = collect(decoded) if not batch_files else []
    raw_files = collect(raw) if not batch_files and not decoded_files else []

    if batch_files:
        files, mode = batch_files, "batch"
    elif decoded_files:
        files, mode = decoded_files, "decoded"
    elif raw_files:
        files, mode = raw_files, "sbd_raw"
    else:
        files, mode = [], "daily"
        for day in find_date_folders(root):
            files.extend(collect(day))

    def rank(p: Path):
        n = p.name.lower()
        if n == Path(technical_csv).name.lower():
            return 0
        if "message 1" in n or "message_1" in n:
            return 1
        if "message 2" in n or "message_2" in n:
            return 2
        return 9

    out = []
    seen = set()
    for p in sorted(files, key=lambda x: (rank(x), str(x).lower())):
        key = str(p.resolve()).lower()
        if key not in seen:
            out.append(p)
            seen.add(key)

    print(f"TECHNICAL_SEARCH mode={mode} files={len(out)}")
    return out


# ---------------------------------------------------------------------
# Column-wise NKE Technical Message parsing
# ---------------------------------------------------------------------
# The Technical Message.csv used by NKE ARVOR files is best interpreted
# column by column:
#   column 0 = labels
#   each column >= 1 = potential GPS/navigation fix
#
# Do NOT assume value/flag pairs for navigation. Some files contain
# profile columns, GPS-only surface columns and zero/separator columns.

GPS_LABELS = {
    "lat_deg": [
        "GPS latitude in degrees",
        "GPS latitude (°)",
    ],
    "lat_min": [
        "GPS latitude in minutes",
        "GPS latitude (minutes)",
    ],
    "lat_frac": [
        "GPS latitude in minutes fractions",
        "GPS latitude (minutes fractions",
    ],
    "lat_orient": [
        "GPS latitude orientation",
    ],
    "lon_deg": [
        "GPS longitude in degrees",
        "GPS longitude (°)",
    ],
    "lon_min": [
        "GPS longitude in minutes",
        "GPS longitude (minutes)",
    ],
    "lon_frac": [
        "GPS longitude in minutes fractions",
        "GPS longitude (minutes fractions",
    ],
    "lon_orient": [
        "GPS longitude orientation",
    ],
    "valid_fix": [
        "GPS valid fix",
    ],
}


CORE_PROFILE_TIMING_LABELS = [
    "Cycle start time",
    "Descent start time",
    "End of descent time",
    "Ascent start time",
    "Time at end of ascent",
]


def raw_label_text(x) -> str:
    return str(x).strip().strip('"')


def find_row_index(df: pd.DataFrame, label_contains) -> int | None:
    """Find a row by one or more case-insensitive label fragments."""
    if df.empty:
        return None
    candidates = label_contains if isinstance(label_contains, (list, tuple)) else [label_contains]
    labels = df.iloc[:, 0].astype(str).str.strip().str.strip('"').str.lower()
    for candidate in candidates:
        wanted = str(candidate).lower()
        mask = labels.str.contains(wanted, regex=False, na=False)
        if mask.any():
            return int(mask[mask].index[0])
    return None

def row_raw_values(df: pd.DataFrame, label_contains) -> list[float]:
    """
    Return values after the label preserving the original CSV columns.
    No value/flag skipping is applied.
    """
    idx = find_row_index(df, label_contains)
    if idx is None:
        return []
    return [safe_float(v) for v in df.loc[idx].iloc[1:].values]


def raw_at(values: list[float], idx0: int, default=np.nan) -> float:
    return values[idx0] if idx0 < len(values) else default


def decimal_from_nke_gps(deg, minutes, minute_fraction_4th, orientation) -> float:
    """degrees + (minutes + fraction/10000)/60; orientation 1 gives negative sign."""
    if not (np.isfinite(deg) and np.isfinite(minutes)):
        return np.nan

    frac = 0.0 if not np.isfinite(minute_fraction_4th) else float(minute_fraction_4th) / 10000.0
    val = float(deg) + (float(minutes) + frac) / 60.0

    if np.isfinite(orientation) and int(orientation) == 1:
        val = -val

    return val


def parse_gps_fixes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parse GPS fixes column by column.

    Every original CSV column after the label is a potential fix. A fix is kept
    when the lat/lon reconstructed from that same column is valid and non-zero.
    """
    lat_deg = row_raw_values(df, GPS_LABELS["lat_deg"])
    lat_min = row_raw_values(df, GPS_LABELS["lat_min"])
    lat_frac = row_raw_values(df, GPS_LABELS["lat_frac"])
    lat_ori = row_raw_values(df, GPS_LABELS["lat_orient"])

    lon_deg = row_raw_values(df, GPS_LABELS["lon_deg"])
    lon_min = row_raw_values(df, GPS_LABELS["lon_min"])
    lon_frac = row_raw_values(df, GPS_LABELS["lon_frac"])
    lon_ori = row_raw_values(df, GPS_LABELS["lon_orient"])
    valid_fix = row_raw_values(df, GPS_LABELS["valid_fix"])

    n_cols = max(
        len(lat_deg), len(lat_min), len(lat_frac), len(lat_ori),
        len(lon_deg), len(lon_min), len(lon_frac), len(lon_ori),
    )
    records = []
    gps_fix_index = 0

    for source_idx0 in range(n_cols):
        vf = raw_at(valid_fix, source_idx0, np.nan)
        if np.isfinite(vf) and int(vf) != 1:
            continue

        lat = decimal_from_nke_gps(
            raw_at(lat_deg, source_idx0),
            raw_at(lat_min, source_idx0),
            raw_at(lat_frac, source_idx0, 0),
            raw_at(lat_ori, source_idx0, 0),
        )
        lon = decimal_from_nke_gps(
            raw_at(lon_deg, source_idx0),
            raw_at(lon_min, source_idx0),
            raw_at(lon_frac, source_idx0, 0),
            raw_at(lon_ori, source_idx0, 0),
        )

        if not (np.isfinite(lat) and np.isfinite(lon)):
            continue
        if abs(lat) <= 1e-9 or abs(lon) <= 1e-9:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        gps_fix_index += 1
        records.append({
            "gps_fix_index": gps_fix_index,
            "source_column_index": source_idx0 + 1,  # 1-based after label column
            "source_csv_column": source_idx0 + 2,    # 1-based spreadsheet column including label column
            "lat": lat,
            "lon": lon,
        })

    return pd.DataFrame(records)


def classify_fix_from_cycle_timing(df: pd.DataFrame, source_idx0: int) -> tuple[str, bool, int, dict]:
    """
    Classify a GPS fix using the cycle timing values in the SAME original CSV column.

    profile:
        all core timing rows exist and are non-zero in that same column.

    gps_surface_only:
        GPS exists, but core cycle timing is zero/incomplete in that column.
    """
    timing = {}
    nonzero_count = 0

    # Count all rows inside CYCLE TIMING for diagnostics.
    labels = df.iloc[:, 0].astype(str).str.strip().str.strip('"')
    start_candidates = [i for i, label in enumerate(labels) if "CYCLE TIMING" in label.upper()]
    if start_candidates:
        start = start_candidates[0]
        end = len(labels)
        for j in range(start + 1, len(labels)):
            if str(labels.iloc[j]).startswith("-----"):
                end = j
                break
        for row_i in range(start + 1, end):
            val = safe_float(df.iloc[row_i, source_idx0 + 1]) if source_idx0 + 1 < df.shape[1] else np.nan
            if np.isfinite(val) and abs(val) > 1e-9:
                nonzero_count += 1

    core_values = []
    for label in CORE_PROFILE_TIMING_LABELS:
        values = row_raw_values(df, label)
        val = raw_at(values, source_idx0, np.nan)
        timing_key = (
            label.lower()
            .replace(" ", "_")
            .replace("(", "")
            .replace(")", "")
            .replace("/", "_")
        )
        timing[timing_key] = val
        core_values.append(val)

    # A genuine profile column contains a populated CYCLE TIMING block.
    # Do not require every named field to be non-zero: midnight and elapsed-time
    # fields can legitimately be zero, and label wording varies between NKE
    # decoder versions. GPS-only columns have an empty/zero timing block.
    finite_core_count = sum(np.isfinite(v) for v in core_values)
    nonzero_core_count = sum(np.isfinite(v) and abs(v) > 1e-9 for v in core_values)
    has_profile = (nonzero_count >= 3) or (finite_core_count >= 4 and nonzero_core_count >= 2)
    record_type = "profile" if has_profile else "gps_surface_only"

    return record_type, bool(has_profile), nonzero_count, timing


def parse_message_times(df: pd.DataFrame, date_folder: str, n_source_columns: int, root: Path | None = None) -> list[pd.Timestamp]:
    """Build one timestamp per source column, preserving day changes."""
    years = row_raw_values(df, ["Float time : Year", "Float's year"])
    months = row_raw_values(df, ["Float time : Month", "Float's month"])
    days = row_raw_values(df, ["Float time : Day", "Float's day"])
    hours = row_raw_values(df, ["Float time : Hour", "Float's hour"])
    minutes = row_raw_values(df, ["Float time : Minute", "Float's minute"])
    seconds = row_raw_values(df, ["Float time : Second", "Float's second"])

    raw_dates = discover_raw_sbd_dates(root) if root is not None else []
    base = (pd.to_datetime(raw_dates[0], format="%Y%m%d", errors="coerce")
            if raw_dates else parse_folder_date(date_folder))

    out = []
    day_offset = 0
    previous_clock = None
    for source_idx0 in range(n_source_columns):
        y = raw_at(years, source_idx0, np.nan)
        mo = raw_at(months, source_idx0, np.nan)
        d = raw_at(days, source_idx0, np.nan)
        h = raw_at(hours, source_idx0, np.nan)
        mi = raw_at(minutes, source_idx0, np.nan)
        sec = raw_at(seconds, source_idx0, 0)

        if all(np.isfinite(v) for v in [y, mo, d, h, mi]):
            year = int(y) + (2000 if int(y) < 100 else 0)
            try:
                stamp = pd.Timestamp(year=year, month=int(mo), day=int(d),
                                     hour=int(h), minute=int(mi),
                                     second=int(sec if np.isfinite(sec) else 0))
                out.append(stamp)
                previous_clock = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
                continue
            except Exception:
                pass

        if pd.notna(base) and np.isfinite(h) and np.isfinite(mi):
            clock = int(h) * 3600 + int(mi) * 60 + int(sec if np.isfinite(sec) else 0)
            if previous_clock is not None and clock < previous_clock - 6 * 3600:
                day_offset += 1
            previous_clock = clock
            out.append(base + pd.Timedelta(days=day_offset, seconds=clock))
        else:
            out.append(pd.NaT)

    return out

def extract_health(df: pd.DataFrame, n_source_columns: int) -> dict[str, list[float]]:
    """Health/status values by original source column."""
    labels = {
        "battery_voltage_drop_at_pmax": [
            "Battery voltage drop at Pmax",
            "Batteries voltage at Pmax",
        ],
        "internal_pressure_mbar": ["Internal pressure"],
        "internal_temperature_degC": ["Internal temperature"],
        "descent_speed_mbar_sec": ["Descent speed"],
        "ascent_speed_mbar_sec": ["Ascent speed"],
        "defect_mode": ["Defect mode"],
        "number_of_ascent_messages": [
            "Number of ascent messages",
            "Number of ascent CTDO messages",
        ],
    }

    out = {}
    for key, label in labels.items():
        raw = row_raw_values(df, label)
        out[key] = [raw_at(raw, source_idx0, np.nan) for source_idx0 in range(n_source_columns)]

    return out


def excel_column_name(one_based_index: int) -> str:
    out = ""
    n = one_based_index
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _try_parse_flat_technical(df: pd.DataFrame, imei: str, date_folder: str, path: Path):
    """
    Try to parse a Python decoder "flat" Technical Message CSV.

    Format: Cycle number;Hour;Minute;Second;Latitude;Longitude;GPS valid;Pressure offset (dbar);SBD file
    One row per cycle, with decimal Latitude/Longitude already computed.

    Returns a DataFrame of navigation records, or None if format doesn't match.
    """
    if df.empty or df.shape[0] < 2:
        return None

    # Check if the first row is a header containing 'Latitude' and 'Longitude'
    header_row = [str(c).strip().lower() for c in df.iloc[0].values]
    lat_col = None
    lon_col = None
    cycle_col = None
    gps_valid_col = None
    sbd_file_col = None
    date_col = None
    internal_pres_col = None
    has_profile_col = None

    for i, h in enumerate(header_row):
        if "latitude" in h:
            lat_col = i
        elif "longitude" in h:
            lon_col = i
        elif "cycle" in h:
            cycle_col = i
        elif "gps valid" in h or "gps_valid" in h:
            gps_valid_col = i
        elif "sbd file" in h or "sbd_file" in h:
            sbd_file_col = i
        elif h == "date":
            date_col = i
        elif "internal pressure" in h or "internal_pressure" in h:
            internal_pres_col = i
        elif "has profile" in h or "has_profile" in h:
            has_profile_col = i

    if lat_col is None or lon_col is None:
        return None  # Not the flat format

    rows = []
    gps_fix_index = 0

    for row_idx in range(1, df.shape[0]):
        row = df.iloc[row_idx]

        lat = safe_float(row.iloc[lat_col]) if lat_col < len(row) else np.nan
        lon = safe_float(row.iloc[lon_col]) if lon_col < len(row) else np.nan

        if not (np.isfinite(lat) and np.isfinite(lon)):
            continue
        if abs(lat) < 1e-9 and abs(lon) < 1e-9:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        # Check GPS valid flag if present
        if gps_valid_col is not None and gps_valid_col < len(row):
            gps_valid = safe_float(row.iloc[gps_valid_col])
            if np.isfinite(gps_valid) and int(gps_valid) != 1:
                continue

        gps_fix_index += 1

        cycle_num = 0
        if cycle_col is not None and cycle_col < len(row):
            cn = safe_float(row.iloc[cycle_col])
            if np.isfinite(cn):
                cycle_num = int(cn)

        # Determine if this fix has an associated CTD profile
        has_profile_val = False
        if has_profile_col is not None and has_profile_col < len(row):
            hp = safe_float(row.iloc[has_profile_col])
            has_profile_val = bool(np.isfinite(hp) and int(hp) == 1)
        else:
            # Fallback: assume all cycle > 0 are profiles (legacy CSVs without column)
            has_profile_val = cycle_num > 0

        # Extract timestamp from Date column or SBD filename
        # If the date has hour 00:00:00 and an SBD filename is available,
        # use the MOMSN from the filename as a sub-day ordering proxy.
        timestamp = pd.NaT
        if date_col is not None and date_col < len(row):
            date_str = str(row.iloc[date_col]).strip()
            if date_str:
                try:
                    timestamp = pd.to_datetime(date_str)
                except (ValueError, TypeError):
                    pass

        # If timestamp has midnight (no real hour) and we have an SBD filename,
        # extract MOMSN to create a unique sub-day timestamp
        if pd.notna(timestamp) and timestamp.hour == 0 and timestamp.minute == 0 and timestamp.second == 0:
            if sbd_file_col is not None and sbd_file_col < len(row):
                sbd_name = str(row.iloc[sbd_file_col]).strip()
                parts = sbd_name.split("_")
                if len(parts) >= 2:
                    try:
                        momsn = int(parts[1])
                        # Use MOMSN as minutes offset within the day (wraps at 1440)
                        # This gives unique timestamps for ordering within the same day
                        minutes_offset = momsn % 1440
                        timestamp = timestamp.replace(
                            hour=minutes_offset // 60,
                            minute=minutes_offset % 60,
                            second=0
                        )
                    except (ValueError, IndexError):
                        pass

        if pd.isna(timestamp) and sbd_file_col is not None and sbd_file_col < len(row):
            sbd_name = str(row.iloc[sbd_file_col]).strip()
            if sbd_name and len(sbd_name) >= 8:
                try:
                    timestamp = pd.to_datetime(sbd_name[:8], format="%Y%m%d")
                    parts = sbd_name.split("_")
                    if len(parts) >= 2:
                        momsn = int(parts[1])
                        minutes_offset = momsn % 1440
                        timestamp = timestamp.replace(
                            hour=minutes_offset // 60,
                            minute=minutes_offset % 60
                        )
                except (ValueError, TypeError):
                    pass

        rows.append({
            "imei": imei,
            "date_folder": date_folder,
            "source_file": str(path),
            "gps_fix_index": gps_fix_index,
            "source_column_index": cycle_num,
            "source_excel_column": "",
            "source_csv_column": row_idx + 1,
            "n_gps_fixes_in_message": 0,  # filled later
            "timestamp": timestamp,
            "timestamp_string": "" if pd.isna(timestamp) else pd.Timestamp(timestamp).strftime("%Y-%m-%dT%H:%M:%S"),
            "lat": lat,
            "lon": lon,
            "gps_mode": "flat_python_decoder",
            "record_type": "profile" if has_profile_val else "gps_surface_only",
            "has_profile": has_profile_val,
            "is_gps_only": not has_profile_val,
            "cycle_timing_nonzero_count": 0,
            "is_end_of_life_candidate": False,
            "internal_pressure_mbar": safe_float(row.iloc[internal_pres_col]) if internal_pres_col is not None and internal_pres_col < len(row) else np.nan,
        })

    if not rows:
        return None

    result = pd.DataFrame(rows)
    result["n_gps_fixes_in_message"] = len(result)
    return result


def parse_one_technical(path: Path, imei: str, root: Path) -> pd.DataFrame:
    df = read_semicolon_csv_raw(path)
    date_folder = resolve_file_date(path, root)

    # ── Try the Python decoder "flat" format first ──
    # Format: Cycle number;Hour;Minute;Second;Latitude;Longitude;GPS valid;...
    # Detect by checking if the first row looks like a column header with "Latitude"
    flat_records = _try_parse_flat_technical(df, imei, date_folder, path)
    if flat_records is not None and not flat_records.empty:
        return flat_records

    # ── Fall back to NKE transposed format ──
    gps = parse_gps_fixes(df)

    if gps.empty:
        return pd.DataFrame()

    # IMPORTANT: keep every valid GPS fix from every useful source column.
    # NKE Technical Message files can contain many GPS fixes in one CSV.
    # Reducing to last_per_message/last_per_day loses recovery positions.
    gps_selected = gps.copy()

    n_source_columns = max(int(gps["source_column_index"].max()), df.shape[1] - 1)
    times_by_column = parse_message_times(df, date_folder, n_source_columns, root=root)
    health_by_column = extract_health(df, n_source_columns)

    rows = []

    for _, fix in gps_selected.iterrows():
        source_idx0 = int(fix["source_column_index"]) - 1
        t = times_by_column[source_idx0] if source_idx0 < len(times_by_column) else pd.NaT

        record_type, has_profile, cycle_nonzero_count, timing = classify_fix_from_cycle_timing(df, source_idx0)

        rec = {
            "imei": imei,
            "date_folder": date_folder,
            "source_file": str(path),
            "gps_fix_index": int(fix["gps_fix_index"]),
            "source_column_index": int(fix["source_column_index"]),
            "source_excel_column": excel_column_name(int(fix["source_csv_column"])),
            "source_csv_column": int(fix["source_csv_column"]),
            "n_gps_fixes_in_message": int(len(gps)),
            "timestamp": t,
            "timestamp_string": "" if pd.isna(t) else pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%S"),
            "lat": float(fix["lat"]),
            "lon": float(fix["lon"]),
            "gps_mode": "all_fixes_forced",
            "record_type": record_type,
            "has_profile": has_profile,
            "is_gps_only": not has_profile,
            "cycle_timing_nonzero_count": int(cycle_nonzero_count),
            "is_end_of_life_candidate": bool(len(gps) > 1),
        }

        rec.update(timing)

        for key, values in health_by_column.items():
            rec[key] = values[source_idx0] if source_idx0 < len(values) else np.nan

        rows.append(rec)

    return pd.DataFrame(rows)

def load_navigation_table(root: Path, imei: str, technical_csv: str):
    files = find_technical_files(root, technical_csv=technical_csv)
    records = []
    file_rows = []
    for f in files:
        parsed = parse_one_technical(f, imei=imei, root=root)
        file_rows.append({
            "source_file": str(f),
            "date_folder": infer_date_from_path(f),
            "navigation_rows_created": len(parsed),
            "profiles": int(parsed["has_profile"].sum()) if not parsed.empty and "has_profile" in parsed.columns else 0,
            "gps_surface_only": int((~parsed["has_profile"].astype(bool)).sum()) if not parsed.empty and "has_profile" in parsed.columns else 0,
            "gps_mode": "all_fixes_forced",
        })
        if not parsed.empty:
            records.append(parsed)
    files_df = pd.DataFrame(file_rows)
    if not records:
        return pd.DataFrame(), files_df
    out = pd.concat(records, ignore_index=True)
    # No daily/message reduction: every valid GPS fix remains in the navigation table.
    out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce")
    out = out.sort_values(
        ["timestamp", "source_file", "source_column_index"],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)
    out = out.drop_duplicates(
        subset=["timestamp", "lat", "lon"],
        keep="first",
    ).reset_index(drop=True)
    out["nav_index"] = np.arange(1, len(out) + 1)

    # Separate navigation positions from real profiles.
    # Classification is column-based: the GPS fix is a profile only when the
    # matching CYCLE TIMING column describes a real cycle.
    if "has_profile" not in out.columns:
        out["has_profile"] = False
    out["profile_index"] = np.nan
    profile_counter = 0
    for i in range(len(out)):
        if bool(out.loc[i, "has_profile"]):
            profile_counter += 1
            out.loc[i, "profile_index"] = profile_counter

    return out, files_df


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = np.sin(dlon) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    return float((np.degrees(np.arctan2(x, y)) + 360) % 360)


def destination_point(lat, lon, bearing, distance_m) -> tuple[float, float]:
    r = 6371000.0
    brng = np.radians(bearing)
    lat1 = np.radians(lat)
    lon1 = np.radians(lon)
    d = distance_m / r

    lat2 = np.arcsin(np.sin(lat1) * np.cos(d) + np.cos(lat1) * np.sin(d) * np.cos(brng))
    lon2 = lon1 + np.arctan2(
        np.sin(brng) * np.sin(d) * np.cos(lat1),
        np.cos(d) - np.sin(lat1) * np.sin(lat2),
    )
    return float(np.degrees(lat2)), float(((np.degrees(lon2) + 540) % 360) - 180)




def choose_utm_crs(lat: pd.Series, lon: pd.Series):
    mean_lat = float(np.nanmean(lat))
    mean_lon = float(np.nanmean(lon))
    zone = int(math.floor((mean_lon + 180) / 6) + 1)
    return (32600 if mean_lat >= 0 else 32700) + zone


def project_positions(lat: pd.Series, lon: pd.Series):
    """Project to UTM with pyproj when available; fallback to local tangent."""
    epsg = choose_utm_crs(lat, lon)
    try:
        from pyproj import Transformer
        fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
        inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
        e, n = fwd.transform(lon.to_numpy(float), lat.to_numpy(float))
        return np.asarray(e, float), np.asarray(n, float), inv
    except Exception:
        lat0_deg = float(np.nanmean(lat))
        lon0_deg = float(np.nanmean(lon))
        lat0 = np.deg2rad(lat0_deg)
        r = 6371000.0
        e = (lon.to_numpy(float) - lon0_deg) * np.cos(lat0) * np.pi / 180.0 * r
        n = (lat.to_numpy(float) - lat0_deg) * np.pi / 180.0 * r
        class LocalInverse:
            def transform(self, e2, n2):
                lon2 = lon0_deg + e2 / (np.cos(lat0) * r) * 180.0 / np.pi
                lat2 = lat0_deg + n2 / r * 180.0 / np.pi
                return lon2, lat2
        return np.asarray(e, float), np.asarray(n, float), LocalInverse()

def compute_navigation(df: pd.DataFrame) -> pd.DataFrame:
    """Navigation metrics and one-step prediction for every consecutive pair.

    For each point i >= 1, compute the vector from point i-1 to point i, then
    extrapolate that same vector from point i to create predicted position i+1.
    The first point has no prediction because it has no previous point.
    """
    out = df.copy()
    if out.empty:
        return out

    easting, northing, inv = project_positions(out["lat"], out["lon"])
    out["easting_m"] = easting
    out["northing_m"] = northing
    out["range_m"] = np.nan
    out["heading_deg"] = np.nan
    out["delta_hours"] = np.nan
    out["speed_m_per_hour"] = np.nan
    out["predicted_easting_m"] = np.nan
    out["predicted_northing_m"] = np.nan
    out["predicted_lat"] = np.nan
    out["predicted_lon"] = np.nan
    out["prediction_basis"] = ""

    for i in range(1, len(out)):
        de = out.loc[i, "easting_m"] - out.loc[i - 1, "easting_m"]
        dn = out.loc[i, "northing_m"] - out.loc[i - 1, "northing_m"]
        rng = math.sqrt(de ** 2 + dn ** 2)
        angle = math.degrees(math.atan2(dn, de))
        heading = (90 - angle) % 360
        out.loc[i, "range_m"] = rng
        out.loc[i, "heading_deg"] = heading

        t1 = pd.to_datetime(out.loc[i - 1, "timestamp"], errors="coerce")
        t2 = pd.to_datetime(out.loc[i, "timestamp"], errors="coerce")
        if pd.notna(t1) and pd.notna(t2):
            dh = (t2 - t1).total_seconds() / 3600.0
            out.loc[i, "delta_hours"] = dh
            if dh > 0:
                out.loc[i, "speed_m_per_hour"] = rng / dh

        pred_e = out.loc[i, "easting_m"] + rng * math.cos(math.radians(angle))
        pred_n = out.loc[i, "northing_m"] + rng * math.sin(math.radians(angle))
        pred_lon, pred_lat = inv.transform(pred_e, pred_n)
        out.loc[i, "predicted_easting_m"] = pred_e
        out.loc[i, "predicted_northing_m"] = pred_n
        out.loc[i, "predicted_lat"] = pred_lat
        out.loc[i, "predicted_lon"] = pred_lon
        out.loc[i, "prediction_basis"] = "previous_to_current_vector"

    return out


def prepare_navigation(df: pd.DataFrame) -> pd.DataFrame:
    """Clean, order and calculate navigation metrics without deleting valid fixes.

    Exact duplicate fixes are removed, timestamps are sorted, and non-positive
    time intervals are not used for speed/prediction. Profile numbering is rebuilt
    after sorting. GPS-only fixes remain in the all-fixes product.
    """
    if df.empty:
        return df
    out=df.copy()
    out["timestamp"]=pd.to_datetime(out["timestamp"],errors="coerce")
    out=out.sort_values(["timestamp","source_file","source_column_index"],na_position="last").reset_index(drop=True)
    out=out.drop_duplicates(subset=["timestamp","lat","lon"],keep="first").reset_index(drop=True)
    out=compute_navigation(out)
    # Invalidate calculations across missing/non-positive time intervals.
    bad=~pd.to_numeric(out["delta_hours"],errors="coerce").gt(0)
    for c in ["speed_m_per_hour","predicted_easting_m","predicted_northing_m","predicted_lat","predicted_lon"]:
        if c in out.columns: out.loc[bad,c]=np.nan
    out.loc[bad,"prediction_basis"]=""
    out["speed_m_per_second"]=pd.to_numeric(out["speed_m_per_hour"],errors="coerce")/3600.0
    out["speed_km_per_hour"]=pd.to_numeric(out["speed_m_per_hour"],errors="coerce")/1000.0
    out["nav_index"]=np.arange(1,len(out)+1)
    out["profile_index"]=np.nan
    counter=0
    if "has_profile" in out.columns:
        for i in range(len(out)):
            if bool(out.loc[i,"has_profile"]):
                counter+=1; out.loc[i,"profile_index"]=counter
    return out


def kml_description(row: pd.Series) -> str:
    fields = [
        ("Timestamp", row.get("timestamp_string", "")),
        ("Record type", row.get("record_type", "")),
        ("Cycle timing non-zero fields", row.get("cycle_timing_nonzero_count", "")),
        ("Navigation index", row.get("nav_index", "")),
        ("Profile index", "" if pd.isna(row.get("profile_index", np.nan)) else int(row.get("profile_index"))),
        ("Latitude", f"{row.get('lat', np.nan):.6f}"),
        ("Longitude", f"{row.get('lon', np.nan):.6f}"),
        ("Range from previous (m)", f"{row.get('range_m', np.nan):.1f}" if np.isfinite(row.get("range_m", np.nan)) else ""),
        ("Speed (m/hour)", f"{row.get('speed_m_per_hour', np.nan):.1f}" if np.isfinite(row.get("speed_m_per_hour", np.nan)) else ""),
        ("Heading (deg)", f"{row.get('heading_deg', np.nan):.1f}" if np.isfinite(row.get("heading_deg", np.nan)) else ""),
        ("Battery voltage drop at Pmax", row.get("battery_voltage_drop_at_pmax", "")),
        ("Internal pressure (mbar)", row.get("internal_pressure_mbar", "")),
        ("Internal temperature (degC)", row.get("internal_temperature_degC", "")),
        ("Descent speed (mBar/sec)", row.get("descent_speed_mbar_sec", "")),
        ("Ascent speed (mBar/sec)", row.get("ascent_speed_mbar_sec", "")),
        ("Defect mode", row.get("defect_mode", "")),
        ("GPS fixes in message", row.get("n_gps_fixes_in_message", "")),
        ("Source", row.get("source_file", "")),
    ]
    rows = [f"<tr><td><b>{html.escape(str(k))}</b></td><td>{html.escape(str(v))}</td></tr>" for k, v in fields]
    return "<![CDATA[<table>" + "".join(rows) + "</table>]]>"


def build_kml(df: pd.DataFrame, imei: str) -> str:
    """
    Build KMZ content from ALL navigation rows.

    Every row in navigation_summary is plotted:
    - profile rows -> P labels
    - gps_surface_only rows -> G labels
    - prediction rows -> Pred labels
    - prediction vectors -> red lines from each real point to its prediction
    """
    track_segments = []
    distances = []
    for i in range(1, len(df)):
        distances.append(
            haversine_m(
                float(df.iloc[i - 1]["lat"]),
                float(df.iloc[i - 1]["lon"]),
                float(df.iloc[i]["lat"]),
                float(df.iloc[i]["lon"]),
            ) / 1000.0
        )

    finite = np.asarray([d for d in distances if np.isfinite(d) and d > 0], dtype=float)
    median_step = float(np.nanmedian(finite)) if finite.size else np.nan
    jump_limit = max(20.0, 8.0 * median_step) if np.isfinite(median_step) else 50.0

    for i in range(1, len(df)):
        if not np.isfinite(distances[i - 1]) or distances[i - 1] > jump_limit:
            continue
        a = df.iloc[i - 1]
        b = df.iloc[i]
        track_segments.append(f"""
    <Placemark>
      <name>Track segment {i}</name>
      <styleUrl>#trackStyle</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
          {a['lon']:.6f},{a['lat']:.6f},0
          {b['lon']:.6f},{b['lat']:.6f},0
        </coordinates>
      </LineString>
    </Placemark>""")

    profile_pms = []
    gps_pms = []
    pred_pms = []
    pred_vecs = []

    for _, row in df.iterrows():
        nav_idx = int(row.get("nav_index", 0))
        ts = str(row.get("timestamp_string", "") or "").replace("T", " ")

        if bool(row.get("has_profile", False)):
            pidx = row.get("profile_index", np.nan)
            base_name = f"P{int(pidx)}" if not pd.isna(pidx) else "P?"
            name = f"{base_name} {ts}".strip()
            profile_pms.append(f"""
    <Placemark>
      <name>{html.escape(name)}</name>
      <styleUrl>#profileStyle</styleUrl>
      <description>{kml_description(row)}</description>
      <Point><coordinates>{row['lon']:.6f},{row['lat']:.6f},0</coordinates></Point>
    </Placemark>""")
        else:
            name = f"G{nav_idx} {ts}".strip()
            gps_pms.append(f"""
    <Placemark>
      <name>{html.escape(name)}</name>
      <styleUrl>#gpsOnlyStyle</styleUrl>
      <description>{kml_description(row)}</description>
      <Point><coordinates>{row['lon']:.6f},{row['lat']:.6f},0</coordinates></Point>
    </Placemark>""")

        plat = row.get("predicted_lat", np.nan)
        plon = row.get("predicted_lon", np.nan)
        if np.isfinite(plat) and np.isfinite(plon):
            pred_name = f"Pred {nav_idx} {ts}".strip()
            pred_pms.append(f"""
    <Placemark>
      <name>{html.escape(pred_name)}</name>
      <styleUrl>#predictionStyle</styleUrl>
      <description><![CDATA[
        <b>Prediction from navigation point {nav_idx}</b><br/>
        Lat: {plat:.6f}<br/>
        Lon: {plon:.6f}<br/>
        Range: {row.get('range_m', np.nan):.1f} m<br/>
        Heading: {row.get('heading_deg', np.nan):.1f} deg<br/>
        Speed: {row.get('speed_m_per_hour', np.nan):.1f} m/hour
      ]]></description>
      <Point><coordinates>{plon:.6f},{plat:.6f},0</coordinates></Point>
    </Placemark>""")

            pred_vecs.append(f"""
    <Placemark>
      <name>Vector {nav_idx}</name>
      <styleUrl>#predictionVectorStyle</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
          {row['lon']:.6f},{row['lat']:.6f},0
          {plon:.6f},{plat:.6f},0
        </coordinates>
      </LineString>
    </Placemark>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"
     xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document>
  <name>ARVORC Navigation IMEI {html.escape(str(imei))}</name>

  <Style id="trackStyle">
    <LineStyle><color>ff00ffff</color><width>3</width></LineStyle>
  </Style>

  <Style id="profileStyle">
    <IconStyle>
      <scale>1.1</scale>
      <Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon>
    </IconStyle>
    <LabelStyle><scale>0.85</scale></LabelStyle>
  </Style>

  <Style id="gpsOnlyStyle">
    <IconStyle>
      <scale>0.9</scale>
      <Icon><href>http://maps.google.com/mapfiles/kml/paddle/blu-circle.png</href></Icon>
    </IconStyle>
    <LabelStyle><scale>0.7</scale></LabelStyle>
  </Style>

  <Style id="predictionStyle">
    <IconStyle>
      <color>ff0000ff</color>
      <scale>1.0</scale>
      <Icon><href>http://maps.google.com/mapfiles/kml/pushpin/red-pushpin.png</href></Icon>
    </IconStyle>
    <LabelStyle><scale>0.7</scale></LabelStyle>
  </Style>

  <Style id="predictionVectorStyle">
    <LineStyle><color>ff0000ff</color><width>2</width></LineStyle>
  </Style>

  <Folder>
    <name>Track - all GPS positions</name>
{''.join(track_segments)}
  </Folder>

  <Folder>
    <name>Profile positions</name>
{''.join(profile_pms)}
  </Folder>

  <Folder>
    <name>GPS-only positions</name>
{''.join(gps_pms)}
  </Folder>

  <Folder>
    <name>Predictions</name>
{''.join(pred_pms)}
  </Folder>

  <Folder>
    <name>Prediction vectors</name>
{''.join(pred_vecs)}
  </Folder>

</Document>
</kml>
"""

def write_kmz(df: pd.DataFrame, outdir: Path, imei: str) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    kmz = outdir / f"navigation_IMEI_{imei or 'unknown'}_{stamp}.kmz"
    kml = build_kml(df, imei=imei)
    with zipfile.ZipFile(kmz, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)

    n_profile = int(df["has_profile"].sum()) if "has_profile" in df.columns else 0
    n_gps = int((~df["has_profile"].astype(bool)).sum()) if "has_profile" in df.columns else len(df)
    n_pred = int(df[["predicted_lat", "predicted_lon"]].dropna().shape[0]) if {"predicted_lat", "predicted_lon"}.issubset(df.columns) else 0
    print(f"KMZ_LAYER_COUNTS profile={n_profile} gps_surface_only={n_gps} predictions={n_pred} total_positions={len(df)}")
    return kmz


def plot_map(df: pd.DataFrame, outdir: Path, imei: str) -> Path | None:
    if df.empty:
        return None

    outdir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8.5, 7.5))

    track_lon = df["lon"].to_numpy(float).copy()
    track_lat = df["lat"].to_numpy(float).copy()
    if len(df) >= 3:
        distances = [
            haversine_m(
                track_lat[i - 1], track_lon[i - 1],
                track_lat[i], track_lon[i],
            ) / 1000.0
            for i in range(1, len(df))
        ]
        finite = np.asarray([d for d in distances if np.isfinite(d) and d > 0], dtype=float)
        median_step = float(np.nanmedian(finite)) if finite.size else np.nan
        jump_limit = max(20.0, 8.0 * median_step) if np.isfinite(median_step) else 50.0
        for i, distance in enumerate(distances, start=1):
            if np.isfinite(distance) and distance > jump_limit:
                track_lon[i] = np.nan
                track_lat[i] = np.nan

    ax.plot(track_lon, track_lat, "-k", lw=1.2, alpha=0.85, zorder=2)

    if "has_profile" in df.columns:
        profiles = df[df["has_profile"].astype(bool)]
        gps_only = df[~df["has_profile"].astype(bool)]
    else:
        profiles = df
        gps_only = df.iloc[0:0]

    sc = ax.scatter(df["lon"], df["lat"], c=df["nav_index"], s=45, cmap="viridis", edgecolor="k", lw=0.35, zorder=3)

    if len(gps_only):
        ax.scatter(gps_only["lon"], gps_only["lat"], marker=".", s=32, color="0.35", alpha=0.65, zorder=4, label="GPS only")

    if len(profiles):
        ax.scatter(
            profiles["lon"], profiles["lat"],
            marker="o", s=90, facecolor="none", edgecolor="red",
            lw=1.2, zorder=5, label="Profile"
        )

    ax.scatter(df.iloc[0]["lon"], df.iloc[0]["lat"], marker="*", s=180, color="yellow", edgecolor="k", zorder=7, label="First")
    ax.scatter(df.iloc[-1]["lon"], df.iloc[-1]["lat"], marker="o", s=110, color="red", edgecolor="k", zorder=8, label="Last")

    preds = df.dropna(subset=["predicted_lat", "predicted_lon"]) if {"predicted_lat", "predicted_lon"}.issubset(df.columns) else df.iloc[0:0]
    if len(preds):
        # Draw predictions in blue and lighter than the real GPS track so they
        # remain visible without dominating the figure.
        ax.scatter(
            preds["predicted_lon"], preds["predicted_lat"],
            marker="x", s=42, color="blue", linewidth=1.0,
            alpha=0.75, zorder=6, label="Predictions"
        )
        for _, r in preds.iterrows():
            ax.plot(
                [r["lon"], r["predicted_lon"]],
                [r["lat"], r["predicted_lat"]],
                color="blue", linestyle="--", linewidth=0.7,
                alpha=0.35, zorder=5
            )

    lon_pad = max(0.03, (df["lon"].max() - df["lon"].min()) * 0.18)
    lat_pad = max(0.03, (df["lat"].max() - df["lat"].min()) * 0.18)
    ax.set_xlim(df["lon"].min() - lon_pad, df["lon"].max() + lon_pad)
    ax.set_ylim(df["lat"].min() - lat_pad, df["lat"].max() + lat_pad)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"Navigation track - IMEI {imei}")
    ax.grid(True, alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="best")

    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label("Navigation point")

    out = outdir / f"navigation_map_IMEI_{imei or 'unknown'}.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_series(df: pd.DataFrame, outdir: Path, imei: str) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    outputs=[]
    variables=[
        ("battery_voltage_drop_at_pmax","Battery voltage drop at Pmax",True),
        ("internal_pressure_mbar","Internal pressure (mbar)",True),
        ("internal_temperature_degC","Internal temperature (degC)",True),
        ("speed_m_per_hour","Drift speed (m/hour)",False),
        ("heading_deg","Drift heading (degrees)",False),
        ("range_m","Range between fixes (m)",False),
    ]
    for col,label,profiles_only in variables:
        if col not in df.columns: continue
        plot_df=df[df["has_profile"].astype(bool)].copy() if profiles_only and "has_profile" in df.columns else df.copy()
        vals=pd.to_numeric(plot_df[col],errors="coerce")
        valid=vals.notna()
        if valid.sum()==0: continue
        plot_df=plot_df.loc[valid]; vals=vals.loc[valid]
        x=pd.to_datetime(plot_df["timestamp"],errors="coerce")
        if x.isna().all(): x=plot_df["nav_index"]
        fig,ax=plt.subplots(figsize=(8.5,4.2))
        ax.plot(x,vals,"-o",lw=1.2,ms=4)
        ax.set_title(f"{label} - IMEI {imei}")
        ax.set_ylabel(label)
        ax.grid(True,alpha=0.25)
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        fig.autofmt_xdate()
        out=outdir/f"{col}_IMEI_{imei or 'unknown'}.png"
        fig.savefig(out,dpi=300,bbox_inches="tight"); plt.close(fig); outputs.append(out)
    return outputs


def plot_recovery_zoom(df: pd.DataFrame, outdir: Path, imei: str, n_last: int = 10) -> Path | None:
    """Generate a zoom map of the last N positions with prediction vector for recovery."""
    if df.empty or len(df) < 3:
        return None

    outdir.mkdir(parents=True, exist_ok=True)

    # Use last n_last positions
    last = df.tail(n_last).copy()
    has_pred = last["predicted_lat"].notna() & last["predicted_lon"].notna()

    fig, ax = plt.subplots(figsize=(8, 7))

    # Trajectory of last fixes
    ax.plot(last["lon"], last["lat"], "o-", color="#333333", markersize=6,
            linewidth=1.5, alpha=0.8, zorder=3)

    # Label each point with nav_index or timestamp
    for _, row in last.iterrows():
        t = pd.to_datetime(row.get("timestamp"), errors="coerce")
        label = t.strftime("%H:%M") if pd.notna(t) and t.hour + t.minute > 0 else f"#{int(row['nav_index'])}"
        ax.annotate(label, (row["lon"], row["lat"]), textcoords="offset points",
                    xytext=(5, 5), fontsize=7, color="#333333")

    # Last known position (highlighted)
    last_row = last.iloc[-1]
    ax.scatter(last_row["lon"], last_row["lat"], s=120, color="#2E7D32", edgecolor="white",
               linewidths=2, zorder=6, label="Last known position")

    # Prediction vector and predicted position
    if has_pred.any():
        pred_row = last[has_pred].iloc[-1]
        ax.annotate("", xy=(pred_row["predicted_lon"], pred_row["predicted_lat"]),
                    xytext=(pred_row["lon"], pred_row["lat"]),
                    arrowprops=dict(arrowstyle="-|>", color="#D32F2F", lw=2))
        ax.scatter(pred_row["predicted_lon"], pred_row["predicted_lat"], s=100,
                   marker="X", color="#D32F2F", edgecolor="white", linewidths=1.5,
                   zorder=7, label="Predicted next position")

    # Info box with last fix info and forecast
    speed = pd.to_numeric(last_row.get("speed_m_per_hour", np.nan), errors="coerce")
    heading = pd.to_numeric(last_row.get("heading_deg", np.nan), errors="coerce")
    info_lines = []
    t_last = pd.to_datetime(last_row.get("timestamp"), errors="coerce")
    info_lines.append("── Last fix ──")
    if pd.notna(t_last):
        info_lines.append(f"  Time: {t_last.strftime('%Y-%m-%d %H:%M UTC')}")
    info_lines.append(f"  Lat: {last_row['lat']:.6f}")
    info_lines.append(f"  Lon: {last_row['lon']:.6f}")
    info_lines.append("")
    info_lines.append("── Forecast ──")
    if np.isfinite(speed):
        info_lines.append(f"  Speed: {speed:.0f} m/h")
    if np.isfinite(heading):
        info_lines.append(f"  Heading: {heading:.0f}\u00b0")
    if has_pred.any():
        info_lines.append(f"  Pred. lat: {pred_row['predicted_lat']:.6f}")
        info_lines.append(f"  Pred. lon: {pred_row['predicted_lon']:.6f}")
    if info_lines:
        ax.text(0.02, 0.98, "\n".join(info_lines), transform=ax.transAxes,
                fontsize=9, verticalalignment="top",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.9, edgecolor="#cccccc"))

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"Recovery position - IMEI {imei}" if imei else "Recovery position")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Tight padding around data
    lons = list(last["lon"])
    lats = list(last["lat"])
    if has_pred.any():
        lons.append(pred_row["predicted_lon"])
        lats.append(pred_row["predicted_lat"])
    lon_pad = max((max(lons) - min(lons)) * 0.2, 0.001)
    lat_pad = max((max(lats) - min(lats)) * 0.2, 0.001)
    ax.set_xlim(min(lons) - lon_pad, max(lons) + lon_pad)
    ax.set_ylim(min(lats) - lat_pad, max(lats) + lat_pad)

    out = outdir / f"recovery_zoom_IMEI_{imei or 'unknown'}.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out


def main():
    settings = load_settings()

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--imei", default=settings.get("last_imei", ""))
    ap.add_argument("--technical_csv", default=settings.get("technical_csv_name", "Technical Message.csv"))
    # GPS mode is intentionally not exposed: all valid GPS fixes are always used.
    args = ap.parse_args()

    root = Path(args.root)
    outdir = Path(args.outdir)
    nav_dir = outdir / "navigation"
    kmz_dir = outdir / "kmz"
    map_dir = outdir / "maps"

    if not root.exists():
        raise SystemExit(f"ERROR: root folder does not exist: {root}")

    df, files_df = load_navigation_table(root, imei=args.imei, technical_csv=args.technical_csv)
    nav_dir.mkdir(parents=True, exist_ok=True)
    files_csv = nav_dir / f"technical_files_read_IMEI_{args.imei or 'unknown'}.csv"
    files_df.to_csv(files_csv, index=False)
    if df.empty:
        raise SystemExit("ERROR: no GPS navigation records found in Technical Message files.")

    df_raw = df.copy()
    df_nav = prepare_navigation(df)

    nav_dir.mkdir(parents=True, exist_ok=True)
    raw_csv = nav_dir / f"navigation_raw_IMEI_{args.imei or 'unknown'}.csv"
    summary_csv = nav_dir / f"navigation_summary_IMEI_{args.imei or 'unknown'}.csv"

    df_raw.to_csv(raw_csv, index=False)
    df_nav.to_csv(summary_csv, index=False)
    profile_csv = nav_dir / f"navigation_profiles_only_IMEI_{args.imei or 'unknown'}.csv"
    profile_nav = df_nav[df_nav["has_profile"].astype(bool)].copy() if "has_profile" in df_nav.columns else df_nav.iloc[0:0]
    profile_nav.to_csv(profile_csv, index=False)

    kmz = write_kmz(df_nav, kmz_dir, imei=args.imei)
    map_png = plot_map(df_nav, map_dir, imei=args.imei)
    series_pngs = plot_series(df_nav, nav_dir, imei=args.imei)
    recovery_png = plot_recovery_zoom(df_nav, nav_dir, imei=args.imei)

    print(f"TECHNICAL_FILES_READ={len(files_df)}")
    print(f"TECHNICAL_FILES_CSV={files_csv}")
    print(f"NAVIGATION_RECORDS_RAW={len(df_raw)}")
    print(f"NAVIGATION_RECORDS_USED={len(df_nav)}")
    print(f"NAVIGATION_RAW_CSV={raw_csv}")
    print(f"NAVIGATION_SUMMARY_CSV={summary_csv}")
    print(f"NAVIGATION_PROFILES_ONLY_CSV={profile_csv}")
    print(f"NAVIGATION_KMZ={kmz}")
    if map_png:
        print(f"NAVIGATION_MAP={map_png}")
    if recovery_png:
        print(f"RECOVERY_MAP={recovery_png}")
    for p in series_pngs:
        print(f"NAVIGATION_PLOT={p}")
    print("NAVIGATION_PRODUCTS_DONE")


if __name__ == "__main__":
    main()
