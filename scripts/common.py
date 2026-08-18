#!/usr/bin/env python3
"""
Common utilities for Argo SBD Decoder scripts.
All file-name assumptions are read from config/settings.json when available.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_SETTINGS = {
    "technical_csv_name": "Technical Message.csv",
    "ascent_csv_name": "Ascent profile CTD Message.csv",
    "filled_profile_prefix": "Ascent profile CTD Message_filled_",
    "date_folder_regex": r"^\d{8}$",
}


def load_settings(config_path: str | None = None) -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)

    paths: List[Path] = []
    if config_path:
        paths.append(Path(config_path))

    here = Path(__file__).resolve()
    paths.extend([
        here.parents[1] / "config" / "settings.json",
        here.parents[1] / "config" / "settings.template.json",
    ])

    for path in paths:
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                settings.update({k: v for k, v in loaded.items() if v not in (None, "")})
                break
            except Exception:
                continue

    return settings


def find_date_folders(root: Path, date_folder_regex: str | None = None) -> list[Path]:
    settings = load_settings()
    regex_text = date_folder_regex or settings.get("date_folder_regex", r"^\d{8}$")
    rgx = re.compile(regex_text)
    return sorted([p for p in root.rglob("*") if p.is_dir() and rgx.match(p.name)])


def discover_raw_sbd_dates(root: Path) -> list[str]:
    """Return sorted YYYYMMDD dates found in flat sbd_raw file names.

    Flat downloads are named YYYYMMDD_<message-id>_<original>.sbd. The helper
    also accepts any SBD whose first eight filename characters are YYYYMMDD.
    """
    raw = Path(root) / "sbd_raw"
    dates: set[str] = set()
    if raw.exists():
        for p in raw.rglob("*.sbd"):
            prefix = p.name[:8]
            if prefix.isdigit() and 1900 <= int(prefix[:4]) <= 2100:
                dates.add(prefix)
    return sorted(dates)


def default_dataset_date(root: Path) -> str:
    """Best available date for a dataset, including flat batch layout."""
    dates = discover_raw_sbd_dates(root)
    if dates:
        return dates[0]
    folders = find_date_folders(Path(root))
    return folders[0].name if folders else "unknown"
