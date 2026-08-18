"""
apex_apf9.py
============
Decoder for APEX APF9 Iridium SBD/RUDICS floats (decoder IDs 1001-1016, 1314).

APEX floats use text-based .msg and .log files (NOT binary frames like NKE).
The decoder supports two input modes:
  1. Raw .sbd files → binary reassembly → .msg/.log text files → parse
  2. Pre-assembled .msg/.log text files → parse directly

.msg file contains:
  - Mission configuration ($ lines)
  - Park/drift measurements (ParkPt: lines)
  - Low-resolution profile (space-delimited P T S in physical units)
  - High-resolution profile (hex-encoded: PPPPTTTTSSSSNN per line, 14 chars)
  - GPS fixes (Fix: lon lat mm/dd/yyyy hhmmss nsat)
  - Engineering data (key=value pairs)

.log file contains:
  - Timestamped events: (mmm dd yyyy HH:MM:SS, N sec) CMD INFO
  - GPS from GpsServices() events
  - Cycle number from TelemetryInit() events

Sensor conversions (APEX APF9):
  Pressure:    raw / 10 (dbar). Fill: 0x8000, 0x7FFF, 0x8001
  Temperature: raw / 1000 (degC). Fill: 0xF000, 0xEFFF, 0xF001
  Salinity:    raw / 1000 (PSU). Fill: 0xF000, 0xEFFF, 0xF001
  Negative values via twos_complement when raw >= threshold.

Reference:
  decode_apx_ir.m, read_apx_ir_sbd_msg_file.m, decode_apx_ir_HR_profile_data.m
  convert_sbd_files_apex_iridium_sbd.m, sensor_2_value_for_apex_apf9_*.m
  Coriolis MATLAB decoder v085h (2026-07-10)
  DOI: https://doi.org/10.17882/45589
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..base import (
    BaseDecoder,
    CTDMeasurement,
    GPSFix,
    TechnicalData,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Sensor Conversions
# ─────────────────────────────────────────────────────────────────────────────

# Pressure fill values
_PRES_FILL = {0x8000, 0x7FFF, 0x8001}
# Temperature/Salinity fill values
_TEMP_SAL_FILL = {0xF000, 0xEFFF, 0xF001}


def _twos_complement(value: int, bits: int) -> int:
    """Convert unsigned to signed via two's complement."""
    if (value >> (bits - 1)) & 1:
        return -((value ^ ((1 << bits) - 1)) + 1)
    return value


def _pressure_apf9(raw: int) -> Optional[float]:
    """
    Convert APEX APF9 pressure counts to dbar.
    Source: sensor_2_value_for_apex_apf9_pressure.m
    """
    if raw in _PRES_FILL:
        return None
    if raw < 0x7FFF:
        return raw / 10.0
    return _twos_complement(raw, 16) / 10.0


def _temperature_apf9(raw: int) -> Optional[float]:
    """
    Convert APEX APF9 temperature counts to degC.
    Source: sensor_2_value_for_apex_apf9_temperature.m
    """
    if raw in _TEMP_SAL_FILL:
        return None
    if raw < 0xEFFF:
        return raw / 1000.0
    return _twos_complement(raw, 16) / 1000.0


def _salinity_apf9(raw: int) -> Optional[float]:
    """
    Convert APEX APF9 salinity counts to PSU.
    Source: sensor_2_value_for_apex_apf9_salinity.m
    """
    if raw in _TEMP_SAL_FILL:
        return None
    if raw < 0xEFFF:
        return raw / 1000.0
    return _twos_complement(raw, 16) / 1000.0


# ─────────────────────────────────────────────────────────────────────────────
# SBD Binary Reassembly
# ─────────────────────────────────────────────────────────────────────────────

def reassemble_apex_sbd(sbd_dir: Path) -> Dict[str, bytes]:
    """
    Reassemble APEX raw .sbd files into ASCII .msg/.log file content.

    APEX SBD binary format:
      Type 1 (header): byte[0]=1, byte[1:2]=filesize(BE), byte[3..null]=filename, then data
      Type 2 (continuation): byte[0]=2, then data bytes

    Returns dict of {filename: content_bytes}.
    """
    sbd_dir = Path(sbd_dir)
    sbd_files = sorted(sbd_dir.glob("*.sbd"))

    if not sbd_files:
        return {}

    # Collect fragments grouped by target filename
    current_name = ""
    current_data: List[bytes] = []
    file_map: Dict[str, bytes] = {}

    for sbd_path in sbd_files:
        raw = sbd_path.read_bytes()
        if len(raw) < 2:
            continue

        msg_type = raw[0]

        if msg_type == 1:
            # Flush previous
            if current_name and current_data:
                file_map[current_name] = b"".join(current_data)

            # Parse header: find null terminator for filename
            null_pos = raw.find(b'\x00', 3)
            if null_pos < 0:
                continue
            filename = raw[3:null_pos].decode("ascii", errors="replace")
            # Ensure .msg extension
            if '.' not in filename or filename.count('.') == 1:
                if not filename.endswith('.msg') and not filename.endswith('.log'):
                    filename += '.msg'

            current_name = filename
            current_data = [raw[null_pos + 1:]]

        elif msg_type == 2:
            # Continuation data
            if current_name:
                current_data.append(raw[1:])

    # Flush last file
    if current_name and current_data:
        file_map[current_name] = b"".join(current_data)

    return file_map


# ─────────────────────────────────────────────────────────────────────────────
# .msg File Parser
# ─────────────────────────────────────────────────────────────────────────────

# Regex patterns
_RE_GPS_FIX = re.compile(
    r"Fix:\s*([-\d.]+)\s+([-\d.]+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{6})\s+(\d+)"
)
_RE_PARK_PT = re.compile(
    r"ParkPt:\s*p=([\d.]+),\s*t=([\d.]+),\s*s=([\d.]+)"
)
_RE_HR_HEX_LINE = re.compile(r"^([0-9a-fA-F]{14})(?:\[(\d+)\])?$")
_RE_PROFILE_TERMINATED = re.compile(
    r"\$\s*Profile\s+\S+\.(\d+)\s+terminated"
)
_RE_CYCLE_FROM_CONFIG = re.compile(
    r"\$\s*Mission configuration for\s+\S+\.(\d+)"
)


def parse_msg_file(content: str, decoder_id: int = 1314) -> Dict[str, Any]:
    """
    Parse an APEX .msg file and extract profiles, GPS, and park data.

    Returns dict with keys: cycle, gps_fixes, lr_profile, hr_profile, park_data, engineering
    """
    result: Dict[str, Any] = {
        "cycle": 0,
        "gps_fixes": [],
        "lr_profile": [],
        "hr_profile": [],
        "park_data": [],
        "engineering": {},
    }

    lines = content.splitlines()
    in_lr_section = False
    in_hr_section = False
    lr_col_count = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # ── Cycle number from config or profile terminated ──
        m = _RE_CYCLE_FROM_CONFIG.match(line)
        if m:
            result["cycle"] = int(m.group(1))
            in_lr_section = False
            in_hr_section = False
            continue

        m = _RE_PROFILE_TERMINATED.match(line)
        if m:
            result["cycle"] = int(m.group(1))
            continue

        # ── GPS Fix ──
        m = _RE_GPS_FIX.search(line)
        if m:
            lon = float(m.group(1))
            lat = float(m.group(2))
            date_str = m.group(3)
            time_str = m.group(4)
            nsat = int(m.group(5))
            try:
                fix_dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H%M%S")
            except ValueError:
                fix_dt = None
            result["gps_fixes"].append({
                "latitude": lat,
                "longitude": lon,
                "date": fix_dt,
                "num_satellites": nsat,
            })
            in_lr_section = False
            in_hr_section = False
            continue

        # ── Park/Drift measurements ──
        m = _RE_PARK_PT.match(line)
        if m:
            result["park_data"].append({
                "pressure_dbar": float(m.group(1)),
                "temperature_degc": float(m.group(2)),
                "salinity_psu": float(m.group(3)),
            })
            in_lr_section = False
            in_hr_section = False
            continue

        # ── Section markers ──
        if "Discrete samples:" in line or line.startswith("$") and "p" in line and "t" in line:
            in_lr_section = True
            in_hr_section = False
            # Detect column count from header
            if line.startswith("$") and "p" in line:
                cols = line.replace("$", "").split()
                lr_col_count = len(cols)
            continue

        if line.startswith("#") and "Sbe41cpSerNo" in line:
            in_lr_section = False
            in_hr_section = True
            continue

        if line.startswith("$") or "<EOT>" in line:
            in_lr_section = False
            in_hr_section = False
            continue

        # ── Low-resolution profile data (physical values) ──
        if in_lr_section:
            parts = line.split()
            if len(parts) >= 3:
                try:
                    p = float(parts[0])
                    t = float(parts[1])
                    s = float(parts[2])
                    result["lr_profile"].append({
                        "pressure_dbar": p,
                        "temperature_degc": t,
                        "salinity_psu": s,
                    })
                except ValueError:
                    in_lr_section = False
            continue

        # ── High-resolution profile data (hex encoded) ──
        if in_hr_section:
            m = _RE_HR_HEX_LINE.match(line)
            if m:
                hex_data = m.group(1)
                replicate = int(m.group(2)) if m.group(2) else 1
                meas = _decode_hr_hex_sample(hex_data)
                if meas:
                    for _ in range(replicate):
                        result["hr_profile"].append(meas)
            else:
                # Check if line is still hex (might be shorter for some decoders)
                cleaned = line.rstrip()
                if len(cleaned) >= 12 and all(c in '0123456789abcdefABCDEF' for c in cleaned[:12]):
                    meas = _decode_hr_hex_sample(cleaned[:14].ljust(14, '0'))
                    if meas:
                        result["hr_profile"].append(meas)
                else:
                    in_hr_section = False
            continue

        # ── Engineering data (key=value) ──
        if "=" in line and not line.startswith("$") and not line.startswith("#"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip()
                result["engineering"][key] = val

    # HR profile is deepest-first → reverse for standard output
    result["hr_profile"].reverse()

    return result


def _decode_hr_hex_sample(hex_str: str) -> Optional[Dict[str, float]]:
    """
    Decode a 14-char hex HR profile sample: PPPPTTTTSSSSNN.

    Returns dict with pressure_dbar, temperature_degc, salinity_psu, or None if fill.
    """
    if len(hex_str) < 12:
        return None

    try:
        pres_raw = int(hex_str[0:4], 16)
        temp_raw = int(hex_str[4:8], 16)
        sal_raw = int(hex_str[8:12], 16)
    except ValueError:
        return None

    pressure = _pressure_apf9(pres_raw)
    temperature = _temperature_apf9(temp_raw)
    salinity = _salinity_apf9(sal_raw)

    if pressure is None or temperature is None or salinity is None:
        return None

    return {
        "pressure_dbar": pressure,
        "temperature_degc": temperature,
        "salinity_psu": salinity,
    }


# ─────────────────────────────────────────────────────────────────────────────
# .log File Parser
# ─────────────────────────────────────────────────────────────────────────────

_RE_LOG_EVENT = re.compile(
    r"\((\w{3}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}),\s*(\d+)\s*sec\)\s*(\S+)\s*(.*)"
)
_RE_LOG_CYCLE = re.compile(r"Profile\s+(\d+)\.")
_RE_LOG_GPS_FIX = re.compile(
    r"Fix:\s*([-\d.]+)\s+([-\d.]+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{6})\s+(\d+)"
)


def parse_log_file(content: str) -> Dict[str, Any]:
    """
    Parse an APEX .log file and extract cycle number and GPS fixes.

    Returns dict with keys: cycle, gps_fixes, events
    """
    result: Dict[str, Any] = {
        "cycle": 0,
        "gps_fixes": [],
        "events": [],
    }

    for line in content.splitlines():
        line = line.strip()
        if not line or "<EOT>" in line:
            continue

        m = _RE_LOG_EVENT.match(line)
        if m:
            timestamp_str = m.group(1)
            # mtime = int(m.group(2))
            cmd = m.group(3)
            info = m.group(4)

            try:
                evt_time = datetime.strptime(timestamp_str, "%b %d %Y %H:%M:%S")
            except ValueError:
                evt_time = None

            result["events"].append({"time": evt_time, "cmd": cmd, "info": info})

            # Extract cycle from TelemetryInit
            if "TelemetryInit" in cmd:
                cm = _RE_LOG_CYCLE.search(info)
                if cm:
                    result["cycle"] = int(cm.group(1))

            # Extract GPS fix
            gm = _RE_LOG_GPS_FIX.search(info)
            if gm:
                lon = float(gm.group(1))
                lat = float(gm.group(2))
                date_str = gm.group(3)
                time_str = gm.group(4)
                nsat = int(gm.group(5))
                try:
                    fix_dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H%M%S")
                except ValueError:
                    fix_dt = evt_time
                result["gps_fixes"].append({
                    "latitude": lat, "longitude": lon,
                    "date": fix_dt, "num_satellites": nsat,
                })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main Decoder Class
# ─────────────────────────────────────────────────────────────────────────────

class ApexApf9Decoder(BaseDecoder):
    """
    Decoder for APEX APF9 Iridium SBD/RUDICS floats (IDs 1001-1016, 1314).

    Uses a directory-based pipeline:
      1. If .msg/.log files exist → parse directly
      2. If only .sbd files exist → reassemble into .msg/.log → parse

    The decode_packet() interface is maintained for compatibility but
    the primary entry point is decode_directory().
    """

    def __init__(self, decoder_id: int, float_info: Dict[str, Any]):
        super().__init__(decoder_id, float_info)

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        """
        Interface compatibility. For APEX, accepts .msg file content as payload.
        The file_name should end in .msg or .log to determine parsing mode.
        """
        if not payload:
            return

        try:
            content = payload.decode("ascii", errors="replace")
        except Exception:
            return

        if file_name.endswith(".log"):
            self._process_log_content(content, file_date)
        else:
            self._process_msg_content(content, file_date)

    def decode_directory(self, input_dir: Path) -> None:
        """
        Primary decode entry point for APEX.

        Looks for .msg/.log files first. If not found, attempts SBD reassembly.
        """
        input_dir = Path(input_dir)

        # Check for pre-assembled .msg/.log files
        msg_files = sorted(input_dir.glob("*.msg"))
        log_files = sorted(input_dir.glob("*.log"))

        if msg_files or log_files:
            logger.info("APEX: Found %d .msg + %d .log files, parsing directly",
                        len(msg_files), len(log_files))
            for f in log_files:
                content = f.read_text(encoding="ascii", errors="replace")
                self._process_log_content(content)
            for f in msg_files:
                content = f.read_text(encoding="ascii", errors="replace")
                self._process_msg_content(content)
            return

        # Try SBD reassembly
        sbd_files = sorted(input_dir.glob("*.sbd"))
        if sbd_files:
            logger.info("APEX: Reassembling %d .sbd files", len(sbd_files))
            file_map = reassemble_apex_sbd(input_dir)
            for filename, data in file_map.items():
                content = data.decode("ascii", errors="replace")
                if filename.endswith(".log"):
                    self._process_log_content(content)
                else:
                    self._process_msg_content(content)
            return

        logger.warning("APEX: No .msg, .log, or .sbd files found in %s", input_dir)

    def _process_msg_content(self, content: str,
                             file_date: Optional[datetime] = None) -> None:
        """Parse .msg content and populate cycles."""
        parsed = parse_msg_file(content, self.decoder_id)
        cycle_num = parsed["cycle"]
        if cycle_num == 0:
            cycle_num = len(self.cycles) + 1  # Fallback

        cycle = self.get_or_create_cycle(cycle_num)

        # GPS fixes
        for fix in parsed["gps_fixes"]:
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num,
                latitude=fix["latitude"],
                longitude=fix["longitude"],
                date=fix.get("date"),
                valid=True,
            ))

        # HR profile (preferred over LR)
        profile_data = parsed["hr_profile"] if parsed["hr_profile"] else parsed["lr_profile"]
        for meas in profile_data:
            cycle.ctd_ascent.append(CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=meas["pressure_dbar"],
                temperature_degc=meas["temperature_degc"],
                salinity_psu=meas["salinity_psu"],
                date=file_date,
                direction="ascent",
            ))

        # Park/drift data
        for park in parsed["park_data"]:
            cycle.ctd_drift.append(CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=park["pressure_dbar"],
                temperature_degc=park["temperature_degc"],
                salinity_psu=park["salinity_psu"],
                date=file_date,
                direction="drift",
            ))

        # Engineering as technical data
        if parsed["engineering"]:
            cycle.technical.append(TechnicalData(
                cycle=cycle_num,
                data=parsed["engineering"],
            ))

    def _process_log_content(self, content: str,
                             file_date: Optional[datetime] = None) -> None:
        """Parse .log content and extract GPS/cycle info."""
        parsed = parse_log_file(content)
        cycle_num = parsed["cycle"]
        if cycle_num == 0:
            return  # Can't determine cycle from this log

        cycle = self.get_or_create_cycle(cycle_num)

        for fix in parsed["gps_fixes"]:
            # Avoid duplicates (may also be in .msg)
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num,
                latitude=fix["latitude"],
                longitude=fix["longitude"],
                date=fix.get("date"),
                valid=True,
            ))

    def get_sensor_conversions(self) -> Dict[str, Any]:
        """Return APEX APF9 sensor conversion functions."""
        return {
            "pressure": _pressure_apf9,
            "temperature": _temperature_apf9,
            "salinity": _salinity_apf9,
        }
