#!/usr/bin/env python3
"""Build normalized, quality-controlled and traceable SOCIB Argo products.

This module deliberately reuses the proven NKE readers from
``generate_quicklook_products`` and writes a stable intermediate data model.
Raw parser outputs are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from common import load_settings
from generate_quicklook_products import load_positions, load_profiles

SOFTWARE_VERSION = "4.0.0"
QC_GOOD, QC_PROBABLY_GOOD, QC_PROBABLY_BAD, QC_BAD, QC_MISSING = 1, 2, 3, 4, 9


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def qc_range(values: pd.Series, low: float, high: float) -> np.ndarray:
    a = pd.to_numeric(values, errors="coerce").to_numpy(float)
    q = np.full(a.shape, QC_GOOD, dtype=np.int8)
    q[~np.isfinite(a)] = QC_MISSING
    q[np.isfinite(a) & ((a < low) | (a > high))] = QC_BAD
    return q


def add_profile_qc(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    out["PRES_QC"] = qc_range(out["PRES"], cfg["pressure_min"], cfg["pressure_max"])
    out["TEMP_QC"] = qc_range(out["TEMP"], cfg["temperature_min"], cfg["temperature_max"])
    out["PSAL_QC"] = qc_range(out["PSAL"], cfg["salinity_min"], cfg["salinity_max"])
    out["PROFILE_QC"] = QC_GOOD
    source = out["SOURCE_FILE"].astype(str) if "SOURCE_FILE" in out else pd.Series("", index=out.index)
    out["PRES_SOURCE"] = np.where(source.str.contains("filled", case=False, na=False), "reconstructed_or_filled", "parser_output")

    for pid, idx in out.groupby("PROFILE_ID").groups.items():
        g = out.loc[idx].sort_values("PRES")
        if len(g) < cfg["min_profile_levels"]:
            out.loc[idx, "PROFILE_QC"] = QC_PROBABLY_BAD
        p = pd.to_numeric(g["PRES"], errors="coerce").to_numpy(float)
        if np.isfinite(p).sum() > 2 and np.nanmin(np.diff(p[np.isfinite(p)])) < -cfg["pressure_reversal_tolerance"]:
            out.loc[idx, "PROFILE_QC"] = QC_PROBABLY_BAD
        for col, threshold, qc_col in [("TEMP", cfg["max_temp_step"], "TEMP_QC"), ("PSAL", cfg["max_sal_step"], "PSAL_QC")]:
            v = pd.to_numeric(g[col], errors="coerce").to_numpy(float)
            jumps = np.r_[False, np.abs(np.diff(v)) > threshold]
            bad_rows = g.index[jumps & np.isfinite(v)]
            out.loc[bad_rows, qc_col] = np.maximum(out.loc[bad_rows, qc_col], QC_PROBABLY_BAD)
    return out


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371000.0
    a1, a2 = np.radians(lat1), np.radians(lat2)
    da = a2 - a1
    dl = np.radians(lon2 - lon1)
    a = np.sin(da / 2) ** 2 + np.cos(a1) * np.cos(a2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def add_navigation_qc(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy().sort_values("TIME").reset_index(drop=True)
    out["POSITION_QC"] = QC_GOOD
    valid = out["LATITUDE"].between(-90, 90) & out["LONGITUDE"].between(-180, 180)
    out.loc[~valid, "POSITION_QC"] = QC_BAD
    t = pd.to_datetime(out["TIME"], errors="coerce", utc=True)
    out["DELTA_TIME_H"] = t.diff().dt.total_seconds() / 3600
    out["DISTANCE_M"] = np.nan
    out["SPEED_MS"] = np.nan
    if len(out) > 1:
        d = haversine_m(out["LONGITUDE"].shift(), out["LATITUDE"].shift(), out["LONGITUDE"], out["LATITUDE"])
        out["DISTANCE_M"] = d
        out["SPEED_MS"] = d / (out["DELTA_TIME_H"] * 3600)
        out.loc[out["SPEED_MS"] > cfg["max_surface_speed_ms"], "POSITION_QC"] = QC_PROBABLY_BAD
        out.loc[out["DELTA_TIME_H"] <= 0, "POSITION_QC"] = QC_BAD
    return out


def write_netcdf(profiles: pd.DataFrame, nav: pd.DataFrame, path: Path, imei: str):
    pids = list(pd.unique(profiles["PROFILE_ID"]))
    max_levels = max((len(profiles[profiles["PROFILE_ID"] == p]) for p in pids), default=0)
    shape = (len(pids), max_levels)
    fill = lambda: np.full(shape, np.nan, dtype=np.float32)
    pres, temp, psal = fill(), fill(), fill()
    pres_qc = np.full(shape, QC_MISSING, dtype=np.int8)
    temp_qc = np.full(shape, QC_MISSING, dtype=np.int8)
    psal_qc = np.full(shape, QC_MISSING, dtype=np.int8)
    times = np.full(len(pids), np.datetime64("NaT"), dtype="datetime64[ns]")
    lat = np.full(len(pids), np.nan)
    lon = np.full(len(pids), np.nan)

    profile_nav = nav[nav.get("HAS_PROFILE", False).astype(bool)].copy() if "HAS_PROFILE" in nav else nav.iloc[0:0]
    profile_nav["TIME"] = pd.to_datetime(profile_nav["TIME"], errors="coerce")
    for i, pid in enumerate(pids):
        g = profiles[profiles["PROFILE_ID"] == pid].sort_values("PRES").reset_index(drop=True)
        n = len(g)
        pres[i, :n], temp[i, :n], psal[i, :n] = g["PRES"], g["TEMP"], g["PSAL"]
        pres_qc[i, :n], temp_qc[i, :n], psal_qc[i, :n] = g["PRES_QC"], g["TEMP_QC"], g["PSAL_QC"]
        pt = pd.to_datetime(g["TIME"].iloc[0], errors="coerce")
        if pd.notna(pt):
            times[i] = np.datetime64(pt)
            if not profile_nav.empty:
                j = (profile_nav["TIME"] - pt).abs().idxmin()
                if abs((profile_nav.loc[j, "TIME"] - pt).total_seconds()) <= 24 * 3600:
                    lat[i], lon[i] = profile_nav.loc[j, ["LATITUDE", "LONGITUDE"]]

    ds = xr.Dataset(
        data_vars={
            "PRES": (("N_PROF", "N_LEVELS"), pres, {"units": "dbar", "standard_name": "sea_water_pressure"}),
            "TEMP": (("N_PROF", "N_LEVELS"), temp, {"units": "degree_Celsius", "standard_name": "sea_water_temperature"}),
            "PSAL": (("N_PROF", "N_LEVELS"), psal, {"units": "1e-3", "standard_name": "sea_water_practical_salinity"}),
            "PRES_QC": (("N_PROF", "N_LEVELS"), pres_qc),
            "TEMP_QC": (("N_PROF", "N_LEVELS"), temp_qc),
            "PSAL_QC": (("N_PROF", "N_LEVELS"), psal_qc),
            "JULD": (("N_PROF",), times),
            "LATITUDE": (("N_PROF",), lat, {"units": "degrees_north"}),
            "LONGITUDE": (("N_PROF",), lon, {"units": "degrees_east"}),
            "PROFILE_ID": (("N_PROF",), np.asarray(pids, dtype=str)),
        },
        coords={"N_PROF": np.arange(len(pids)), "N_LEVELS": np.arange(max_levels)},
        attrs={
            "title": "SOCIB normalized NKE Argo profile product",
            "institution": "Balearic Islands Coastal Observing and Forecasting System (SOCIB)",
            "source": "Iridium SBD decoded with NKE Instrumentation Parser V1_0_21_83",
            "imei": str(imei),
            "software_version": SOFTWARE_VERSION,
            "Conventions": "CF-1.10",
            "history": f"Created {datetime.now(timezone.utc).isoformat()} by SOCIB Argo SBD Decoder",
            "disclaimer": "Operational quick-look product; not an Argo GDAC compliant file.",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import netCDF4  # noqa: F401
        enc = {v: {"zlib": True, "complevel": 4} for v in ["PRES", "TEMP", "PSAL", "PRES_QC", "TEMP_QC", "PSAL_QC"]}
        ds.to_netcdf(path, engine="netcdf4", encoding=enc)
    except ImportError:
        # Useful for development/minimal environments. The distributed
        # requirements install netCDF4, which enables compression on Windows.
        ds.to_netcdf(path, engine="scipy")


def main():
    settings = load_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--imei", default=settings.get("last_imei", ""))
    ap.add_argument("--technical_csv", default=settings.get("technical_csv_name", "Technical Message.csv"))
    ap.add_argument("--ascent_csv", default=settings.get("ascent_csv_name", "Ascent profile CTD Message.csv"))
    ap.add_argument("--filled_prefix", default=settings.get("filled_profile_prefix", "Ascent profile CTD Message_filled_"))
    args = ap.parse_args()

    root, outdir = Path(args.root), Path(args.outdir)
    processed, reports, netcdf_dir = outdir / "processed", outdir / "reports", outdir / "netcdf"
    for d in (processed, reports, netcdf_dir): d.mkdir(parents=True, exist_ok=True)

    nav = load_positions(root, args.technical_csv, imei=args.imei)
    profile_times = nav.loc[nav["HAS_PROFILE"].astype(bool), "TIME"].dropna().sort_values().tolist() if not nav.empty and "HAS_PROFILE" in nav else []
    profiles = load_profiles(root, args.ascent_csv, args.filled_prefix, imei=args.imei, profile_times=profile_times)
    if profiles.empty and nav.empty:
        raise SystemExit("ERROR: no profiles or navigation positions found")

    cfg = {
        "pressure_min": -5.0, "pressure_max": 12000.0,
        "temperature_min": -2.5, "temperature_max": 45.0,
        "salinity_min": 0.0, "salinity_max": 45.0,
        "min_profile_levels": 10,
        "pressure_reversal_tolerance": 5.0,
        "max_temp_step": 5.0, "max_sal_step": 3.0,
        "max_surface_speed_ms": 3.0,
    }
    cfg.update(settings.get("qc", {}))
    profiles_qc = add_profile_qc(profiles, cfg) if not profiles.empty else profiles
    nav_qc = add_navigation_qc(nav, cfg) if not nav.empty else nav

    if not profiles_qc.empty:
        profiles_qc.to_csv(processed / "profiles_long.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    if not nav_qc.empty:
        nav_qc.to_csv(processed / "navigation.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")

    input_files = sorted({Path(x) for x in pd.concat([profiles.get("SOURCE_FILE", pd.Series(dtype=str)), nav.get("SOURCE_FILE", pd.Series(dtype=str))]).dropna().astype(str) if Path(x).exists()})
    all_times = pd.to_datetime(
        pd.concat([profiles_qc.get("TIME", pd.Series(dtype=str)), nav_qc.get("TIME", pd.Series(dtype=str))]),
        errors="coerce",
    ).dropna()
    report = {
        "software_version": SOFTWARE_VERSION,
        "processing_time_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "nke_parser_version": "V1_0_21_83",
        "imei": str(args.imei),
        "profiles": int(profiles_qc["PROFILE_ID"].nunique()) if not profiles_qc.empty else 0,
        "profile_levels": int(len(profiles_qc)),
        "navigation_positions": int(len(nav_qc)),
        "profile_positions": int(nav_qc["HAS_PROFILE"].astype(bool).sum()) if not nav_qc.empty and "HAS_PROFILE" in nav_qc else 0,
        "gps_only_positions": int((~nav_qc["HAS_PROFILE"].astype(bool)).sum()) if not nav_qc.empty and "HAS_PROFILE" in nav_qc else 0,
        "first_time": str(all_times.min()) if not all_times.empty else None,
        "last_time": str(all_times.max()) if not all_times.empty else None,
        "qc_counts": {
            "bad_temperature": int((profiles_qc.get("TEMP_QC", pd.Series(dtype=int)) >= QC_PROBABLY_BAD).sum()),
            "bad_salinity": int((profiles_qc.get("PSAL_QC", pd.Series(dtype=int)) >= QC_PROBABLY_BAD).sum()),
            "bad_pressure": int((profiles_qc.get("PRES_QC", pd.Series(dtype=int)) >= QC_PROBABLY_BAD).sum()),
            "suspicious_positions": int((nav_qc.get("POSITION_QC", pd.Series(dtype=int)) >= QC_PROBABLY_BAD).sum()),
        },
        "qc_configuration": cfg,
        "inputs": [{"path": str(p), "sha256": sha256(p)} for p in input_files],
    }
    (reports / "processing_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["SOCIB Argo SBD Decoder processing report", "=" * 42] + [f"{k}: {v}" for k, v in report.items() if k not in {"inputs", "qc_configuration", "qc_counts"}]
    lines += ["", "QC summary:"] + [f"  {k}: {v}" for k, v in report["qc_counts"].items()]
    (reports / "processing_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not profiles_qc.empty:
        write_netcdf(profiles_qc, nav_qc, netcdf_dir / f"SOCIB_Argo_IMEI_{args.imei or 'unknown'}_profiles.nc", args.imei)

    (reports / "manifest.json").write_text(json.dumps({
        "software_version": SOFTWARE_VERSION,
        "generated": [str(p.relative_to(outdir)) for p in sorted(outdir.rglob("*")) if p.is_file()],
    }, indent=2), encoding="utf-8")
    print(f"NORMALIZED_PROFILES={processed / 'profiles_long.csv'}")
    print(f"NORMALIZED_NAVIGATION={processed / 'navigation.csv'}")
    print(f"PROCESSING_REPORT={reports / 'processing_report.json'}")
    print("SCIENTIFIC_PRODUCTS_DONE")


if __name__ == "__main__":
    main()
