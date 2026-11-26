"""
float_behavior.py
=================
Analyzes Argo float cycle timing from the Technical Message CSV produced
by the NKE proprietary software (or compatible decoders).

Expected file layout — rows are parameters, columns are messages:
    column 0  = parameter label
    columns 1..N = one value per Iridium message received.

The NKE software interleaves "flag" columns (all zeros) between real data
columns.  This script auto-detects and skips those empty columns.

Usage (CLI):
    python float_behavior.py /path/to/float_root_folder
    python float_behavior.py /path/to/float_root_folder --csv sbd_raw/Technical\\ Message.csv

The script searches for "Technical Message.csv" in:
    <root>/sbd_raw/
    <root>/decoded/
    <root>/

Outputs saved to <root>/products/behavior/:
    cycle_summary.csv          — per-cycle timing and pressure table
    float_cycle_timeline.png   — Gantt-style timeline chart
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for scripts and GUI calls
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D


# ──────────────────────────────────────────────────────────────────────────────
# NOMINAL SPEEDS (reference values)
# ──────────────────────────────────────────────────────────────────────────────
ASCENT_SPEED_TECH    = 0.64   # m/s  (from tech spec)
DESCENT_SPEED_TECH   = 0.65   # m/s
ASCENT_SPEED_MANUAL  = 0.30   # m/s  (empirical)
DESCENT_SPEED_MANUAL = 0.20   # m/s


# ──────────────────────────────────────────────────────────────────────────────
# CSV LOADING
# ──────────────────────────────────────────────────────────────────────────────

def find_csv(base_dir: Path, explicit: str | None = None) -> Path:
    """
    Locate the Technical Message CSV.

    Search order:
      1. Explicit path provided by the user.
      2. <base_dir>/sbd_raw/Technical Message.csv
      3. <base_dir>/decoded/Technical Message.csv
      4. <base_dir>/Technical Message.csv
    """
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = base_dir / p
        if p.exists():
            return p
        raise FileNotFoundError(f"CSV not found: {p}")

    candidates = [
        base_dir / "sbd_raw"  / "Technical Message.csv",
        base_dir / "decoded"  / "Technical Message.csv",
        base_dir              / "Technical Message.csv",
    ]
    for c in candidates:
        if c.exists():
            return c

    raise FileNotFoundError(
        f"Could not find 'Technical Message.csv' under:\n  {base_dir}\n"
        "Checked: sbd_raw/, decoded/, and root."
    )


def _is_data_column(series: pd.Series) -> bool:
    """
    Return True if a column contains at least two distinct non-zero numeric
    values (i.e. it is a real data column, not a flag/padding column).
    A single non-zero value is not enough — NKE flag columns sometimes have
    one non-zero entry (e.g. battery voltage = 10 in every column).
    We require that grounding_pressure > 0, which is checked later per-cycle.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    nonzero = numeric[numeric.notna() & (numeric != 0)]
    return len(nonzero) >= 2


def _extract_sbd_dates(base_dir: Path) -> list[str]:
    """
    Return one YYYYMMDD date per cycle, extracted from SBD filenames.
    Groups consecutive SBDs by date and returns one date per group,
    matching the number of real data cycles in the Technical Message.
    """
    sbd_dir = base_dir / "sbd_raw"
    if not sbd_dir.exists():
        sbd_dir = base_dir

    # Collect all dates in order (one per file)
    all_dates = []
    for f in sorted(sbd_dir.glob("*.sbd")):
        part = f.stem.split("_")[0]
        if len(part) == 8 and part.isdigit():
            all_dates.append(part)

    if not all_dates:
        return []

    # Group by consecutive runs of same date, but also detect
    # when the MOMSN (second field) resets — that indicates a new cycle group.
    # Simpler: group files into blocks of equal size (files_per_cycle).
    # We detect the block size from the most common count per date.
    from collections import Counter
    date_counts = Counter(all_dates)
    # Most common count per unique date = files per cycle
    files_per_cycle = Counter(date_counts.values()).most_common(1)[0][0]

    # Build one date per cycle by stepping through all_dates in blocks
    cycle_dates = []
    prev_date = None
    block_count = 0

    for date in all_dates:
        if date != prev_date:
            if prev_date is not None and block_count >= files_per_cycle:
                # New date group starts — previous block was a full cycle
                cycle_dates.append(prev_date)
                block_count = 0
            elif prev_date is None:
                block_count = 0
        block_count += 1
        prev_date = date

    # Last block
    if prev_date is not None:
        cycle_dates.append(prev_date)

    # If grouping failed, fall back to unique dates in order
    if not cycle_dates:
        seen = set()
        for d in all_dates:
            if d not in seen:
                seen.add(d)
                cycle_dates.append(d)

    return cycle_dates


def load_csv(csv_path: Path) -> pd.DataFrame:
    """
    Read the NKE Technical Message CSV and return a tidy DataFrame with
    one row per cycle.

    The raw file has:
      - column 0: parameter label
      - columns 1..N: one value per Iridium message (NKE interleaves
        zero-filled "flag" columns between real data columns)

    This function:
      1. Reads the raw file.
      2. Identifies data columns (non-zero).
      3. Extracts the parameters we need for each data column (= cycle).
      4. Returns a DataFrame with one row per cycle.
    """
    raw = pd.read_csv(
        csv_path,
        sep=";",
        header=None,
        engine="python",
        encoding_errors="replace",
    )

    # Clean parameter labels in column 0
    raw[0] = raw[0].astype(str).str.strip().str.strip('"')

    # Identify data columns (skip the label column 0 and zero/empty columns)
    data_col_indices = [
        col for col in raw.columns[1:]
        if _is_data_column(raw[col])
    ]

    if not data_col_indices:
        raise ValueError(
            f"No data columns found in:\n  {csv_path}\n"
            "All columns after the label are zero or empty."
        )

    print(f"  Found {len(data_col_indices)} cycle column(s) in {csv_path.name}")

    # For each data column, extract the parameters we need
    def get_param(label: str, col_idx) -> float:
        """Return the numeric value of *label* in *col_idx*, or NaN."""
        rows = raw[raw[0] == label]
        if rows.empty:
            return np.nan
        val = rows.iloc[0][col_idx]
        try:
            f = float(val)
            return f if not np.isnan(f) else np.nan
        except (TypeError, ValueError):
            return np.nan

    records = []
    for i, col in enumerate(data_col_indices):
        # Try to get a date string for this cycle from SBD filenames
        sbd_dates = _extract_sbd_dates(csv_path.parent.parent
                                       if csv_path.parent.name == "sbd_raw"
                                       else csv_path.parent)
        cycle_date = sbd_dates[i] if i < len(sbd_dates) else ""

        records.append({
            "cycle_col":          i + 1,
            "cycle_date":         cycle_date,

            # Timing (minutes)
            "cycle_start":        get_param("Cycle start time (min)",        col),
            "descent_start":      get_param("Descent start time (min)",      col),
            "end_descent":        get_param("End of descent time (min)",     col),
            "ascent_start":       get_param("Ascent start time (min)",       col),
            "end_ascent":         get_param("Time at end of ascent (min)",   col),
            "tx_start":           get_param("Transmission start time (min)", col),

            # Pressure (cBar)
            "grounding_pressure": get_param("Grounding pressure (cBar)",     col),
            "min_pressure_drift": get_param("Min pressure in drift (cBar)",  col),
            "max_pressure_drift": get_param("Max pressure in drift (cBar)",  col),

            # Other useful fields
            "internal_pressure":  get_param("Internal pressure (mbar)",      col),
            "descent_speed":      get_param("Descent speed (mBar/sec)",      col),
            "ascent_speed":       get_param("Ascent speed (mBar/sec)",       col),
            "nb_pump_ascent":     get_param("Number of pump actions in ascent", col),
            "nb_valve_descent":   get_param("Number of valve actions in descent", col),
        })

    df = pd.DataFrame(records)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# DERIVED COLUMNS
# ──────────────────────────────────────────────────────────────────────────────

def compute_durations(cycles: pd.DataFrame) -> pd.DataFrame:
    c = cycles.copy()

    # Phase durations (minutes)
    c["ascending_duration"]        = c["end_ascent"]    - c["ascent_start"]
    c["surface_duration"]          = c["descent_start"] - c["end_ascent"]
    c["descending_duration"]       = c["end_descent"]   - c["descent_start"]
    c["observable_cycle_duration"] = c["end_descent"]   - c["ascent_start"]

    # Surface sub-phases
    c["surface_to_transmission"]     = c["tx_start"]      - c["end_ascent"]
    c["surface_to_cycle_start"]      = c["cycle_start"]   - c["end_ascent"]
    c["transmission_to_cycle_start"] = c["cycle_start"]   - c["tx_start"]
    c["cycle_start_to_descent"]      = c["descent_start"] - c["cycle_start"]

    # Pressure anomaly
    c["max_minus_grounding"] = c["max_pressure_drift"] - c["grounding_pressure"]

    # Flag negative durations
    duration_cols = [
        "ascending_duration", "surface_duration", "descending_duration",
        "observable_cycle_duration",
        "surface_to_transmission", "surface_to_cycle_start",
        "transmission_to_cycle_start", "cycle_start_to_descent",
    ]
    c["has_negative_duration"] = c[duration_cols].lt(0).any(axis=1)

    # Speed analysis (grounding_pressure ≈ depth in m for shallow floats)
    c["depth_m"] = c["grounding_pressure"]
    c["observed_ascent_sec"]  = c["ascending_duration"]  * 60
    c["observed_descent_sec"] = c["descending_duration"] * 60
    c["real_ascent_speed"]  = (
        c["depth_m"] / c["observed_ascent_sec"].replace(0, np.nan)
    )
    c["real_descent_speed"] = (
        c["depth_m"] / c["observed_descent_sec"].replace(0, np.nan)
    )

    return c


# ──────────────────────────────────────────────────────────────────────────────
# PRINT STATS
# ──────────────────────────────────────────────────────────────────────────────

def print_stats(cycles: pd.DataFrame) -> None:
    valid = cycles[cycles["grounding_pressure"].fillna(0) > 0]
    n = len(valid)
    print(f"\n══════════════════════════════════════════")
    print(f"  CYCLE SUMMARY  ({n} cycle(s) with data)")
    print(f"══════════════════════════════════════════")

    for col, label in [
        ("ascending_duration",        "Ascending duration     "),
        ("surface_duration",          "Surface duration       "),
        ("descending_duration",        "Descending duration    "),
        ("observable_cycle_duration",  "Observable cycle total "),
        ("surface_to_transmission",    "Surface → transmission "),
        ("cycle_start_to_descent",     "Cycle start → descent  "),
    ]:
        if col in valid.columns and valid[col].notna().any():
            m = valid[col].mean()
            s = valid[col].std()
            print(f"  {label}: {m:6.1f} ± {s:.1f} min")

    if valid["real_ascent_speed"].notna().any():
        print()
        print(f"  Ascent speed  (mean): {valid['real_ascent_speed'].mean():.3f} m/s")
        print(f"  Descent speed (mean): {valid['real_descent_speed'].mean():.3f} m/s")

    n_neg = int(cycles["has_negative_duration"].sum())
    if n_neg:
        print(f"\n  ⚠  {n_neg} cycle(s) with negative durations (check raw data).")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# GANTT TIMELINE PLOT
# ──────────────────────────────────────────────────────────────────────────────

def plot_timeline(cycles: pd.DataFrame, out_path: Path) -> None:
    """
    Gantt-style chart: ascending (green) / surface (blue) / descending (orange).
    Time axis is relative to ascent_start of each cycle.
    Annotations:
      - Ascent speed (m/s) centred on the green bar
      - Descent speed (m/s) centred on the orange bar
      - Grounding pressure (cBar) at the start of the orange bar (above)
      - ΔP below the bar (existing)
    """
    pc = cycles.copy()
    pc["t_ascent_start"]   = 0
    pc["t_end_ascent"]     = pc["end_ascent"]    - pc["ascent_start"]
    pc["t_tx_start"]       = pc["tx_start"]      - pc["ascent_start"]
    pc["t_cycle_start"]    = pc["cycle_start"]   - pc["ascent_start"]
    pc["t_descent_start"]  = pc["descent_start"] - pc["ascent_start"]
    pc["t_end_descent"]    = pc["end_descent"]   - pc["ascent_start"]

    event_cols = [
        "t_ascent_start", "t_end_ascent", "t_tx_start",
        "t_cycle_start",  "t_descent_start", "t_end_descent",
    ]
    # Keep only cycles with actual depth data (grounding_pressure > 0)
    valid = (
        pc.dropna(subset=event_cols)
          .loc[pc["grounding_pressure"].fillna(0) > 0]
          .reset_index(drop=True)
    )

    if valid.empty:
        print("  No valid cycles to plot (missing timing fields).")
        return

    colors = {
        "ascending":  "tab:green",
        "surface":    "tab:blue",
        "descending": "tab:orange",
    }

    fig, ax = plt.subplots(figsize=(14, max(5, 0.65 * len(valid))))
    y = np.arange(len(valid))

    for i, row in valid.iterrows():
        # ── Phase bars ────────────────────────────────────────────────────
        ax.plot(
            [row["t_ascent_start"],  row["t_end_ascent"]],
            [i, i], linewidth=10, color=colors["ascending"],  solid_capstyle="butt",
        )
        ax.plot(
            [row["t_end_ascent"],    row["t_descent_start"]],
            [i, i], linewidth=10, color=colors["surface"],    solid_capstyle="butt",
        )
        ax.plot(
            [row["t_descent_start"], row["t_end_descent"]],
            [i, i], linewidth=10, color=colors["descending"], solid_capstyle="butt",
        )

        # ── Ascent speed label (centre of green bar) ──────────────────────
        asc_speed = row.get("real_ascent_speed", np.nan)
        if pd.notna(asc_speed) and asc_speed > 0:
            t_mid_asc = (row["t_ascent_start"] + row["t_end_ascent"]) / 2
            ax.text(
                t_mid_asc, i,
                f"{asc_speed:.3f} m/s",
                fontsize=7.5, ha="center", va="center",
                color="white", fontweight="bold",
                zorder=6,
            )

        # ── Descent speed label (centre of orange bar) ────────────────────
        desc_speed = row.get("real_descent_speed", np.nan)
        if pd.notna(desc_speed) and desc_speed > 0:
            t_mid_desc = (row["t_descent_start"] + row["t_end_descent"]) / 2
            ax.text(
                t_mid_desc, i,
                f"{desc_speed:.3f} m/s",
                fontsize=7.5, ha="center", va="center",
                color="white", fontweight="bold",
                zorder=6,
            )

        # ── Grounding pressure label (above bar, at descent start) ────────
        gp = row.get("grounding_pressure", np.nan)
        if pd.notna(gp) and gp > 0:
            ax.text(
                row["t_descent_start"], i + 0.38,
                f"{gp:.0f} cBar",
                fontsize=7.5, ha="center", va="bottom",
                color="darkorange", fontweight="bold",
                zorder=6,
            )

        # ── Event markers (above bar) ─────────────────────────────────────
        ye = i + 0.18
        ax.scatter(row["t_end_ascent"],    ye, color="black", s=70, marker="o", zorder=5)
        ax.scatter(row["t_tx_start"],      ye, color="black", s=80, marker="^", zorder=5)
        ax.scatter(row["t_cycle_start"],   ye, color="black", s=80, marker="s", zorder=5)
        ax.scatter(row["t_descent_start"], ye, color="black", s=80, marker="v", zorder=5)

        # ── ΔP annotation (below bar) ─────────────────────────────────────
        dp = row.get("max_minus_grounding", np.nan)
        if pd.notna(dp):
            ax.text(
                row["t_end_descent"], i - 0.22,
                f"ΔP={dp:.0f} cBar",
                fontsize=8, ha="right", va="top",
            )

    ax.set_yticks(y)
    # Use date if available, otherwise fall back to cycle number
    ylabels = []
    for _, r in valid.iterrows():
        date = str(r.get("cycle_date", "")).strip()
        if date and date != "nan":
            try:
                from datetime import datetime as _dt
                ylabels.append(_dt.strptime(date, "%Y%m%d").strftime("%d %b %Y"))
            except ValueError:
                ylabels.append(date)
        else:
            ylabels.append(f"Cycle {int(r['cycle_col'])}")
    ax.set_yticklabels(ylabels)
    ax.set_xlabel("Time since ascent start (min)")
    ax.set_ylabel("Date")
    ax.set_title("Float cycle timeline: ascent start → end of descent")
    ax.grid(axis="x", alpha=0.3)
    ax.set_ylim(-0.7, len(valid) - 0.1)

    phase_leg = [
        mpatches.Patch(color=colors["ascending"],  label="Ascending"),
        mpatches.Patch(color=colors["surface"],    label="Surface"),
        mpatches.Patch(color=colors["descending"], label="Descending"),
    ]
    event_leg = [
        Line2D([0],[0], marker="o", color="black", linestyle="None", markersize=7,  label="End of ascent"),
        Line2D([0],[0], marker="^", color="black", linestyle="None", markersize=8,  label="Transmission start"),
        Line2D([0],[0], marker="s", color="black", linestyle="None", markersize=8,  label="Cycle start"),
        Line2D([0],[0], marker="v", color="black", linestyle="None", markersize=8,  label="Descent start"),
    ]
    l1 = ax.legend(handles=phase_leg, bbox_to_anchor=(1.02, 1),    loc="upper left", title="Phase")
    ax.add_artist(l1)
    ax.legend(      handles=event_leg, bbox_to_anchor=(1.02, 0.62), loc="upper left", title="Events")

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Timeline plot saved to:\n    {out_path}")


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def run(
    base_dir: str | Path,
    outdir: str | Path | None = None,
    csv_path: str | None = None,
) -> Path:
    """
    Main entry point.

    Parameters
    ----------
    base_dir  : float root folder.
    outdir    : where to save outputs (default: <base_dir>/products/behavior/).
    csv_path  : explicit path to Technical Message.csv (relative to base_dir
                or absolute).  If None, auto-searched.

    Returns
    -------
    Path to the output directory.
    """
    base_dir = Path(base_dir)
    if outdir is None:
        outdir = base_dir / "products" / "behavior"
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    csv = find_csv(base_dir, explicit=csv_path)
    print(f"Using: {csv}")

    cycles_raw = load_csv(csv)
    cycles = compute_durations(cycles_raw)

    print_stats(cycles)

    # Save summary CSV
    summary_cols = [
        "cycle_col",
        "cycle_date",
        "ascending_duration", "surface_duration", "descending_duration",
        "observable_cycle_duration",
        "surface_to_transmission", "surface_to_cycle_start",
        "transmission_to_cycle_start", "cycle_start_to_descent",
        "grounding_pressure", "min_pressure_drift", "max_pressure_drift",
        "max_minus_grounding",
        "internal_pressure",
        "real_ascent_speed", "real_descent_speed",
        "has_negative_duration",
    ]
    summary = cycles[[c for c in summary_cols if c in cycles.columns]].round(3)

    # Rename columns to match Technical Message label conventions (with units)
    summary = summary.rename(columns={
        "cycle_col":                    "Cycle",
        "cycle_date":                   "Date",
        "ascending_duration":           "Ascending duration (min)",
        "surface_duration":             "Surface duration (min)",
        "descending_duration":          "Descending duration (min)",
        "observable_cycle_duration":    "Observable cycle duration (min)",
        "surface_to_transmission":      "Surface to transmission start (min)",
        "surface_to_cycle_start":       "Surface to cycle start (min)",
        "transmission_to_cycle_start":  "Transmission to cycle start (min)",
        "cycle_start_to_descent":       "Cycle start to descent (min)",
        "grounding_pressure":           "Grounding pressure (cBar)",
        "min_pressure_drift":           "Min pressure in drift (cBar)",
        "max_pressure_drift":           "Max pressure in drift (cBar)",
        "max_minus_grounding":          "Max pressure minus grounding (cBar)",
        "internal_pressure":            "Internal pressure (mbar)",
        "real_ascent_speed":            "Calculated ascent speed (m/s)",
        "real_descent_speed":           "Calculated descent speed (m/s)",
        "has_negative_duration":        "Has negative duration",
    })

    csv_out = outdir / "cycle_summary.csv"
    summary.to_csv(csv_out, index=False)
    print(f"  Summary CSV saved to:\n    {csv_out}")

    # Save timeline plot
    plot_timeline(cycles, outdir / "float_cycle_timeline.png")

    return outdir


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze Argo float cycle timing from an NKE Technical Message CSV.\n"
            "Produces a summary table and a Gantt-style timeline plot."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "root", nargs="?",
        help="Float root folder.",
    )
    parser.add_argument(
        "--csv", default=None,
        help="Path to Technical Message.csv (relative to root or absolute). "
             "Auto-searched in sbd_raw/, decoded/, root/ if omitted.",
    )
    parser.add_argument(
        "--outdir", default=None,
        help="Output folder (default: <root>/products/behavior/).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.root:
        base_dir = Path(args.root)
    else:
        base_dir = Path(input("Float root folder: ").strip().strip('"'))

    if not base_dir.exists():
        print(f"ERROR: folder not found:\n  {base_dir}", file=sys.stderr)
        sys.exit(1)

    run(base_dir, outdir=args.outdir, csv_path=args.csv)
    print("Done.")


if __name__ == "__main__":
    main()
