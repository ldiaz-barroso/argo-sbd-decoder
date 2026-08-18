"""
sbd_reader.py
=============
Read Iridium SBD binary files and extract message frames.

Supports two SBD frame formats:

1. NKE format (fixed 100-byte frames):
   Each SBD file contains one or more 100-byte frames.
   Byte 0 of each frame is the packet type; bytes 1-99 are the payload.

2. NOVA/DOVA format (variable-length messages):
   Each SBD file contains one message.
   Byte 0 = packet type, bytes 1-2 = total message length (uint16 big-endian),
   bytes 3+ = payload data.

Reference:
  decode_sbd_file.m, read_nova_iridium_sbd.m (Coriolis MATLAB decoder)
  https://doi.org/10.17882/45589
"""

from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

FRAME_SIZE = 100


@dataclass
class SbdFrame:
    """A single SBD message frame (NKE: 100-byte fixed; NOVA: variable-length)."""
    pack_type: int
    payload: bytes
    file_name: str
    file_date: Optional[datetime] = None


@dataclass
class SbdFile:
    """Metadata and frames from one .sbd file."""
    path: Path
    file_name: str
    file_date: Optional[datetime]
    imei: str
    momsn: int
    frames: List[SbdFrame] = field(default_factory=list)


def parse_sbd_filename(filename: str) -> dict:
    """
    Parse the standard Iridium SBD filename.

    Format: YYYYMMDD_MOMSN_IMEI_MTMSN.sbd
    Example: 20260726_24580_300534065469590_000090.sbd

    Returns dict with keys: date, momsn, imei, mtmsn
    """
    stem = Path(filename).stem
    parts = stem.split("_")
    result = {"date": None, "momsn": 0, "imei": "", "mtmsn": 0}

    if len(parts) >= 4:
        try:
            result["date"] = datetime.strptime(parts[0], "%Y%m%d")
        except ValueError:
            pass
        try:
            result["momsn"] = int(parts[1])
        except ValueError:
            pass
        result["imei"] = parts[2]
        try:
            result["mtmsn"] = int(parts[3])
        except ValueError:
            pass

    return result


def _is_padding_frame(frame_bytes: bytes) -> bool:
    """
    Check if a 100-byte frame is just padding.

    Historical SBD buffers were padded with 0x00,
    modern ones are padded with 0x1A (26).
    """
    return all(b == 0 for b in frame_bytes) or all(b == 0x1A for b in frame_bytes)


def read_sbd_file(filepath: Path) -> Optional[SbdFile]:
    """
    Read a single .sbd file and extract all valid frames.

    Parameters
    ----------
    filepath : Path
        Path to the .sbd file.

    Returns
    -------
    SbdFile or None
        Parsed file with frames, or None if file has unexpected size.
    """
    filepath = Path(filepath)
    file_size = filepath.stat().st_size

    if file_size == 0 or file_size % FRAME_SIZE != 0:
        return None

    meta = parse_sbd_filename(filepath.name)

    sbd_file = SbdFile(
        path=filepath,
        file_name=filepath.name,
        file_date=meta["date"],
        imei=meta["imei"],
        momsn=meta["momsn"],
    )

    raw = filepath.read_bytes()
    n_frames = len(raw) // FRAME_SIZE

    for i in range(n_frames):
        frame_bytes = raw[i * FRAME_SIZE : (i + 1) * FRAME_SIZE]

        if _is_padding_frame(frame_bytes):
            continue

        frame = SbdFrame(
            pack_type=frame_bytes[0],
            payload=bytes(frame_bytes[1:]),
            file_name=filepath.name,
            file_date=meta["date"],
        )
        sbd_file.frames.append(frame)

    return sbd_file


def read_sbd_file_nova(filepath: Path) -> Optional[SbdFile]:
    """
    Read a single NOVA/DOVA .sbd file (variable-length message).

    NOVA SBD format (from read_nova_iridium_sbd.m):
      - Byte 0: packet type
      - Bytes 1-2: total message length (uint16 big-endian, includes header)
      - Bytes 3+: payload data

    Each file contains exactly one message.

    Parameters
    ----------
    filepath : Path
        Path to the .sbd file.

    Returns
    -------
    SbdFile or None
        Parsed file with one frame, or None if file is empty/invalid.
    """
    filepath = Path(filepath)
    file_size = filepath.stat().st_size

    if file_size < 3:
        return None

    meta = parse_sbd_filename(filepath.name)

    raw = filepath.read_bytes()

    # Byte 0 = packet type
    pack_type = raw[0]

    # Bytes 1-2 = total message length (big-endian uint16)
    msg_length = (raw[1] << 8) | raw[2]

    # Validate: declared length should not exceed file size
    if msg_length > file_size:
        msg_length = file_size  # Use available data (matches MATLAB tolerance)

    # Payload = everything after byte 0 (includes the 2-byte length field)
    # The decoder expects payload starting from byte 1 (with length field intact)
    payload = bytes(raw[1:msg_length])

    sbd_file = SbdFile(
        path=filepath,
        file_name=filepath.name,
        file_date=meta["date"],
        imei=meta["imei"],
        momsn=meta["momsn"],
    )

    frame = SbdFrame(
        pack_type=pack_type,
        payload=payload,
        file_name=filepath.name,
        file_date=meta["date"],
    )
    sbd_file.frames.append(frame)

    return sbd_file


FRAME_SIZE_SBD2 = 140


def read_sbd_file_sbd2(filepath: Path) -> Optional[SbdFile]:
    """
    Read a single NKE SBD2 .sbd file with 140-byte frames.

    SBD2 uses fixed 140-byte frames (vs 100-byte for standard NKE).
    Byte 0 = packet type; bytes 1-139 = payload.
    Frames that are all zeros are padding and are skipped.

    Parameters
    ----------
    filepath : Path
        Path to the .sbd file.

    Returns
    -------
    SbdFile or None
        Parsed file with frames, or None if file has unexpected size.
    """
    filepath = Path(filepath)
    file_size = filepath.stat().st_size

    if file_size == 0 or file_size % FRAME_SIZE_SBD2 != 0:
        return None

    meta = parse_sbd_filename(filepath.name)

    sbd_file = SbdFile(
        path=filepath,
        file_name=filepath.name,
        file_date=meta["date"],
        imei=meta["imei"],
        momsn=meta["momsn"],
    )

    raw = filepath.read_bytes()
    n_frames = len(raw) // FRAME_SIZE_SBD2

    for i in range(n_frames):
        frame_bytes = raw[i * FRAME_SIZE_SBD2 : (i + 1) * FRAME_SIZE_SBD2]

        # Skip all-zero frames
        if all(b == 0 for b in frame_bytes):
            continue

        frame = SbdFrame(
            pack_type=frame_bytes[0],
            payload=bytes(frame_bytes[1:]),
            file_name=filepath.name,
            file_date=meta["date"],
        )
        sbd_file.frames.append(frame)

    return sbd_file


def read_sbd_directory(sbd_dir: Path, imei_filter: str = "",
                       mode: str = "nke") -> List[SbdFile]:
    """
    Read all .sbd files from a directory, sorted by filename (chronological).

    Parameters
    ----------
    sbd_dir : Path
        Directory containing .sbd files.
    imei_filter : str, optional
        If set, only read files matching this IMEI.
    mode : str
        Frame format: "nke" for fixed 100-byte frames (default),
        "nova" for variable-length NOVA/DOVA messages,
        "sbd2" for fixed 140-byte frames (PROVOR SBD2).

    Returns
    -------
    list of SbdFile
        Sorted list of parsed SBD files.
    """
    sbd_dir = Path(sbd_dir)
    files = sorted(sbd_dir.glob("*.sbd"))

    if mode == "nova":
        reader_fn = read_sbd_file_nova
    elif mode == "sbd2":
        reader_fn = read_sbd_file_sbd2
    else:
        reader_fn = read_sbd_file

    results = []
    for f in files:
        if imei_filter:
            meta = parse_sbd_filename(f.name)
            if meta["imei"] != imei_filter:
                continue

        sbd_file = reader_fn(f)
        if sbd_file and sbd_file.frames:
            results.append(sbd_file)

    return results


def collect_all_frames(sbd_dir: Path, imei_filter: str = "",
                       mode: str = "nke") -> List[SbdFrame]:
    """
    Collect all frames from all SBD files in a directory.

    Parameters
    ----------
    sbd_dir : Path
        Directory containing .sbd files.
    imei_filter : str, optional
        Filter by IMEI.
    mode : str
        Frame format: "nke" for fixed 100-byte frames (default),
        "nova" for variable-length NOVA/DOVA messages.

    Returns
    -------
    list of SbdFrame
        All valid frames, ordered chronologically.
    """
    sbd_files = read_sbd_directory(sbd_dir, imei_filter, mode=mode)
    frames = []
    for sf in sbd_files:
        frames.extend(sf.frames)
    return frames
