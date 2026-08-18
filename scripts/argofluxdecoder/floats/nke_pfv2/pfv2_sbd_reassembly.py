"""
pfv2_sbd_reassembly.py
======================
Reconstruct PFV2 float data files (.hex) from Iridium SBD fragments.

PFV2 SBD format:
  Each .sbd file contains:
    - A filename string (ASCII, e.g. "M1C5TEC1.hex.gz.001")
    - A separator byte: 0x03 (more fragments) or 0x04 (last fragment)
    - Binary data (a fragment of the compressed .hex.gz file)

Fragments for the same base file are concatenated in order, then
gunzipped to produce the final .hex data file.

File types determined by filename pattern:
  - M{m}C{c}TEC{n}.hex  → Technical file
  - M{m}C{c}S{s}F{f}{phase}{freq}.hex → Data measurement file
  - M{m}C{c}EOL{n}.hex  → End-of-life file
  - *_selftest.hex       → Self-test file
  - *_setting.xml        → Configuration file

Reference:
  create_data_files_pfv2_sbd.m (Coriolis MATLAB decoder v085h)
  DOI: https://doi.org/10.17882/45589
"""

from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Separator bytes
SEP_MORE_FRAGMENTS = 0x03
SEP_LAST_FRAGMENT = 0x04


@dataclass
class SbdFragment:
    """A single SBD file parsed into its components."""
    base_filename: str          # e.g. "M1C5TEC1.hex"
    fragment_number: int        # 1-based fragment index
    is_last: bool               # True if this is the final fragment
    data: bytes                 # Binary payload (compressed fragment)
    sbd_filename: str           # Original .sbd filename
    sbd_date: Optional[str] = None


@dataclass
class ReassembledFile:
    """A complete reassembled .hex file from SBD fragments."""
    filename: str               # Final filename (e.g. "M1C5TEC1.hex")
    data: bytes                 # Decompressed file content
    file_type: str              # "tech", "data", "eol", "selftest", "config"
    mission: int = 0
    cycle: int = 0
    sensor: int = -1
    format_num: int = 0
    phase: str = ""
    date_freq: int = 0
    sbd_sources: List[str] = field(default_factory=list)


def parse_sbd_fragment(sbd_path: Path) -> Optional[SbdFragment]:
    """
    Parse a single PFV2 SBD file into its components.

    PFV2 SBD structure:
      [filename_ascii] [0x03 or 0x04] [binary_data]

    Parameters
    ----------
    sbd_path : Path
        Path to the .sbd file.

    Returns
    -------
    SbdFragment or None
        Parsed fragment, or None if parsing fails.
    """
    raw = sbd_path.read_bytes()
    if len(raw) < 3:
        return None

    # Find separator byte (0x03 or 0x04)
    sep_pos = None
    sep_byte = None
    for i, b in enumerate(raw):
        if b == SEP_MORE_FRAGMENTS or b == SEP_LAST_FRAGMENT:
            sep_pos = i
            sep_byte = b
            break

    if sep_pos is None or sep_pos == 0:
        logger.warning("PFV2: No separator found in %s", sbd_path.name)
        return None

    # Extract filename and data
    filename_bytes = raw[:sep_pos]
    try:
        filename_str = filename_bytes.decode("ascii").strip()
    except UnicodeDecodeError:
        logger.warning("PFV2: Non-ASCII filename in %s", sbd_path.name)
        return None

    data = bytes(raw[sep_pos + 1:])
    is_last = (sep_byte == SEP_LAST_FRAGMENT)

    # Parse base filename and fragment number
    # Format: "base.hex.gz" or "base.hex.gz.NNN"
    base_filename, frag_num = _parse_fragment_name(filename_str)

    return SbdFragment(
        base_filename=base_filename,
        fragment_number=frag_num,
        is_last=is_last,
        data=data,
        sbd_filename=sbd_path.name,
    )


def _parse_fragment_name(filename: str) -> Tuple[str, int]:
    """
    Parse fragment filename into base name and fragment number.

    Examples:
      "M1C5TEC1.hex.gz"     → ("M1C5TEC1.hex", 1)
      "M1C5TEC1.hex.gz.001" → ("M1C5TEC1.hex", 1)
      "M1C5TEC1.hex.gz.002" → ("M1C5TEC1.hex", 2)
    """
    # Try to extract .gz.NNN suffix
    match = re.match(r"^(.+\.hex)\.gz(?:\.(\d+))?$", filename, re.IGNORECASE)
    if match:
        base = match.group(1)
        frag_str = match.group(2)
        frag_num = int(frag_str) if frag_str else 1
        return base, frag_num

    # Also handle XML config files
    match = re.match(r"^(.+\.xml)\.gz(?:\.(\d+))?$", filename, re.IGNORECASE)
    if match:
        base = match.group(1)
        frag_str = match.group(2)
        frag_num = int(frag_str) if frag_str else 1
        return base, frag_num

    # Fallback: treat whole string as filename, fragment 1
    return filename, 1


def reassemble_sbd_directory(sbd_dir: Path) -> List[ReassembledFile]:
    """
    Reassemble all PFV2 .hex files from SBD fragments in a directory.

    Pipeline:
      1. Parse all .sbd files into fragments
      2. Group fragments by base filename
      3. For complete file groups (last fragment received), concatenate and decompress
      4. Parse filenames to determine file type and metadata

    Parameters
    ----------
    sbd_dir : Path
        Directory containing .sbd files.

    Returns
    -------
    list of ReassembledFile
        All successfully reassembled data files.
    """
    sbd_dir = Path(sbd_dir)
    sbd_files = sorted(sbd_dir.glob("*.sbd"))

    if not sbd_files:
        return []

    # Parse all fragments
    fragments: Dict[str, List[SbdFragment]] = {}
    for sbd_path in sbd_files:
        frag = parse_sbd_fragment(sbd_path)
        if frag is None:
            continue
        key = frag.base_filename
        if key not in fragments:
            fragments[key] = []
        fragments[key].append(frag)

    # Reassemble complete files
    results = []
    for base_name, frags in fragments.items():
        # Check if we have the last fragment
        has_last = any(f.is_last for f in frags)
        if not has_last:
            logger.warning("PFV2: Incomplete file %s (missing last fragment)", base_name)
            continue

        # Sort by fragment number
        frags.sort(key=lambda f: f.fragment_number)

        # Check for contiguous sequence starting at 1
        expected_nums = list(range(1, len(frags) + 1))
        actual_nums = [f.fragment_number for f in frags]
        if actual_nums != expected_nums:
            logger.warning(
                "PFV2: Non-contiguous fragments for %s: %s",
                base_name, actual_nums
            )
            continue

        # Concatenate data
        compressed_data = b"".join(f.data for f in frags)
        sbd_sources = [f.sbd_filename for f in frags]

        # Decompress
        try:
            decompressed = gzip.decompress(compressed_data)
        except Exception as e:
            logger.warning("PFV2: Failed to decompress %s: %s", base_name, e)
            continue

        # Parse file metadata
        reassembled = _create_reassembled_file(base_name, decompressed, sbd_sources)
        if reassembled:
            results.append(reassembled)

    logger.info("PFV2: Reassembled %d files from %d SBD fragments", len(results), len(sbd_files))
    return results


def _create_reassembled_file(filename: str, data: bytes,
                             sbd_sources: List[str]) -> Optional[ReassembledFile]:
    """
    Create a ReassembledFile with parsed metadata from the filename.

    Filename patterns:
      M{m}C{c}TEC{n}.hex      → tech
      M{m}C{c}S{s}F{f}{P}{d}.hex → data (P=phase letter, d=date frequency)
      M{m}C{c}EOL{n}.hex      → eol
      {datetime}_selftest.hex  → selftest
      *_setting.xml            → config
    """
    rf = ReassembledFile(
        filename=filename,
        data=data,
        file_type="unknown",
        sbd_sources=sbd_sources,
    )

    # Tech file: M{mission}C{cycle}TEC{n}.hex
    m = re.match(r"^M(\d+)C(\d+)TEC(\d+)\.hex$", filename, re.IGNORECASE)
    if m:
        rf.file_type = "tech"
        rf.mission = int(m.group(1))
        rf.cycle = int(m.group(2))
        return rf

    # EOL file: M{mission}C{cycle}EOL{n}.hex
    m = re.match(r"^M(\d+)C(\d+)EOL(\d+)\.hex$", filename, re.IGNORECASE)
    if m:
        rf.file_type = "eol"
        rf.mission = int(m.group(1))
        rf.cycle = int(m.group(2))
        return rf

    # Data file: M{m}C{c}S{sensor}F{format}{phase}{dateFreq}.hex
    m = re.match(
        r"^M(\d+)C(\d+)S(\d+)F(\d+)([DPTBAI])(\d+)\.hex$",
        filename, re.IGNORECASE
    )
    if m:
        rf.file_type = "data"
        rf.mission = int(m.group(1))
        rf.cycle = int(m.group(2))
        rf.sensor = int(m.group(3))
        rf.format_num = int(m.group(4))
        rf.phase = m.group(5).upper()
        rf.date_freq = int(m.group(6))
        return rf

    # Self-test file
    if "_selftest.hex" in filename.lower():
        rf.file_type = "selftest"
        return rf

    # Config/settings
    if filename.lower().endswith("_setting.xml") or filename.lower().endswith(".xml"):
        rf.file_type = "config"
        return rf

    # Unknown but still return it
    logger.debug("PFV2: Unrecognized file pattern: %s", filename)
    return rf
