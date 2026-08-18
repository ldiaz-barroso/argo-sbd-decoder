"""
apex_apf11.py
=============
Decoder for APEX APF11 Iridium SBD floats (decoder IDs 1101-1132, 1321-1323).

APF11 floats produce binary science_log and text system_log files.
The decoder supports two input modes:
  1. Raw .sbd files → binary reassembly → science_log.bin + system_log.txt → parse
  2. Pre-assembled log files → parse directly

science_log.bin format (BINARY):
  Each record: [length(1) + recordId(1) + timestamp(4, LE unix epoch) + data(N)]
  CTD_PTS (recordId=13): PRES(4,float32) + TEMP(4,float32) + PSAL(4,float32)
  GPS (recordId=1): lat(4,float32) + lon(4,float32) + nbsat(4,uint32)
  Values are already in PHYSICAL UNITS (no conversion needed).

system_log.txt format (ASCII):
  Lines: yyyymmddTHHMMSS|priority|function|message
  GPS Fix: MM/DD/YYYY HH:MM:SS, latitude, longitude, nb_satellites

Reference:
  decode_apx_apf11_ir.m, decode_science_log_apx_apf11_ir.m,
  read_apx_apf11_ir_binary_log_file.m, convert_sbd_files_apex_apf11_iridium_sbd.m
  Coriolis MATLAB decoder v085h (2026-07-10)
  DOI: https://doi.org/10.17882/45589
"""

from __future__ import annotations

import gzip
import logging
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import (
    BaseDecoder,
    CTDMeasurement,
    GPSFix,
    TechnicalData,
)

logger = logging.getLogger(__name__)

# Unix epoch for timestamp conversion
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Science log record IDs
_REC_MESSAGE = 0
_REC_GPS = 1
_REC_CTD_BINS = 10
_REC_CTD_P = 11
_REC_CTD_PT = 12
_REC_CTD_PTS = 13
_REC_CTD_CP = 14
_REC_CTD_PTSH = 15
_REC_CTD_CP_H = 16

# GPS pattern in system_log
_RE_SYS_GPS_FIX = re.compile(
    r"GPS Fix:\s*(\d{1,2}/\d{1,2}/\d{4}\s+\d{2}:\d{2}:\d{2}),\s*([-\d.]+),\s*([-\d.]+),\s*(\d+)"
)
# Cycle number from filename: {id}.{cycle}.science_log.bin or similar
_RE_CYCLE_FROM_FILENAME = re.compile(r"\.(\d+)\.")


def _unix_to_datetime(epoch_sec: int) -> Optional[datetime]:
    """Convert Unix epoch seconds to datetime."""
    if epoch_sec == 0 or epoch_sec == 0xFFFFFFFF:
        return None
    try:
        return datetime(1970, 1, 1) + __import__('datetime').timedelta(seconds=epoch_sec)
    except (OverflowError, ValueError):
        return None


def _unix_to_datetime_fast(epoch_sec: int) -> Optional[datetime]:
    """Convert Unix epoch seconds to datetime (no timezone)."""
    if epoch_sec == 0 or epoch_sec == 0xFFFFFFFF:
        return None
    try:
        return datetime.utcfromtimestamp(epoch_sec)
    except (OSError, OverflowError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SBD Binary Reassembly (APF11-specific)
# ─────────────────────────────────────────────────────────────────────────────

def reassemble_apf11_sbd(sbd_dir: Path) -> Dict[str, bytes]:
    """
    Reassemble APF11 raw .sbd files into float log file content.

    APF11 SBD format:
      Type 1 (header): byte[0]=1, byte[1:2]=expected_count(BE), byte[3..null]=filename, data
      Type 2 (continuation): byte[0]=2, data bytes

    Returns dict of {filename: content_bytes}.
    """
    sbd_dir = Path(sbd_dir)
    sbd_files = sorted(sbd_dir.glob("*.sbd"))
    if not sbd_files:
        return {}

    # Track current file being assembled
    current_name = ""
    current_data: List[bytes] = []
    current_expected = 0
    current_count = 0
    file_map: Dict[str, bytes] = {}

    for sbd_path in sbd_files:
        raw = sbd_path.read_bytes()
        if len(raw) < 2:
            continue

        msg_type = raw[0]

        if msg_type == 1:
            # Flush previous if complete
            if current_name and current_count >= current_expected and current_expected > 0:
                file_map[current_name] = b"".join(current_data)

            # Parse header
            current_expected = (raw[1] << 8) | raw[2]
            null_pos = raw.find(b'\x00', 3)
            if null_pos < 0:
                current_name = ""
                continue
            current_name = raw[3:null_pos].decode("ascii", errors="replace")
            current_data = [raw[null_pos + 1:]]
            current_count = 1

        elif msg_type == 2:
            if current_name:
                current_data.append(raw[1:])
                current_count += 1

    # Flush last file
    if current_name and current_count >= current_expected and current_expected > 0:
        file_map[current_name] = b"".join(current_data)

    # Decompress .gz files
    gz_keys = [k for k in file_map if k.endswith(".gz")]
    for gz_name in gz_keys:
        try:
            decompressed = gzip.decompress(file_map[gz_name])
            base_name = gz_name[:-3]  # Remove .gz
            file_map[base_name] = decompressed
            del file_map[gz_name]
        except Exception as e:
            logger.warning("APF11: Failed to decompress %s: %s", gz_name, e)

    return file_map


# ─────────────────────────────────────────────────────────────────────────────
# science_log.bin Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_science_log(data: bytes) -> Dict[str, Any]:
    """
    Parse an APF11 science_log.bin binary file.

    Record structure: [length(1) + recordId(1) + timestamp(4,LE) + payload(N)]
    CTD_PTS values are IEEE 754 single-precision floats in physical units.

    Returns dict with keys: ctd_pts, gps, messages
    """
    result: Dict[str, Any] = {
        "ctd_pts": [],     # List of (datetime, pres, temp, sal)
        "ctd_pt": [],      # List of (datetime, pres, temp)
        "ctd_p": [],       # List of (datetime, pres)
        "gps": [],         # List of (datetime, lat, lon, nbsat)
        "messages": [],    # List of (datetime, text)
    }

    pos = 0
    while pos < len(data):
        if pos + 1 >= len(data):
            break

        rec_length = data[pos]
        if pos + rec_length >= len(data):
            break

        rec_id = data[pos + 1]

        # Timestamp: 4 bytes little-endian uint32 (Unix epoch)
        if pos + 5 >= len(data):
            break
        ts_bytes = data[pos + 2:pos + 6]
        timestamp_raw = struct.unpack_from("<I", ts_bytes)[0]
        ts = _unix_to_datetime_fast(timestamp_raw)

        # Payload starts after the 5-byte header (len + id + 4-byte timestamp)
        payload_start = pos + 6
        payload_end = pos + rec_length + 1  # rec_length includes everything after the length byte

        if rec_id == _REC_MESSAGE:
            # Variable-length ASCII message
            msg_bytes = data[payload_start:payload_end]
            msg_text = msg_bytes.decode("ascii", errors="replace").rstrip('\x00')
            result["messages"].append((ts, msg_text))

        elif rec_id == _REC_GPS:
            # GPS: lat(float32) + lon(float32) + nbsat(uint32) = 12 bytes
            if payload_end - payload_start >= 12:
                lat = struct.unpack_from("<f", data, payload_start)[0]
                lon = struct.unpack_from("<f", data, payload_start + 4)[0]
                nbsat = struct.unpack_from("<I", data, payload_start + 8)[0]
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    result["gps"].append((ts, lat, lon, nbsat))

        elif rec_id == _REC_CTD_PTS:
            # CTD_PTS: PRES(float32) + TEMP(float32) + PSAL(float32) = 12 bytes
            if payload_end - payload_start >= 12:
                pres = struct.unpack_from("<f", data, payload_start)[0]
                temp = struct.unpack_from("<f", data, payload_start + 4)[0]
                sal = struct.unpack_from("<f", data, payload_start + 8)[0]
                # Filter fill values
                if pres != -999 and not (pres == 0 and temp == 0):
                    result["ctd_pts"].append((ts, pres, temp, sal))

        elif rec_id == _REC_CTD_PT:
            # CTD_PT: PRES(float32) + TEMP(float32) = 8 bytes
            if payload_end - payload_start >= 8:
                pres = struct.unpack_from("<f", data, payload_start)[0]
                temp = struct.unpack_from("<f", data, payload_start + 4)[0]
                if pres != -999 and not (pres == 0 and temp == 0):
                    result["ctd_pt"].append((ts, pres, temp))

        elif rec_id == _REC_CTD_P:
            # CTD_P: PRES(float32) = 4 bytes
            if payload_end - payload_start >= 4:
                pres = struct.unpack_from("<f", data, payload_start)[0]
                if pres != -999:
                    result["ctd_p"].append((ts, pres))

        elif rec_id == _REC_CTD_CP:
            # CTD_CP: PRES(float32) + TEMP(float32) + PSAL(float32) + nb_sample(uint16) = 14 bytes
            if payload_end - payload_start >= 14:
                pres = struct.unpack_from("<f", data, payload_start)[0]
                temp = struct.unpack_from("<f", data, payload_start + 4)[0]
                sal = struct.unpack_from("<f", data, payload_start + 8)[0]
                if pres != -999 and not (pres == 0 and temp == 0):
                    result["ctd_pts"].append((ts, pres, temp, sal))

        # Skip other record types (O2, FLBB, etc.) — stored but not CTD
        pos = payload_end

    return result


# ─────────────────────────────────────────────────────────────────────────────
# system_log.txt Parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_system_log(content: str) -> Dict[str, Any]:
    """
    Parse an APF11 system_log.txt file for GPS fixes.

    Format: yyyymmddTHHMMSS|priority|function|message
    GPS: 'GPS Fix: MM/DD/YYYY HH:MM:SS, lat, lon, nbsat'
    """
    result: Dict[str, Any] = {"gps": []}

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        # Look for GPS Fix pattern in the message part
        m = _RE_SYS_GPS_FIX.search(line)
        if m:
            date_str = m.group(1)
            lat = float(m.group(2))
            lon = float(m.group(3))
            nbsat = int(m.group(4))
            try:
                fix_dt = datetime.strptime(date_str.strip(), "%m/%d/%Y %H:%M:%S")
            except ValueError:
                fix_dt = None
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                result["gps"].append((fix_dt, lat, lon, nbsat))

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main Decoder Class
# ─────────────────────────────────────────────────────────────────────────────

class ApexApf11Decoder(BaseDecoder):
    """
    Decoder for APEX APF11 Iridium SBD floats (IDs 1101-1132, 1321-1323).

    Uses binary science_log for CTD profiles and text system_log for GPS.
    CTD values in science_log are already in physical units (IEEE 754 floats).

    Supports:
      - Direct input of .science_log.bin / .system_log.txt files
      - Raw .sbd file reassembly
    """

    def __init__(self, decoder_id: int, float_info: Dict[str, Any]):
        super().__init__(decoder_id, float_info)

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        """
        Interface compatibility. Accepts science_log binary content or system_log text.
        """
        if not payload:
            return

        if "science_log" in file_name or file_name.endswith(".bin"):
            self._process_science_log(payload, file_name)
        elif "system_log" in file_name or file_name.endswith(".txt"):
            content = payload.decode("ascii", errors="replace")
            self._process_system_log(content, file_name)

    def decode_directory(self, input_dir: Path) -> None:
        """
        Primary decode entry point for APF11.

        Looks for science_log.bin / system_log.txt files first.
        If not found, attempts SBD reassembly.
        """
        input_dir = Path(input_dir)

        # Check for pre-assembled log files
        science_files = sorted(input_dir.glob("*science_log*"))
        system_files = sorted(input_dir.glob("*system_log*"))

        if science_files or system_files:
            logger.info("APF11: Found %d science + %d system log files",
                        len(science_files), len(system_files))
            for f in science_files:
                data = f.read_bytes()
                self._process_science_log(data, f.name)
            for f in system_files:
                content = f.read_text(encoding="ascii", errors="replace")
                self._process_system_log(content, f.name)
            return

        # Try SBD reassembly
        sbd_files = sorted(input_dir.glob("*.sbd"))
        if sbd_files:
            logger.info("APF11: Reassembling %d .sbd files", len(sbd_files))
            file_map = reassemble_apf11_sbd(input_dir)
            for filename, data in file_map.items():
                if "science_log" in filename:
                    self._process_science_log(data, filename)
                elif "system_log" in filename:
                    content = data.decode("ascii", errors="replace")
                    self._process_system_log(content, filename)
            return

        logger.warning("APF11: No science_log, system_log, or .sbd files in %s", input_dir)

    def _process_science_log(self, data: bytes, filename: str) -> None:
        """Parse binary science_log and populate cycles."""
        cycle_num = self._cycle_from_filename(filename)
        parsed = parse_science_log(data)

        cycle = self.get_or_create_cycle(cycle_num)

        # CTD profiles (PTS — already in physical units)
        for ts, pres, temp, sal in parsed["ctd_pts"]:
            cycle.ctd_ascent.append(CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=pres,
                temperature_degc=temp,
                salinity_psu=sal,
                date=ts,
                direction="ascent",
            ))

        # CTD PT (no salinity)
        for ts, pres, temp in parsed["ctd_pt"]:
            cycle.ctd_drift.append(CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=pres,
                temperature_degc=temp,
                salinity_psu=0.0,
                date=ts,
                direction="drift",
            ))

        # GPS from science_log
        for ts, lat, lon, nbsat in parsed["gps"]:
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num,
                latitude=lat,
                longitude=lon,
                date=ts,
                valid=True,
            ))

    def _process_system_log(self, content: str, filename: str) -> None:
        """Parse system_log text for GPS fixes."""
        cycle_num = self._cycle_from_filename(filename)
        parsed = parse_system_log(content)

        if not parsed["gps"]:
            return

        cycle = self.get_or_create_cycle(cycle_num)
        for fix_dt, lat, lon, nbsat in parsed["gps"]:
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num,
                latitude=lat,
                longitude=lon,
                date=fix_dt,
                valid=True,
            ))

    def _cycle_from_filename(self, filename: str) -> int:
        """Extract cycle number from APF11 filename pattern: {id}.{cycle}.xxx"""
        m = _RE_CYCLE_FROM_FILENAME.search(filename)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        # Fallback: increment
        return len(self.cycles) + 1

    def get_sensor_conversions(self) -> Dict[str, Any]:
        """APF11 science_log stores values in physical units — no conversion needed."""
        return {
            "pressure": "Already in dbar (IEEE 754 float in science_log)",
            "temperature": "Already in degC (IEEE 754 float in science_log)",
            "salinity": "Already in PSU (IEEE 754 float in science_log)",
        }
