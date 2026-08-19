#!/usr/bin/env python3
"""
Generate quick-look products from decoded CSV files.

Products:
- TS diagram from ascent profiles;
- TEMP/PSAL sections at 0-100 dbar;
- TEMP/PSAL sections at full available depth;
- trajectory map;
- KMZ trajectory.

Position handling:
NKE Technical Message files often store GPS as degrees, minutes,
minute fractions (4th), and orientation. This script explicitly converts:

latitude = degrees + (minutes + fractions/10000) / 60
orientation 0=North, 1=South

longitude = degrees + (minutes + fractions/10000) / 60
orientation 0=East, 1=West

Bathymetry:
If a GEBCO NetCDF path is provided, map and section plots include GEBCO
bathymetry. This requires xarray/netCDF4.
"""

from __future__ import annotations

import argparse
import csv
import math
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from common import load_settings, find_date_folders, discover_raw_sbd_dates, default_dataset_date


# ---------------------------
# Basic IO/helpers
# ---------------------------
def safe_float(x) -> float:
    s = str(x).strip().strip('"').replace(",", ".")
    if s == "" or s.lower() in ("nan", "none", "null"):
        return np.nan
    try:
        return float(s)
    except Exception:
        return np.nan


def read_semicolon_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, sep=";", encoding="latin-1", engine="python")
    except Exception:
        return pd.read_csv(path, sep=";", encoding="utf-8", engine="python")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().strip('"') for c in out.columns]
    return out


def pick_col(df: pd.DataFrame, candidates: list[str], fallback_idx: int | None = None) -> str | None:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    if fallback_idx is not None and fallback_idx < len(df.columns):
        return df.columns[fallback_idx]
    return None


def to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str).str.strip().str.strip('"').str.replace(",", ".", regex=False),
        errors="coerce"
    )


def infer_date_from_path(path: Path) -> str:
    for parent in [path.parent, *path.parents]:
        if parent.name.isdigit() and len(parent.name) == 8:
            return parent.name
    if path.name[:8].isdigit():
        return path.name[:8]
    return "unknown"


def resolve_file_date(path: Path, root: Path) -> str:
    """Resolve a date for daily and flat-batch decoded outputs."""
    d = infer_date_from_path(path)
    return default_dataset_date(root) if d == "unknown" else d


def batch_message_times(df: pd.DataFrame, root: Path, n_source_columns: int) -> list[pd.Timestamp]:
    """Build timestamps in flat batch mode and handle midnight rollovers.

    The first raw SBD filename date is used as the initial calendar date. When
    the float hour decreases between consecutive source columns, one day is
    added. This preserves profiles transmitted across midnight.
    """
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


def find_ascent_files(root: Path, ascent_csv: str, filled_prefix: str) -> list[Path]:
    """Find ascent CSVs in batch or legacy layouts without duplicating datasets."""
    root = Path(root)
    decoded = root / "decoded"
    batch = decoded / "batch"
    raw = root / "sbd_raw"

    if batch.exists() and any(batch.rglob("*Ascent*CTD*Message*.csv")):
        roots = [batch]
        mode = "batch"
    elif decoded.exists() and any(decoded.rglob("*Ascent*CTD*Message*.csv")):
        roots = [decoded]
        mode = "decoded"
    elif raw.exists() and any(raw.rglob("*Ascent*CTD*Message*.csv")):
        roots = [raw]
        mode = "sbd_raw"
    else:
        roots = find_date_folders(root)
        mode = "daily"

    filled = []
    originals = []
    for base in roots:
        for p in base.rglob("*.csv"):
            name = p.name.lower()
            if "ascent" not in name or "ctd" not in name or "message" not in name:
                continue
            if "filled" in name:
                filled.append(p)
            else:
                originals.append(p)

    selected = filled if filled else originals
    out = []
    seen = set()
    for p in sorted(selected, key=lambda x: str(x).lower()):
        key = str(p.resolve()).lower()
        if key not in seen:
            out.append(p)
            seen.add(key)

    print(
        f"ASCENT_SEARCH mode={mode} filled={len(filled)} "
        f"original={len(originals)} selected={len(out)}"
    )
    return out

def find_position_files(root: Path, technical_csv: str) -> list[Path]:
    """Find one coherent position dataset, preferring decoded/batch."""
    root = Path(root)
    decoded = root / "decoded"
    batch = decoded / "batch"
    raw = root / "sbd_raw"

    def collect(base: Path) -> list[Path]:
        if not base.exists():
            return []
        return (
            list(base.rglob("Technical Message*.csv"))
            + list(base.rglob("Technical message*.csv"))
            + list(base.rglob("*GPS*Message*.csv"))
            + list(base.rglob("*Position*.csv"))
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
        if n == str(technical_csv).lower():
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

    print(f"POSITION_SEARCH mode={mode} files={len(out)}")
    return out



# ---------------------------
# NKE GPS parsing
# ---------------------------
# This block intentionally mirrors generate_navigation_products.py.
# NKE Technical Message.csv files are column-oriented:
#   column 0 = labels
#   each column >= 1 = a possible GPS/profile/navigation fix
# Therefore the quicklook map must NOT pick only the last numeric value in each row.
# It must parse every useful GPS column and classify each fix as profile or GPS-only.

GPS_LABELS = {
    # Support both NKE Technical Message naming styles:
    #   - "GPS latitude in degrees"
    #   - "GPS latitude (°)"
    "lat_deg": [
        "GPS latitude in degrees",
        "GPS latitude (°",
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
        "GPS latitude orientation (0=North",
    ],
    "lon_deg": [
        "GPS longitude in degrees",
        "GPS longitude (°",
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
        "GPS longitude orientation (0=East",
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


def read_semicolon_csv_raw(path: Path) -> pd.DataFrame:
    """Read semicolon CSV preserving all original columns, including ragged rows."""
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


def find_row_index(df: pd.DataFrame, label_contains) -> int | None:
    """Find row index by case-insensitive substring in first column.

    label_contains may be a string or a list of alternative label substrings.
    """
    if df.empty:
        return None

    candidates = label_contains if isinstance(label_contains, (list, tuple)) else [label_contains]
    labels = df.iloc[:, 0].astype(str).str.strip().str.strip('"').str.lower()

    for cand in candidates:
        wanted = str(cand).lower()
        mask = labels.str.contains(wanted, regex=False, na=False)
        if mask.any():
            return int(mask[mask].index[0])

    return None


def row_raw_values(df: pd.DataFrame, label_contains) -> list[float]:
    """
    Return values after the label preserving the original CSV columns.
    No value/flag skipping is applied.

    label_contains may be a string or a list of alternative label substrings.
    """
    idx = find_row_index(df, label_contains)
    if idx is None:
        return []
    return [safe_float(v) for v in df.loc[idx].iloc[1:].values]


def raw_at(values: list[float], idx0: int, default=np.nan) -> float:
    return values[idx0] if idx0 < len(values) else default


def decimal_from_nke_gps(deg, minutes, minute_fraction_4th, orientation, positive_orientation_zero=True):
    if not (np.isfinite(deg) and np.isfinite(minutes)):
        return np.nan

    frac = 0.0 if not np.isfinite(minute_fraction_4th) else float(minute_fraction_4th) / 10000.0
    val = float(deg) + (float(minutes) + frac) / 60.0

    # NKE convention in these Technical Message files:
    # lat orientation: 0=North, 1=South
    # lon orientation: 0=East, 1=West
    if np.isfinite(orientation) and int(orientation) == 1:
        val = -val

    return val


def parse_gps_fixes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parse GPS fixes column by column.

    Every original CSV column after the label is a potential fix. A fix is kept
    when the lat/lon reconstructed from that same column is valid and non-zero.

    This supports both known NKE naming styles:
      - GPS latitude in degrees / minutes / ...
      - GPS latitude (°) / GPS latitude (minutes) / ...
    """
    lat_deg = row_raw_values(df, GPS_LABELS["lat_deg"])
    lat_min = row_raw_values(df, GPS_LABELS["lat_min"])
    lat_frac = row_raw_values(df, GPS_LABELS["lat_frac"])
    lat_ori = row_raw_values(df, GPS_LABELS["lat_orient"])

    lon_deg = row_raw_values(df, GPS_LABELS["lon_deg"])
    lon_min = row_raw_values(df, GPS_LABELS["lon_min"])
    lon_frac = row_raw_values(df, GPS_LABELS["lon_frac"])
    lon_ori = row_raw_values(df, GPS_LABELS["lon_orient"])

    valid_fix = row_raw_values(df, GPS_LABELS.get("valid_fix", []))

    n_cols = max(
        len(lat_deg), len(lat_min), len(lat_frac), len(lat_ori),
        len(lon_deg), len(lon_min), len(lon_frac), len(lon_ori),
    )
    records = []
    gps_fix_index = 0

    for source_idx0 in range(n_cols):
        # If a valid-fix flag exists, respect it. If it does not exist, keep the
        # historical behaviour and rely on reconstructed lat/lon validity.
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
            "source_column_index": source_idx0 + 1,
            "source_csv_column": source_idx0 + 2,
            "lat": lat,
            "lon": lon,
        })

    return pd.DataFrame(records)


def classify_fix_from_cycle_timing(df: pd.DataFrame, source_idx0: int) -> tuple[str, bool, int]:
    """Return (record_type, has_profile, nonzero_timing_count) for the same GPS column."""
    nonzero_count = 0
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
        core_values.append(raw_at(values, source_idx0, np.nan))

    # A genuine profile column contains a populated CYCLE TIMING block.
    # Do not require every named field to be non-zero: midnight and elapsed-time
    # fields can legitimately be zero, and label wording varies between NKE
    # decoder versions. GPS-only columns have an empty/zero timing block.
    finite_core_count = sum(np.isfinite(v) for v in core_values)
    nonzero_core_count = sum(np.isfinite(v) and abs(v) > 1e-9 for v in core_values)
    has_profile = (nonzero_count >= 3) or (finite_core_count >= 4 and nonzero_core_count >= 2)
    record_type = "profile" if has_profile else "gps_surface_only"
    return record_type, bool(has_profile), int(nonzero_count)


def parse_message_times(
    df: pd.DataFrame,
    date_folder: str,
    n_source_columns: int,
    root: Path | None = None,
) -> list[pd.Timestamp]:
    """Return one real timestamp per original Technical Message column.

    Full calendar fields are preferred. When NKE exports only hour/minute/second,
    infer day changes from clock rollovers and the dates encoded in the raw SBD
    filenames instead of assigning the first dataset date to every profile.
    """
    years = row_raw_values(df, ["Float time : Year", "Float's year"])
    months = row_raw_values(df, ["Float time : Month", "Float's month"])
    days = row_raw_values(df, ["Float time : Day", "Float's day"])
    hours = row_raw_values(df, ["Float time : Hour", "Float's hour"])
    minutes = row_raw_values(df, ["Float time : Minute", "Float's minute"])
    seconds = row_raw_values(df, ["Float time : Second", "Float's second"])

    fallback_dates = discover_raw_sbd_dates(root) if root is not None else []
    try:
        folder_base = pd.to_datetime(date_folder, format="%Y%m%d", errors="coerce")
    except Exception:
        folder_base = pd.NaT
    fallback_base = (
        pd.to_datetime(fallback_dates[0], format="%Y%m%d", errors="coerce")
        if fallback_dates else folder_base
    )

    out = []
    day_offset = 0
    previous_clock = None
    for idx in range(n_source_columns):
        y = raw_at(years, idx, np.nan)
        mo = raw_at(months, idx, np.nan)
        d = raw_at(days, idx, np.nan)
        h = raw_at(hours, idx, np.nan)
        mi = raw_at(minutes, idx, np.nan)
        sec = raw_at(seconds, idx, 0)

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

        if pd.notna(fallback_base) and np.isfinite(h) and np.isfinite(mi):
            clock = int(h) * 3600 + int(mi) * 60 + int(sec if np.isfinite(sec) else 0)
            if previous_clock is not None and clock < previous_clock - 6 * 3600:
                day_offset += 1
            previous_clock = clock
            out.append(fallback_base + pd.Timedelta(days=day_offset, seconds=clock))
        else:
            out.append(pd.NaT)

    return out

def extract_tabular_positions(df: pd.DataFrame):
    lat_col = None
    lon_col = None
    time_col = None
    cycle_col = None

    for c in df.columns:
        cl = c.lower()
        if lat_col is None and "lat" in cl:
            lat_col = c
        if lon_col is None and ("lon" in cl or "long" in cl):
            lon_col = c
        if time_col is None and ("time" in cl or "date" in cl):
            time_col = c
        if cycle_col is None and "cycle" in cl:
            cycle_col = c

    if lat_col is None or lon_col is None:
        return None

    lat = to_num(df[lat_col])
    lon = to_num(df[lon_col])

    if time_col is not None:
        time = pd.to_datetime(df[time_col], errors="coerce")
    else:
        time = pd.Series([pd.NaT] * len(df))

    if cycle_col is not None:
        cycle = to_num(df[cycle_col])
    else:
        cycle = pd.Series(np.arange(1, len(df) + 1), dtype=float)

    return pd.DataFrame({
        "TIME": time,
        "CYCLE_NUMBER": cycle,
        "LATITUDE": lat,
        "LONGITUDE": lon,
        "RECORD_TYPE": "position",
        "HAS_PROFILE": False,
    })


# ---------------------------
# Data loading
# ---------------------------
def invalid_ctd_rows(pres: pd.Series, temp: pd.Series, psal: pd.Series) -> pd.Series:
    """Identify NKE placeholder rows that are not physical CTD observations.

    Known placeholders:
      - PRES=1000 dbar, TEMP=0 °C, PSAL=0 PSU
      - TEMP=0 °C, PSAL=25 PSU separator rows

    Comparisons use a small tolerance to accommodate decimal formatting.
    """
    p = pd.to_numeric(pres, errors="coerce")
    t = pd.to_numeric(temp, errors="coerce")
    s = pd.to_numeric(psal, errors="coerce")

    bad_1000_0_0 = (
        np.isclose(p, 1000.0, atol=1e-6, equal_nan=False) &
        np.isclose(t, 0.0, atol=1e-6, equal_nan=False) &
        np.isclose(s, 0.0, atol=1e-6, equal_nan=False)
    )
    separator_0_25 = (
        np.isclose(t, 0.0, atol=1e-6, equal_nan=False) &
        np.isclose(s, 25.0, atol=1e-6, equal_nan=False)
    )
    return pd.Series(bad_1000_0_0 | separator_0_25, index=pres.index)

def _profile_from_dataframe(df: pd.DataFrame, path: Path, profile_number: int, imei: str,
                            profile_time=pd.NaT, root: Path | None = None) -> pd.DataFrame:
    """Convert one already-split ascent CSV into the normalized profile table."""
    df = normalize_columns(df)
    cP = pick_col(df, ["CTD - Pressure (dbar)", "CTD Pressure (dbar)", "CTD Pressure", "PRES", "Pressure"], 5)
    cT = pick_col(df, ["CTD - Temperature (°C)", "CTD - Temperature (degC)", "CTD - Temperature", "CTD temperature", "CTD Temperature", "TEMP", "Temperature"], 6)
    cS = pick_col(df, ["CTD - Salinity (PSU)", "CTD - Salinity", "CTD Salinity (PSU)", "CTD Salinity", "PSAL", "Salinity"], 7)
    if cP is None or cT is None or cS is None:
        return pd.DataFrame()

    # Try to get date from the DataFrame's DATE column (Python decoder output)
    date_col = pick_col(df, ["Date", "DATE", "date"], None)
    if date_col and date_col in df.columns and pd.isna(profile_time):
        date_vals = df[date_col].dropna()
        if len(date_vals) > 0:
            first_date = str(date_vals.iloc[0]).strip()
            if first_date:
                parsed = pd.to_datetime(first_date, errors="coerce")
                if pd.notna(parsed):
                    profile_time = parsed

    date_folder = infer_date_from_path(path)
    if date_folder == "unknown" and pd.notna(profile_time):
        date_folder = pd.Timestamp(profile_time).strftime("%Y%m%d")
    elif date_folder == "unknown" and root is not None:
        date_folder = default_dataset_date(root)

    tmp = pd.DataFrame({
        "IMEI": imei,
        "DATE_FOLDER": date_folder,
        "PROFILE_NUMBER": profile_number,
        "PROFILE_ID": f"{date_folder}_{profile_number:02d}",
        "PRES": to_num(df[cP]),
        "TEMP": to_num(df[cT]),
        "PSAL": to_num(df[cS]),
    })
    tmp["TIME"] = pd.Timestamp(profile_time) if pd.notna(profile_time) else pd.to_datetime(date_folder, format="%Y%m%d", errors="coerce")
    tmp = tmp.replace([np.inf, -np.inf], np.nan)

    bad_ctd = invalid_ctd_rows(tmp["PRES"], tmp["TEMP"], tmp["PSAL"])
    if bad_ctd.any():
        print(f"CTD_PLACEHOLDERS_REMOVED file={path} rows={int(bad_ctd.sum())}")
        tmp = tmp.loc[~bad_ctd].copy()

    tmp = tmp.dropna(subset=["PRES", "TEMP", "PSAL"], how="all")
    tmp = tmp[(tmp["TEMP"].isna() | tmp["TEMP"].between(-5, 45)) &
              (tmp["PSAL"].isna() | tmp["PSAL"].between(0, 50)) &
              (tmp["PRES"].isna() | tmp["PRES"].between(-5, 12000))]
    return tmp


def _split_raw_ascent_dataframe(df: pd.DataFrame) -> list[pd.DataFrame]:
    """Split a concatenated NKE ascent table into individual profiles.

    If the DataFrame contains a 'Cycle number' column (produced by the Python
    decoder), use it directly to group measurements into profiles. Otherwise
    fall back to the legacy heuristic based on pressure separators.
    """
    df = normalize_columns(df)

    # --- Strategy 1: split by Cycle number column (Python decoder output) ---
    cycle_col = pick_col(df, ["Cycle number", "Cycle", "CYCLE_NUMBER", "cycle_number"], None)
    if cycle_col is not None:
        cycles = to_num(df[cycle_col])
        valid_mask = cycles.notna() & (cycles > 0)
        if valid_mask.sum() > 0:
            cP = pick_col(df, ["CTD - Pressure (dbar)", "CTD Pressure (dbar)", "CTD Pressure", "PRES", "Pressure"], 5)
            cT = pick_col(df, ["CTD - Temperature (°C)", "CTD - Temperature (degC)", "CTD - Temperature", "CTD temperature", "CTD Temperature", "TEMP", "Temperature"], 6)
            cS = pick_col(df, ["CTD - Salinity (PSU)", "CTD - Salinity", "CTD Salinity (PSU)", "CTD Salinity", "PSAL", "Salinity"], 7)
            date_col = pick_col(df, ["Date", "DATE", "date", "Time", "TIME"], None)
            if cP and cT and cS:
                output = []
                for cy in sorted(cycles[valid_mask].unique()):
                    mask = cycles == cy
                    seg = df.loc[mask].copy().reset_index(drop=True)
                    p = to_num(seg[cP])
                    t = to_num(seg[cT])
                    s = to_num(seg[cS])
                    valid = p.notna() & (p > 0)
                    seg_dict = {cP: p[valid].values, cT: t[valid].values, cS: s[valid].values}
                    if date_col and date_col in seg.columns:
                        seg_dict["DATE"] = seg.loc[valid.values, date_col].values
                    seg_clean = pd.DataFrame(seg_dict).reset_index(drop=True)
                    if len(seg_clean) >= 2:
                        output.append(seg_clean)
                if output:
                    print(f"RAW_ASCENT_SPLIT profiles={len(output)} rows={sum(len(o) for o in output)} (split by Cycle number)")
                    return output

    # --- Strategy 2: legacy heuristic (NKE parser output without cycle column) ---
    cP = pick_col(
        df,
        ["CTD - Pressure (dbar)", "CTD Pressure (dbar)", "CTD Pressure", "PRES", "Pressure"],
        5,
    )
    cT = pick_col(
        df,
        ["CTD - Temperature (°C)", "CTD - Temperature (degC)", "CTD - Temperature", "CTD temperature", "CTD Temperature", "TEMP", "Temperature"],
        6,
    )
    cS = pick_col(
        df,
        ["CTD - Salinity (PSU)", "CTD - Salinity", "CTD Salinity (PSU)", "CTD Salinity", "PSAL", "Salinity"],
        7,
    )
    if cP is None or cT is None or cS is None:
        print(f"PROFILE_COLUMNS_NOT_FOUND columns={list(df.columns)}")
        return []

    work = pd.DataFrame({
        "P": to_num(df[cP]),
        "T": to_num(df[cT]),
        "S": to_num(df[cS]),
    }).reset_index(drop=True)

    separator = (
        (work["T"].eq(0.0) & work["S"].eq(25.0))
        | (work["P"].eq(1000.0) & work["T"].eq(0.0) & work["S"].eq(0.0))
    )

    boundaries = [0]
    previous_anchor = np.nan

    for i in range(len(work)):
        if bool(separator.iloc[i]):
            boundaries.extend([i, i + 1])
            previous_anchor = np.nan
            continue

        p = work.at[i, "P"]
        if np.isfinite(p) and np.isfinite(previous_anchor):
            if (p - previous_anchor) > 50.0 and previous_anchor <= 150.0:
                boundaries.append(i)
        if np.isfinite(p):
            previous_anchor = float(p)

    boundaries.append(len(work))
    boundaries = sorted(set(max(0, min(len(work), b)) for b in boundaries))

    output = []
    for a, b in zip(boundaries[:-1], boundaries[1:]):
        seg = work.iloc[a:b].copy().reset_index(drop=True)
        invalid = (
            (seg["T"].eq(0.0) & seg["S"].eq(25.0))
            | (seg["P"].eq(1000.0) & seg["T"].eq(0.0) & seg["S"].eq(0.0))
        )
        seg = seg.loc[~invalid].reset_index(drop=True)
        if len(seg) < 2:
            continue

        p = seg["P"].to_numpy(float)
        anchors = np.flatnonzero(np.isfinite(p))
        if len(anchors) == 0:
            continue

        sample_index = np.arange(len(seg), dtype=float)
        if len(anchors) >= 2:
            filled = np.interp(sample_index, anchors.astype(float), p[anchors])
            first, second = anchors[0], anchors[1]
            last2, last = anchors[-2], anchors[-1]
            slope_start = (p[second] - p[first]) / max(1, second - first)
            slope_end = (p[last] - p[last2]) / max(1, last - last2)
            if first > 0:
                filled[:first] = p[first] + (sample_index[:first] - first) * slope_start
            if last < len(seg) - 1:
                filled[last + 1:] = p[last] + (sample_index[last + 1:] - last) * slope_end
        else:
            anchor = int(anchors[0])
            filled = p[anchor] - (sample_index - anchor)

        seg["P"] = filled
        seg = seg[
            seg["P"].between(-5, 12000)
            & (seg["T"].isna() | seg["T"].between(-5, 45))
            & (seg["S"].isna() | seg["S"].between(0, 50))
        ].copy()

        if len(seg) >= 2:
            output.append(pd.DataFrame({
                cP: seg["P"],
                cT: seg["T"],
                cS: seg["S"],
            }))

    print(f"RAW_ASCENT_SPLIT profiles={len(output)} rows={len(work)}")
    return output

def load_profiles(root: Path, ascent_csv: str, filled_prefix: str, imei: str = "", profile_times: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    """Load and split all ascent profiles.

    Important: an NKE file whose name contains ``filled_1`` is not necessarily
    one oceanographic profile. In batch mode it can contain many profiles
    concatenated vertically. Therefore every ascent CSV is inspected for
    pressure resets/separators and split before plotting.
    """
    records = []
    files = find_ascent_files(root, ascent_csv, filled_prefix)
    profile_number = 0

    for path in files:
        try:
            df = read_semicolon_csv(path)
        except Exception as exc:
            print(f"WARNING: could not read ascent file {path}: {exc}")
            continue

        # Always attempt to split, including files called filled_1.csv.
        frames = _split_raw_ascent_dataframe(df)
        if not frames:
            frames = [df]

        print(f"ASCENT_PROFILES_IN_FILE file={path} profiles={len(frames)}")

        for frame in frames:
            candidate_number = profile_number + 1
            profile_time = (
                profile_times[candidate_number - 1]
                if profile_times and candidate_number <= len(profile_times)
                else pd.NaT
            )

            tmp = _profile_from_dataframe(
                frame,
                path,
                candidate_number,
                imei,
                profile_time=profile_time,
                root=root,
            )
            tmp = tmp.dropna(subset=["PRES"])

            # Require real CTD values, not merely two placeholder rows.
            valid_ctd = tmp["TEMP"].notna() & tmp["PSAL"].notna() & tmp["PRES"].notna()
            if valid_ctd.sum() < 2:
                continue

            profile_number = candidate_number
            records.append(tmp)

    if not records:
        return pd.DataFrame()

    out = pd.concat(records, ignore_index=True)
    out = out.replace([np.inf, -np.inf], np.nan)
    print(
        f"PROFILE_TABLE rows={len(out)} "
        f"profiles={out['PROFILE_ID'].nunique()}"
    )
    return out

def load_positions(root: Path, technical_csv: str, imei: str = "") -> pd.DataFrame:
    """Load all GPS positions for map/KMZ products.

    For NKE Technical Message.csv files, every useful source column is parsed as
    an independent GPS fix. The output keeps a RECORD_TYPE field so plots can
    distinguish real profile positions from extra GPS-only surface positions.
    """
    records = []
    files = find_position_files(root, technical_csv)

    for path in files:
        date_folder = resolve_file_date(path, root)

        # First try the Python decoder flat format (Cycle;Date;...;Latitude;Longitude;GPS valid;...)
        try:
            raw_flat = read_semicolon_csv_raw(path)
            if raw_flat is not None and not raw_flat.empty:
                header_row = [str(c).strip().lower() for c in raw_flat.iloc[0].values]
                if "latitude" in header_row and "longitude" in header_row:
                    lat_col = header_row.index("latitude")
                    lon_col = header_row.index("longitude")
                    cycle_col = next((i for i, h in enumerate(header_row) if "cycle" in h), None)
                    date_col = next((i for i, h in enumerate(header_row) if h == "date"), None)
                    has_profile_col = next((i for i, h in enumerate(header_row) if "has profile" in h or "has_profile" in h), None)

                    flat_rows = []
                    for row_idx in range(1, raw_flat.shape[0]):
                        row = raw_flat.iloc[row_idx]
                        lat_val = safe_float(row.iloc[lat_col])
                        lon_val = safe_float(row.iloc[lon_col])
                        if not (np.isfinite(lat_val) and np.isfinite(lon_val)):
                            continue
                        if abs(lat_val) < 1e-9 and abs(lon_val) < 1e-9:
                            continue

                        cycle_num = 0
                        if cycle_col is not None:
                            cn = safe_float(row.iloc[cycle_col])
                            if np.isfinite(cn):
                                cycle_num = int(cn)

                        t = pd.NaT
                        if date_col is not None:
                            date_str = str(row.iloc[date_col]).strip()
                            if date_str:
                                t = pd.to_datetime(date_str, errors="coerce")

                        # Determine has_profile from CSV column or fallback to cycle_num
                        has_profile_val = False
                        if has_profile_col is not None:
                            hp = safe_float(row.iloc[has_profile_col])
                            has_profile_val = bool(np.isfinite(hp) and int(hp) == 1)
                        else:
                            has_profile_val = cycle_num > 0

                        flat_rows.append({
                            "IMEI": imei,
                            "SOURCE_FILE": str(path),
                            "DATE_FOLDER": date_folder,
                            "TIME": t,
                            "CYCLE_NUMBER": float(cycle_num),
                            "LATITUDE": lat_val,
                            "LONGITUDE": lon_val,
                            "RECORD_TYPE": "profile" if has_profile_val else "gps_surface_only",
                            "HAS_PROFILE": has_profile_val,
                            "SOURCE_COLUMN_INDEX": row_idx,
                        })

                    if flat_rows:
                        records.append(pd.DataFrame(flat_rows))
                        continue
        except Exception:
            pass

        # Then try the NKE column-wise Technical Message structure.
        try:
            raw = read_semicolon_csv_raw(path)
            gps = parse_gps_fixes(raw)
        except Exception:
            raw = None
            gps = pd.DataFrame()

        if not gps.empty and raw is not None:
            n_source_columns = max(int(gps["source_column_index"].max()), raw.shape[1] - 1)
            times_by_column = parse_message_times(raw, date_folder, n_source_columns, root=root)

            rows = []
            for _, fix in gps.iterrows():
                source_idx0 = int(fix["source_column_index"]) - 1
                t = times_by_column[source_idx0] if source_idx0 < len(times_by_column) else pd.NaT
                record_type, has_profile, nonzero_count = classify_fix_from_cycle_timing(raw, source_idx0)
                rows.append({
                    "IMEI": imei,
                    "SOURCE_FILE": str(path),
                    "DATE_FOLDER": date_folder,
                    "TIME": t,
                    "CYCLE_NUMBER": np.nan,
                    "GPS_FIX_INDEX": int(fix["gps_fix_index"]),
                    "SOURCE_COLUMN_INDEX": int(fix["source_column_index"]),
                    "SOURCE_CSV_COLUMN": int(fix["source_csv_column"]),
                    "LATITUDE": float(fix["lat"]),
                    "LONGITUDE": float(fix["lon"]),
                    "RECORD_TYPE": record_type,
                    "HAS_PROFILE": bool(has_profile),
                    "CYCLE_TIMING_NONZERO_COUNT": int(nonzero_count),
                })
            records.append(pd.DataFrame(rows))
            continue

        # Fallback for already-tabular lat/lon position files.
        try:
            df = normalize_columns(read_semicolon_csv(path))
        except Exception:
            continue

        tab = extract_tabular_positions(df)
        if tab is None:
            continue

        if tab["TIME"].isna().all():
            try:
                tab["TIME"] = pd.to_datetime(date_folder, format="%Y%m%d")
            except Exception:
                pass

        tab["IMEI"] = imei
        tab["SOURCE_FILE"] = str(path)
        tab["DATE_FOLDER"] = date_folder
        if "CYCLE_NUMBER" not in tab:
            tab["CYCLE_NUMBER"] = len(records) + 1

        records.append(tab)

    if not records:
        return pd.DataFrame()

    pos = pd.concat(records, ignore_index=True)
    pos = pos.dropna(subset=["LATITUDE", "LONGITUDE"])
    pos = pos[
        (pos["LATITUDE"].between(-90, 90)) &
        (pos["LONGITUDE"].between(-180, 180))
    ].copy()
    pos = pos[~((pos["LATITUDE"].abs() < 1e-12) & (pos["LONGITUDE"].abs() < 1e-12))]

    if pos.empty:
        return pos

    if "HAS_PROFILE" not in pos.columns:
        pos["HAS_PROFILE"] = False
    if "RECORD_TYPE" not in pos.columns:
        pos["RECORD_TYPE"] = np.where(pos["HAS_PROFILE"].astype(bool), "profile", "gps_surface_only")

    pos["CYCLE_NUMBER"] = pd.to_numeric(pos.get("CYCLE_NUMBER", np.nan), errors="coerce")
    sort_cols = ["TIME", "SOURCE_FILE"]
    if "SOURCE_COLUMN_INDEX" in pos.columns:
        sort_cols.append("SOURCE_COLUMN_INDEX")
    elif "GPS_FIX_INDEX" in pos.columns:
        sort_cols.append("GPS_FIX_INDEX")
    pos = pos.sort_values(sort_cols, na_position="last", kind="stable").reset_index(drop=True)
    pos = pos.drop_duplicates(
        subset=["TIME", "LATITUDE", "LONGITUDE"],
        keep="first",
    ).reset_index(drop=True)
    pos["POINT_NUMBER"] = np.arange(1, len(pos) + 1)

    pos["PROFILE_NUMBER"] = np.nan
    profile_counter = 0
    for i in range(len(pos)):
        if bool(pos.loc[i, "HAS_PROFILE"]):
            profile_counter += 1
            pos.loc[i, "PROFILE_NUMBER"] = profile_counter

    return pos

# ---------------------------
# Bathymetry helpers
# ---------------------------
def open_bathy(path: str | None):
    """Open GEBCO, resolving moved application folders automatically."""
    here = Path(__file__).resolve()
    app_root = here.parents[1] if len(here.parents) > 1 else here.parent
    bundled_dir = app_root / "resources" / "gebco"

    candidates = []
    if path:
        configured = Path(str(path).strip().strip('"'))
        candidates.append(configured)
        if not configured.is_absolute():
            candidates.append(app_root / configured)
        if configured.name:
            candidates.append(bundled_dir / configured.name)

    if bundled_dir.exists():
        candidates.extend(sorted(bundled_dir.glob("*.nc")))

    resolved = None
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            resolved = candidate
            break

    if resolved is None:
        print(f"BATHY_NOT_FOUND expected_dir={bundled_dir}")
        return None

    try:
        import xarray as xr
    except Exception as exc:
        print(f"BATHY_DEPENDENCY_MISSING=xarray: {exc}")
        return None

    errors = []
    ds = None
    for engine in (None, "netcdf4", "scipy", "h5netcdf"):
        try:
            kwargs = {} if engine is None else {"engine": engine}
            ds = xr.open_dataset(resolved, **kwargs)
            break
        except Exception as exc:
            errors.append(f"{engine or 'auto'}: {exc}")

    if ds is None:
        print(f"BATHY_OPEN_FAILED file={resolved} errors={' | '.join(errors)}")
        return None

    coord_names = list(ds.coords) + [d for d in ds.dims if d not in ds.coords]
    lon_candidates = [v for v in coord_names if str(v).lower() in ["lon", "longitude", "x"]]
    lat_candidates = [v for v in coord_names if str(v).lower() in ["lat", "latitude", "y"]]
    if not lon_candidates or not lat_candidates:
        print(f"BATHY_COORDINATES_NOT_FOUND available={coord_names}")
        return None

    lon_name = lon_candidates[0]
    lat_name = lat_candidates[0]

    if "elevation" in ds.data_vars:
        z_name = "elevation"
    elif "z" in ds.data_vars:
        z_name = "z"
    else:
        spatial = [
            name for name, da in ds.data_vars.items()
            if lon_name in da.dims and lat_name in da.dims
        ]
        if not spatial:
            print(f"BATHY_VARIABLE_NOT_FOUND available={list(ds.data_vars)}")
            return None
        z_name = spatial[0]

    print(f"BATHY_OPENED={resolved}")
    print(f"BATHY_VARIABLES lon={lon_name} lat={lat_name} z={z_name}")
    return ds, lon_name, lat_name, z_name



def interpolate_bathy_at_points(bathy, lon, lat):
    if bathy is None:
        return None
    try:
        import xarray as xr
        ds, lon_name, lat_name, z_name = bathy
        lons = xr.DataArray(np.asarray(lon, dtype=float), dims="points")
        lats = xr.DataArray(np.asarray(lat, dtype=float), dims="points")
        z = ds[z_name].interp({lon_name: lons, lat_name: lats}, method="linear").values
        return -np.asarray(z, dtype=float)
    except Exception as exc:
        print(f"WARNING: could not interpolate bathymetry: {exc}")
        return None


def get_bathy_grid(bathy, xmin, xmax, ymin, ymax, max_cells=900):
    """
    Return a bathymetry subset for the requested map extent without loading the
    full global GEBCO grid in memory.

    This is important for global GEBCO files, where reading ds[z_name].values
    would allocate tens of GB. The function first subsets with xarray and only
    then converts the small map window to numpy.
    """
    if bathy is None:
        print("BATHY_SKIPPED=no bathymetry file loaded")
        return None

    try:
        ds, lon_name, lat_name, z_name = bathy

        lons = np.asarray(ds[lon_name].values, dtype=float)
        lats = np.asarray(ds[lat_name].values, dtype=float)

        lon_min, lon_max = float(np.nanmin(lons)), float(np.nanmax(lons))
        lat_min, lat_max = float(np.nanmin(lats)), float(np.nanmax(lats))

        print(
            f"BATHY_LOADED lon=[{lon_min:.4f},{lon_max:.4f}] "
            f"lat=[{lat_min:.4f},{lat_max:.4f}] "
            f"map_lon=[{xmin:.4f},{xmax:.4f}] map_lat=[{ymin:.4f},{ymax:.4f}]"
        )

        if xmax < lon_min or xmin > lon_max or ymax < lat_min or ymin > lat_max:
            print("BATHY_SKIPPED=map extent is outside the GEBCO subset")
            return None

        # Clip request to the bathymetry domain.
        req_xmin = max(xmin, lon_min)
        req_xmax = min(xmax, lon_max)
        req_ymin = max(ymin, lat_min)
        req_ymax = min(ymax, lat_max)

        lon_slice = slice(req_xmin, req_xmax) if lons[0] <= lons[-1] else slice(req_xmax, req_xmin)
        lat_slice = slice(req_ymin, req_ymax) if lats[0] <= lats[-1] else slice(req_ymax, req_ymin)

        sub = ds[z_name].sel({lon_name: lon_slice, lat_name: lat_slice})

        # Downsample only after subsetting. This keeps global GEBCO usable and
        # avoids huge pcolormesh arrays for large map extents.
        nlat = int(sub.sizes.get(lat_name, 0))
        nlon = int(sub.sizes.get(lon_name, 0))
        if nlat < 2 or nlon < 2:
            print(f"BATHY_SKIPPED=not enough GEBCO grid cells in map extent lon_cells={nlon} lat_cells={nlat}")
            return None

        lat_step = max(1, int(np.ceil(nlat / max_cells)))
        lon_step = max(1, int(np.ceil(nlon / max_cells)))
        if lat_step > 1 or lon_step > 1:
            sub = sub.isel({lat_name: slice(None, None, lat_step), lon_name: slice(None, None, lon_step)})
            print(f"BATHY_DOWNSAMPLED lat_step={lat_step} lon_step={lon_step}")

        blon = np.asarray(sub[lon_name].values, dtype=float)
        blat = np.asarray(sub[lat_name].values, dtype=float)
        z = np.asarray(sub.values, dtype=float)

        # If the variable dimensions are lon,lat instead of lat,lon, transpose.
        dims = list(sub.dims)
        if dims == [lon_name, lat_name]:
            z = z.T

        print(
            f"BATHY_GRID cells={z.shape[0]}x{z.shape[1]} "
            f"z=[{np.nanmin(z):.1f},{np.nanmax(z):.1f}]"
        )

        return blon, blat, z

    except Exception as exc:
        print(f"WARNING: could not subset bathymetry: {exc}")
        return None



# ---------------------------
# Plot helpers
# ---------------------------
def style_ax(ax):
    ax.grid(False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def compute_sigma0(S, T):
    """Compute potential density anomaly using TEOS-10/GSW.

    For quick-look purposes the parser salinity and temperature are passed to
    ``gsw.sigma0`` directly. The dependency is mandatory so the software never
    silently substitutes a non-oceanographic approximation.
    """
    import gsw
    return gsw.sigma0(S, T)


def get_limits(x, pad_frac=0.04, nice=0.1):
    arr = np.asarray(x, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0, 1
    lo, hi = np.nanmin(arr), np.nanmax(arr)
    if math.isclose(lo, hi):
        lo -= 0.5
        hi += 0.5
    pad = (hi - lo) * pad_frac
    return np.floor((lo - pad) / nice) * nice, np.ceil((hi + pad) / nice) * nice


def get_cycle_coloring(labels, cmap_name="turbo", max_cbar_ticks=12):
    all_labels = np.array(sorted(pd.unique(labels)))
    n = len(all_labels)
    base = plt.colormaps.get_cmap(cmap_name)
    colors = base(np.linspace(0, 1, max(n, 1)))
    cmap = mpl.colors.ListedColormap(colors)
    label_to_color = {lab: colors[i] for i, lab in enumerate(all_labels)}

    norm = mpl.colors.BoundaryNorm(np.arange(-0.5, n + 0.5, 1), cmap.N)
    sm = mpl.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])

    step = max(1, int(np.ceil(max(n, 1) / max_cbar_ticks)))
    tick_idx = np.arange(0, n, step)
    if len(tick_idx) and tick_idx[-1] != n - 1:
        tick_idx = np.append(tick_idx, n - 1)
    if len(tick_idx) == 0:
        tick_idx = np.array([0])
    tick_labels = all_labels[tick_idx] if n else []

    return all_labels, label_to_color, sm, tick_idx, tick_labels


# ---------------------------
# Products
# ---------------------------
def plot_ts(profiles: pd.DataFrame, outdir: Path, imei: str, cmap_name="turbo", max_cbar_ticks=12):
    df = profiles.dropna(subset=["PRES", "TEMP", "PSAL"]).copy()
    if df.empty:
        print("TS_SKIPPED=no valid TEMP/PSAL/PRES data")
        return

    labels = df["PROFILE_ID"]
    all_profiles, color_by_profile, sm, tick_idx, tick_labels = get_cycle_coloring(labels, cmap_name, max_cbar_ticks)

    smin, smax = get_limits(df["PSAL"], pad_frac=0.05, nice=0.01)
    tmin, tmax = get_limits(df["TEMP"], pad_frac=0.05, nice=0.1)

    s_grid = np.linspace(smin, smax, 250)
    t_grid = np.linspace(tmin, tmax, 250)
    Sg, Tg = np.meshgrid(s_grid, t_grid)
    sigma = compute_sigma0(Sg, Tg)
    sigma_levels = np.arange(
        np.floor(np.nanmin(sigma) * 2) / 2,
        np.ceil(np.nanmax(sigma) * 2) / 2 + 0.001,
        0.5
    )

    fig, ax = plt.subplots(figsize=(7.4, 6.2))

    cs = ax.contour(Sg, Tg, sigma, levels=sigma_levels, colors="0.65", linewidths=0.8)
    ax.clabel(cs, fontsize=8, fmt="%.1f")

    for pid in all_profiles:
        g = df[df["PROFILE_ID"] == pid].sort_values("PRES")
        color = color_by_profile[pid]
        ax.plot(g["PSAL"], g["TEMP"], color=color, linewidth=1.15, alpha=0.9)
        # Keep markers sparse so dense profiles remain readable.
        step=max(1,len(g)//45)
        gm=g.iloc[::step]
        ax.scatter(gm["PSAL"], gm["TEMP"], color=[color], s=10, alpha=0.75, linewidths=0)

    ax.set_xlim(smin, smax)
    ax.set_ylim(tmin, tmax)
    ax.set_xlabel("Salinity")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title(f"TS diagram - IMEI {imei}" if imei else "TS diagram")
    style_ax(ax)

    cbar = fig.colorbar(sm, ax=ax, ticks=tick_idx, fraction=0.046, pad=0.04)
    # Strip the _XX profile number suffix for cleaner colorbar labels
    display_labels = [str(lbl).rsplit("_", 1)[0] if "_" in str(lbl) else str(lbl) for lbl in tick_labels]
    cbar.ax.set_yticklabels(display_labels)
    cbar.set_label("Profile")

    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"TS_IMEI_{imei or 'unknown'}.png"
    fig.tight_layout()
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"TS_PLOT={out}")


def make_pressure_grid(max_pres, step=2):
    """Pressure grid from surface to depth; plots invert the y-axis."""
    return np.arange(0.0, float(max_pres) + float(step), float(step))


def make_section(df: pd.DataFrame, varname: str, pgrid: np.ndarray, profile_ids: list[str]) -> pd.DataFrame:
    section = pd.DataFrame(index=pgrid, columns=profile_ids, dtype=float)
    for pid in profile_ids:
        g = df[df["PROFILE_ID"] == pid].sort_values("PRES")
        x = g["PRES"].to_numpy(float)
        y = g[varname].to_numpy(float)
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 2:
            continue
        x = x[mask]
        y = y[mask]
        x_unique, idx = np.unique(x, return_index=True)
        y_unique = y[idx]
        if len(x_unique) < 2:
            continue
        interp = np.interp(pgrid, x_unique, y_unique)
        interp[pgrid < x_unique.min()] = np.nan
        interp[pgrid > x_unique.max()] = np.nan
        section[pid] = interp
    return section


def plot_section_one(profiles: pd.DataFrame, pos: pd.DataFrame | None, outdir: Path, imei: str, max_pres, label, bathy=None, temp_cmap="RdYlBu_r", psal_cmap="viridis"):
    df = profiles.dropna(subset=["PRES"]).copy()
    df = df[df["PRES"] <= max_pres].copy()
    if df.empty:
        print(f"SECTION_SKIPPED_{label}=no data within requested pressure range")
        return

    meta = (
        df.sort_values(["TIME", "PROFILE_ID"])
          .groupby("PROFILE_ID", as_index=False)
          .first()[["PROFILE_ID", "TIME"]]
    )
    profile_ids = meta["PROFILE_ID"].tolist()
    if len(profile_ids) < 2:
        print(f"SECTION_SKIPPED_{label}=at least two profiles are required")
        return
    pgrid = make_pressure_grid(max_pres=max_pres, step=2)

    temp = make_section(df.dropna(subset=["TEMP"]), "TEMP", pgrid, profile_ids)
    psal = make_section(df.dropna(subset=["PSAL"]), "PSAL", pgrid, profile_ids)

    if temp.isna().all().all() and psal.isna().all().all():
        print(f"SECTION_SKIPPED_{label}=no valid TEMP/PSAL sections")
        return

    sigma = pd.DataFrame(index=temp.index, columns=temp.columns, dtype=float)
    for c in temp.columns:
        T = temp[c].to_numpy(float)
        S = psal[c].to_numpy(float)
        mask = np.isfinite(T) & np.isfinite(S)
        out = np.full(T.shape, np.nan)
        if mask.any():
            out[mask] = compute_sigma0(S[mask], T[mask])
        sigma[c] = out

    # Use the real profile timestamps on the horizontal axis. This represents
    # the temporal evolution directly and avoids distortions caused by variable
    # drift speed or incomplete navigation positions.
    profile_times = pd.to_datetime(meta["TIME"], errors="coerce")
    if profile_times.notna().sum() < 2:
        print(f"SECTION_SKIPPED_{label}=at least two valid profile dates are required")
        return

    # Matplotlib date numbers remain numeric, so pcolormesh and contour can use
    # them directly while the axis is formatted as calendar dates.
    x = mdates.date2num(np.array(profile_times.dt.to_pydatetime()))
    x_label = "Date"
    X, Y = np.meshgrid(x, pgrid)

    tvals = temp.to_numpy(float)
    svals = psal.to_numpy(float)

    # Do not imply continuity across long temporal gaps. Columns following a
    # gap greater than three times the median cycle interval are masked.
    dt_days = np.diff(x)
    finite_dt = dt_days[np.isfinite(dt_days) & (dt_days > 0)]
    if finite_dt.size:
        gap_limit = max(3.0 * float(np.median(finite_dt)), 1.0)
        gap_cols = np.where(dt_days > gap_limit)[0] + 1
        tvals[:, gap_cols] = np.nan
        svals[:, gap_cols] = np.nan

    tmin, tmax = get_limits(tvals, pad_frac=0.02, nice=0.5)
    smin, smax = get_limits(svals, pad_frac=0.02, nice=0.1)

    fig, axes = plt.subplots(2, 1, figsize=(10.5, 7.6), sharex=True)
    fig.subplots_adjust(right=0.88, hspace=0.15)

    pcm_temp = axes[0].pcolormesh(X, Y, tvals, shading="auto", cmap=temp_cmap, vmin=tmin, vmax=tmax)
    temp_levels = np.arange(np.floor(tmin), np.ceil(tmax) + 0.001, 1.0)
    if len(temp_levels) > 1:
        cs = axes[0].contour(X, Y, tvals, levels=temp_levels, colors="k", linewidths=0.7)
        axes[0].clabel(cs, fmt="%.0f", fontsize=7)

    sig_vals = sigma.to_numpy(float)
    if np.isfinite(sig_vals).sum() > 8:
        sig_levels = np.arange(
            np.floor(np.nanmin(sig_vals) * 2) / 2,
            np.ceil(np.nanmax(sig_vals) * 2) / 2 + 0.001,
            0.5
        )
        if len(sig_levels) > 1:
            cs_sig = axes[0].contour(X, Y, sig_vals, levels=sig_levels, colors="white", linestyles="--", linewidths=0.8)
            axes[0].clabel(cs_sig, fmt="%.1f", fontsize=6, colors="white")
    else:
        sig_levels = []

    axes[0].set_ylabel("Pressure (dbar)")
    axes[0].set_title(f"Temperature section ({label}) - IMEI {imei}" if imei else f"Temperature section ({label})")
    axes[0].set_ylim(float(max_pres), 0.0)

    pcm_psal = axes[1].pcolormesh(X, Y, svals, shading="auto", cmap=psal_cmap, vmin=smin, vmax=smax)
    psal_levels = np.arange(np.floor(smin * 2) / 2, np.ceil(smax * 2) / 2 + 0.001, 0.5)
    if len(psal_levels) > 1:
        cs = axes[1].contour(X, Y, svals, levels=psal_levels, colors="k", linewidths=0.7)
        axes[1].clabel(cs, fmt="%.1f", fontsize=7)

    if np.isfinite(sig_vals).sum() > 8 and len(sig_levels) > 1:
        cs_sig = axes[1].contour(X, Y, sig_vals, levels=sig_levels, colors="white", linestyles="--", linewidths=0.8)
        axes[1].clabel(cs_sig, fmt="%.1f", fontsize=6, colors="white")

    axes[1].set_ylabel("Pressure (dbar)")
    axes[1].set_title(f"Salinity section ({label}) - IMEI {imei}" if imei else f"Salinity section ({label})")
    axes[1].set_ylim(float(max_pres), 0.0)

    # Optional bathymetry is sampled only at profile fixes and plotted against
    # the corresponding profile dates.
    if bathy is not None and pos is not None and not pos.empty:
        profile_pos = pos[pos["HAS_PROFILE"].astype(bool)].copy() if "HAS_PROFILE" in pos.columns else pos.copy()
        profile_pos = profile_pos.sort_values("TIME").reset_index(drop=True)
        if len(profile_pos) >= len(profile_ids):
            pp = profile_pos.iloc[:len(profile_ids)]
            bottom = interpolate_bathy_at_points(bathy, pp["LONGITUDE"].values, pp["LATITUDE"].values)
            if bottom is not None:
                bottom_plot = np.minimum(bottom, max_pres)
                valid = np.isfinite(bottom_plot)
                for ax in axes:
                    ax.fill_between(x[valid], bottom_plot[valid], max_pres, color="0.3", alpha=0.22, zorder=5)
                    ax.plot(x[valid], bottom_plot[valid], color="k", linewidth=0.8, zorder=6)

    n_ticks=min(8,len(profile_ids))
    tick_idx=np.unique(np.linspace(0,len(profile_ids)-1,n_ticks).round().astype(int))
    axes[1].set_xticks(x[tick_idx])
    axes[1].xaxis_date()
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%d-%b-%Y"))
    axes[1].set_xticklabels(
        [pd.Timestamp(profile_times.iloc[i]).strftime("%d-%b-%Y") for i in tick_idx],
        rotation=45,
        ha="right",
    )
    axes[1].set_xlabel(x_label)

    for ax in axes:
        for xv in x:
            ax.axvline(xv, color="white", linewidth=0.35, alpha=0.45, zorder=4)
        style_ax(ax)

    cax_temp = fig.add_axes([0.90, 0.56, 0.018, 0.30])
    cbar_temp = fig.colorbar(pcm_temp, cax=cax_temp)
    cbar_temp.set_label("Temperature (°C)")

    cax_psal = fig.add_axes([0.90, 0.16, 0.018, 0.30])
    cbar_psal = fig.colorbar(pcm_psal, cax=cax_psal)
    cbar_psal.set_label("Salinity")

    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"section_TEMP_PSAL_IMEI_{imei or 'unknown'}_{label}.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"SECTION_PLOT={out}")


def plot_sections(
    profiles: pd.DataFrame,
    positions: pd.DataFrame | None,
    outdir: Path,
    imei: str,
    max_pres=300,
    bathy=None,
    temp_cmap="RdYlBu_r",
    psal_cmap="viridis",
):
    """Generate full-depth and upper-300-dbar section figures."""
    if profiles.empty:
        print("SECTIONS_SKIPPED=no profile data")
        return

    finite_p = pd.to_numeric(profiles["PRES"], errors="coerce").to_numpy(float)
    finite_p = finite_p[np.isfinite(finite_p)]
    if finite_p.size == 0:
        print("SECTIONS_SKIPPED=no pressure data")
        return

    observed_max = float(np.nanmax(finite_p))
    full_depth = max(10.0, float(np.ceil(observed_max / 10.0) * 10.0))
    upper_depth = min(300.0, full_depth)

    plot_section_one(
        profiles, positions, outdir, imei,
        max_pres=full_depth,
        label=f"full_depth_0_{int(full_depth)}dbar",
        bathy=bathy,
        temp_cmap=temp_cmap,
        psal_cmap=psal_cmap,
    )
    plot_section_one(
        profiles, positions, outdir, imei,
        max_pres=upper_depth,
        label=f"upper_0_{int(upper_depth)}dbar",
        bathy=bathy,
        temp_cmap=temp_cmap,
        psal_cmap=psal_cmap,
    )
    print(f"SECTIONS_DEPTHS full={full_depth:.1f}dbar upper={upper_depth:.1f}dbar")


def plot_map(pos: pd.DataFrame, outdir: Path, imei: str, cmap_name="viridis", max_cbar_ticks=12, bounds=None, bathy=None):
    if pos.empty:
        print("MAP_SKIPPED=no valid positions")
        return

    g = pos.copy()

    if bounds:
        xmin, xmax, ymin, ymax = bounds
    else:
        lon_pad = max(0.05, (g["LONGITUDE"].max() - g["LONGITUDE"].min()) * 0.25)
        lat_pad = max(0.05, (g["LATITUDE"].max() - g["LATITUDE"].min()) * 0.25)
        xmin, xmax = g["LONGITUDE"].min() - lon_pad, g["LONGITUDE"].max() + lon_pad
        ymin, ymax = g["LATITUDE"].min() - lat_pad, g["LATITUDE"].max() + lat_pad

    labels = g["POINT_NUMBER"]
    all_points, color_by_point, sm, tick_idx, tick_labels = get_cycle_coloring(labels, cmap_name, max_cbar_ticks)

    fig, ax = plt.subplots(figsize=(8.7, 7.8))
    fig.subplots_adjust(right=0.86)

    bathy_grid = get_bathy_grid(bathy, xmin, xmax, ymin, ymax)
    cf = None
    if bathy_grid is not None:
        blon, blat, z = bathy_grid

        # Plot bathymetry robustly. Negative GEBCO values are ocean depth;
        # positive values are land elevation. Using pcolormesh makes the layer
        # visible even when the local depth range does not match fixed contours.
        ocean = np.where(z <= 0, z, np.nan)
        if np.isfinite(ocean).sum() > 0:
            cf = ax.pcolormesh(
                blon,
                blat,
                ocean,
                shading="auto",
                cmap="Blues_r",
                zorder=0
            )

            contour_levels = [lev for lev in [-2000, -1000, -500, -200, -100, -50] if np.nanmin(ocean) <= lev <= np.nanmax(ocean)]
            if contour_levels:
                ax.contour(
                    blon,
                    blat,
                    ocean,
                    levels=contour_levels,
                    colors="0.55",
                    linewidths=0.5,
                    zorder=1
                )
        else:
            print("BATHY_WARNING=GEBCO subset has no ocean cells with elevation <= 0")

        # Coastline / zero contour if present.
        try:
            if np.nanmin(z) <= 0 <= np.nanmax(z):
                ax.contour(blon, blat, z, levels=[0], colors="k", linewidths=0.8, zorder=2)
        except Exception:
            pass

    ax.plot(g["LONGITUDE"], g["LATITUDE"], linewidth=1.2, alpha=0.85, color="k", zorder=3)

    gps_only = g[~g.get("HAS_PROFILE", False).astype(bool)] if "HAS_PROFILE" in g.columns else g
    profiles = g[g.get("HAS_PROFILE", False).astype(bool)] if "HAS_PROFILE" in g.columns else g.iloc[0:0]

    # Base colored points by navigation order.
    for pnum in all_points:
        gg = g[g["POINT_NUMBER"] == pnum]
        ax.scatter(
            gg["LONGITUDE"], gg["LATITUDE"],
            s=46,
            color=[color_by_point[pnum]],
            edgecolor="k",
            linewidth=0.35,
            zorder=4
        )

    # Overlay record-type styling so GPS-only and profile fixes are distinguishable.
    if len(gps_only):
        ax.scatter(
            gps_only["LONGITUDE"], gps_only["LATITUDE"],
            marker=".",
            s=42,
            color="0.35",
            alpha=0.70,
            zorder=5,
            label="GPS position"
        )

    if len(profiles):
        ax.scatter(
            profiles["LONGITUDE"], profiles["LATITUDE"],
            marker="o",
            s=92,
            facecolor="none",
            edgecolor="red",
            linewidth=1.2,
            zorder=6,
            label="Profile"
        )

    if len(g) > 0:
        ax.scatter(
            g.iloc[0]["LONGITUDE"], g.iloc[0]["LATITUDE"],
            marker="*",
            s=180,
            color="yellow",
            edgecolor="k",
            linewidth=0.8,
            zorder=8,
            label="First position"
        )
        ax.scatter(
            g.iloc[-1]["LONGITUDE"], g.iloc[-1]["LATITUDE"],
            marker="o",
            s=110,
            color="red",
            edgecolor="k",
            linewidth=0.8,
            zorder=8,
            label="Last position"
        )

    # Do not annotate point/profile numbers on the map: for dense recovery
    # trajectories they add visual noise and obscure the track.

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"Trajectory - IMEI {imei}" if imei else "Trajectory")
    ax.grid(True, alpha=0.25)
    style_ax(ax)
    ax.legend(loc="best")

    cax = fig.add_axes([0.88, 0.50, 0.025, 0.34])
    cbar = fig.colorbar(sm, cax=cax, ticks=tick_idx)
    cbar.ax.set_yticklabels(tick_labels)
    cbar.set_label("Position number")

    if cf is not None:
        cax2 = fig.add_axes([0.88, 0.12, 0.025, 0.28])
        cbar2 = fig.colorbar(cf, cax=cax2)
        cbar2.set_label("Bathymetry (m)")

    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"map_IMEI_{imei or 'unknown'}.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"MAP_PLOT={out}")

def make_kml(pos: pd.DataFrame, name="NKE trajectory"):
    """Create an open KML trajectory with consecutive segments."""
    ordered = pos.copy().reset_index(drop=True)

    distances = []
    for i in range(1, len(ordered)):
        lat1 = float(ordered.loc[i - 1, "LATITUDE"])
        lon1 = float(ordered.loc[i - 1, "LONGITUDE"])
        lat2 = float(ordered.loc[i, "LATITUDE"])
        lon2 = float(ordered.loc[i, "LONGITUDE"])
        r = 6371.0088
        p1, p2 = np.radians(lat1), np.radians(lat2)
        dp = np.radians(lat2 - lat1)
        dl = np.radians(lon2 - lon1)
        a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
        distances.append(2 * r * np.arctan2(np.sqrt(a), np.sqrt(max(0.0, 1 - a))))

    finite = np.asarray([d for d in distances if np.isfinite(d) and d > 0], dtype=float)
    median_step = float(np.nanmedian(finite)) if finite.size else np.nan
    jump_limit = max(20.0, 8.0 * median_step) if np.isfinite(median_step) else 50.0

    segments = []
    for i in range(1, len(ordered)):
        if not np.isfinite(distances[i - 1]) or distances[i - 1] > jump_limit:
            continue
        a = ordered.iloc[i - 1]
        b = ordered.iloc[i]
        segments.append(f"""    <Placemark>
      <name>Track segment {i}</name>
      <styleUrl>#trackStyle</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
          {a.LONGITUDE:.8f},{a.LATITUDE:.8f},0
          {b.LONGITUDE:.8f},{b.LATITUDE:.8f},0
        </coordinates>
      </LineString>
    </Placemark>""")

    placemarks = []
    for r in ordered.itertuples():
        is_profile = bool(getattr(r, "HAS_PROFILE", False))
        profile_number = getattr(r, "PROFILE_NUMBER", np.nan)
        point_number = int(getattr(r, "POINT_NUMBER"))
        if is_profile and np.isfinite(profile_number):
            label = f"P{int(profile_number)}"
            style = "#profileStyle"
        else:
            label = f"G{point_number}"
            style = "#gpsStyle"
        placemarks.append(f"""    <Placemark>
      <name>{label}</name>
      <styleUrl>{style}</styleUrl>
      <Point><coordinates>{r.LONGITUDE:.8f},{r.LATITUDE:.8f},0</coordinates></Point>
    </Placemark>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name}</name>
    <Style id="trackStyle"><LineStyle><width>3</width></LineStyle></Style>
    <Style id="profileStyle">
      <IconStyle><scale>1.1</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle>
    </Style>
    <Style id="gpsStyle">
      <IconStyle><scale>0.9</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/blu-circle.png</href></Icon></IconStyle>
    </Style>
{chr(10).join(segments)}
{chr(10).join(placemarks)}
  </Document>
</kml>
"""

def write_kmz(pos: pd.DataFrame, outdir: Path, imei: str):
    if pos.empty:
        print("KMZ_SKIPPED=no valid positions")
        return

    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    kmz_path = outdir / f"trajectory_IMEI_{imei or 'unknown'}_{stamp}.kmz"
    kml = make_kml(pos, name=f"NKE trajectory IMEI {imei}")

    with zipfile.ZipFile(kmz_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml)

    clean_csv = outdir / f"trajectory_IMEI_{imei or 'unknown'}_{stamp}.csv"
    pos.to_csv(clean_csv, index=False)

    n_profile = int(pos["HAS_PROFILE"].astype(bool).sum()) if "HAS_PROFILE" in pos.columns else 0
    n_gps = int((~pos["HAS_PROFILE"].astype(bool)).sum()) if "HAS_PROFILE" in pos.columns else len(pos)
    print(f"KMZ={kmz_path}")
    print(f"TRAJECTORY_CSV={clean_csv}")
    print(f"TRAJECTORY_LAYER_COUNTS profile={n_profile} gps_position={n_gps} total_positions={len(pos)}")

def parse_bounds(args):
    values = [args.xmin, args.xmax, args.ymin, args.ymax]
    if any(v is None for v in values):
        return None
    try:
        return tuple(float(v) for v in values)
    except Exception:
        return None


def main():
    settings = load_settings()

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--imei", default=settings.get("last_imei", ""))
    ap.add_argument("--technical_csv", default=settings.get("technical_csv_name", "Technical Message.csv"))
    ap.add_argument("--ascent_csv", default=settings.get("ascent_csv_name", "Ascent profile CTD Message.csv"))
    ap.add_argument("--filled_prefix", default=settings.get("filled_profile_prefix", "Ascent profile CTD Message_filled_"))
    ap.add_argument("--max_pres", type=float, default=float(settings.get("max_section_pressure", 300) or 300))
    ap.add_argument("--profile_cmap", default=settings.get("profile_cmap", "turbo"))
    ap.add_argument("--map_cmap", default=settings.get("map_cmap", "viridis"))
    ap.add_argument("--temp_cmap", default=settings.get("section_temp_cmap", "RdYlBu_r"))
    ap.add_argument("--psal_cmap", default=settings.get("section_psal_cmap", "viridis"))
    ap.add_argument("--bathymetry_file", default=settings.get("bathymetry_file", ""))
    ap.add_argument("--xmin", default=settings.get("map_xmin", None) or None)
    ap.add_argument("--xmax", default=settings.get("map_xmax", None) or None)
    ap.add_argument("--ymin", default=settings.get("map_ymin", None) or None)
    ap.add_argument("--ymax", default=settings.get("map_ymax", None) or None)
    ap.add_argument("--products", nargs="+", default=["ts", "sections", "map", "kmz"], choices=["ts", "sections", "map", "kmz"])
    args = ap.parse_args()

    root = Path(args.root)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Create only the product folders requested in this run.
    requested_subdirs = {
        "ts": "profiles",
        "sections": "sections",
        "map": "maps",
        "kmz": "kmz",
    }
    for product in args.products:
        subdir = requested_subdirs.get(product)
        if subdir:
            (outdir / subdir).mkdir(parents=True, exist_ok=True)
    imei = str(args.imei or "").strip()
    any_input_found = False

    if not root.exists():
        raise SystemExit(f"ERROR: root folder does not exist: {root}")

    bathy = open_bathy(args.bathymetry_file)

    profiles = None
    positions = None

    # Load positions first whenever profile products are requested. In flat
    # batch mode their Technical Message timestamps provide the real profile
    # dates that daily-folder workflows previously supplied.
    if any(p in args.products for p in ["map", "kmz", "sections", "ts"]):
        positions = load_positions(root, args.technical_csv, imei=imei)
        if positions.empty:
            print("POSITIONS_NOT_FOUND")
        else:
            any_input_found = True
            print(f"POSITIONS_LOADED points={len(positions)}")
            print("POSITIONS_PREVIEW")
            preview_cols = [c for c in ["POINT_NUMBER", "RECORD_TYPE", "HAS_PROFILE", "LATITUDE", "LONGITUDE"] if c in positions.columns]
            print(positions[preview_cols].head().to_string(index=False))

    if any(p in args.products for p in ["ts", "sections"]):
        profile_times = []
        if positions is not None and not positions.empty and "HAS_PROFILE" in positions:
            profile_times = positions.loc[positions["HAS_PROFILE"].astype(bool), "TIME"].dropna().sort_values().tolist()
        profiles = load_profiles(root, args.ascent_csv, args.filled_prefix, imei=imei, profile_times=profile_times)
        if profiles.empty:
            print("PROFILES_NOT_FOUND")
        else:
            any_input_found = True
            print(f"PROFILES_LOADED rows={len(profiles)} profiles={profiles['PROFILE_ID'].nunique()}")

    if "ts" in args.products and profiles is not None and not profiles.empty:
        plot_ts(profiles, outdir / "profiles", imei=imei, cmap_name=args.profile_cmap)

    if "sections" in args.products and profiles is not None and not profiles.empty:
        plot_sections(profiles, positions, outdir / "sections", imei=imei, max_pres=args.max_pres, bathy=bathy, temp_cmap=args.temp_cmap, psal_cmap=args.psal_cmap)

    if "map" in args.products and positions is not None and not positions.empty:
        plot_map(positions, outdir / "maps", imei=imei, cmap_name=args.map_cmap, bounds=parse_bounds(args), bathy=bathy)

    if "kmz" in args.products and positions is not None and not positions.empty:
        write_kmz(positions, outdir / "kmz", imei=imei)

    print("PRODUCTS_DONE")
    if not any_input_found:
        raise SystemExit("ERROR: no profiles or positions were found. Check decoded outputs and file names.")


if __name__ == "__main__":
    main()
