"""
pfv2_tech_decoder.py
====================
Decode PFV2 technical .hex files (TEC files).

PFV2 tech files use an event-driven TLV (Tag-Length-Value) structure:
  - 2-byte event ID (little-endian uint16 stored big-endian via get_bits then swapped)
  - Variable-length payload depending on event ID

Key events:
  0     → Product information (firmware, ID)
  1     → Mission/cycle information
  2     → Life information
  1000+ → Phase timing events (descent, park, ascent, etc.)
  2000  → Grounding
  3000+ → Alarms
  7000  → GPS location fix
  7001  → Last Iridium session info

Reference:
  decode_pfv2_tech_file_401.m, decode_pfv2_tech_file_402.m (Coriolis MATLAB v085h)
  DOI: https://doi.org/10.17882/45589
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# PFV2 epoch: 2000-01-01 00:00:00 UTC
EPOCH_2000 = datetime(2000, 1, 1)


def _epoch2000_to_datetime(epoch_seconds: int) -> Optional[datetime]:
    """Convert PFV2 epoch (seconds since 2000-01-01) to datetime."""
    if epoch_seconds == 0 or epoch_seconds == 0xFFFFFFFF:
        return None
    try:
        return EPOCH_2000 + timedelta(seconds=epoch_seconds)
    except (OverflowError, ValueError):
        return None


def _read_uint8(data: bytes, pos: int) -> tuple:
    """Read 1-byte unsigned integer. Returns (value, new_pos)."""
    if pos >= len(data):
        return 0, pos
    return data[pos], pos + 1


def _read_uint16(data: bytes, pos: int) -> tuple:
    """Read 2-byte unsigned integer (little-endian). Returns (value, new_pos)."""
    if pos + 1 >= len(data):
        return 0, pos
    val = struct.unpack_from("<H", data, pos)[0]
    return val, pos + 2


def _read_int16(data: bytes, pos: int) -> tuple:
    """Read 2-byte signed integer (little-endian). Returns (value, new_pos)."""
    if pos + 1 >= len(data):
        return 0, pos
    val = struct.unpack_from("<h", data, pos)[0]
    return val, pos + 2


def _read_uint32(data: bytes, pos: int) -> tuple:
    """Read 4-byte unsigned integer (little-endian). Returns (value, new_pos)."""
    if pos + 3 >= len(data):
        return 0, pos
    val = struct.unpack_from("<I", data, pos)[0]
    return val, pos + 4


def _read_float32(data: bytes, pos: int) -> tuple:
    """Read 4-byte IEEE 754 single-precision float (little-endian). Returns (value, new_pos)."""
    if pos + 3 >= len(data):
        return 0.0, pos
    val = struct.unpack_from("<f", data, pos)[0]
    return val, pos + 4


def _read_string(data: bytes, pos: int, length: int) -> tuple:
    """Read fixed-length null-terminated string. Returns (string, new_pos)."""
    if pos + length > len(data):
        return "", pos
    raw = data[pos:pos + length]
    # Find null terminator
    null_idx = raw.find(b'\x00')
    if null_idx >= 0:
        raw = raw[:null_idx]
    return raw.decode("ascii", errors="replace"), pos + length


@dataclass
class Pfv2GpsFix:
    """GPS fix from a PFV2 tech file."""
    time: Optional[datetime]
    latitude: float
    longitude: float
    num_satellites: int = 0
    valid: bool = True
    clock_drift_sec: float = 0.0
    session_duration_sec: int = 0


@dataclass
class Pfv2PhaseEvent:
    """Phase timing event from a PFV2 tech file."""
    event_id: int
    label: str
    time: Optional[datetime]
    pressure_dbar: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Pfv2TechResult:
    """Complete decoded result from a PFV2 tech file."""
    mission: int = 0
    cycle: int = 0
    float_id: str = ""
    firmware: str = ""
    gps_fixes: List[Pfv2GpsFix] = field(default_factory=list)
    phase_events: List[Pfv2PhaseEvent] = field(default_factory=list)
    tech_data: Dict[str, Any] = field(default_factory=dict)


def decode_tech_file(data: bytes, decoder_id: int = 401) -> Pfv2TechResult:
    """
    Decode a PFV2 technical .hex file.

    Parameters
    ----------
    data : bytes
        Raw bytes of the decompressed tech file.
    decoder_id : int
        Decoder ID (401 or 402) — affects some event payload sizes.

    Returns
    -------
    Pfv2TechResult
        Decoded technical data including GPS fixes and phase events.
    """
    result = Pfv2TechResult()
    pos = 0

    while pos + 1 < len(data):
        # Read 2-byte event number (little-endian)
        evt_num, pos = _read_uint16(data, pos)

        try:
            pos = _decode_event(data, pos, evt_num, decoder_id, result)
        except Exception as e:
            logger.warning(
                "PFV2 tech: Error decoding event %d at byte %d: %s",
                evt_num, pos, e
            )
            break  # Can't recover position if event layout is unknown

    return result


def _decode_event(data: bytes, pos: int, evt_num: int,
                  decoder_id: int, result: Pfv2TechResult) -> int:
    """Decode a single tech event and advance position. Returns new pos."""

    # ── Event 0: Product Information ──
    if evt_num == 0:
        float_id, pos = _read_string(data, pos, 32)
        result.float_id = float_id

        # Firmware: 5-byte type + 3 bytes (version, subversion, release)
        fw_type, pos = _read_string(data, pos, 6)
        fw_type = fw_type.rstrip('\x00')
        version, pos = _read_uint8(data, pos)
        sub_version, pos = _read_uint8(data, pos)
        release, pos = _read_uint8(data, pos)
        result.firmware = f"{fw_type} v20{version}.{sub_version}.{release}"

        # Firmware checksum (4 bytes)
        _checksum, pos = _read_uint32(data, pos)
        result.tech_data["firmware_checksum"] = _checksum

    # ── Event 1: Mission/Cycle Information ──
    elif evt_num == 1:
        mission, pos = _read_uint16(data, pos)
        cycle, pos = _read_uint16(data, pos)
        _loop_idx, pos = _read_uint8(data, pos)
        _cycle_idx, pos = _read_uint8(data, pos)
        result.mission = mission
        result.cycle = cycle

    # ── Event 2: Life Information ──
    elif evt_num == 2:
        profiles_done, pos = _read_uint16(data, pos)
        total_distance, pos = _read_float32(data, pos)
        total_duration, pos = _read_uint32(data, pos)
        result.tech_data["profiles_done"] = profiles_done
        result.tech_data["total_distance_km"] = total_distance
        result.tech_data["total_duration_sec"] = total_duration

    # ── Events 1000-1021: Phase Events ──
    elif 1000 <= evt_num <= 1021:
        pos = _decode_phase_event(data, pos, evt_num, decoder_id, result)

    # ── Event 2000: Grounding ──
    elif evt_num == 2000:
        epoch, pos = _read_uint32(data, pos)
        pres, pos = _read_uint16(data, pos)
        _oil_vol, pos = _read_uint16(data, pos)
        evt = Pfv2PhaseEvent(
            event_id=evt_num,
            label="grounding",
            time=_epoch2000_to_datetime(epoch),
            pressure_dbar=pres / 10.0 - 100.0,
        )
        result.phase_events.append(evt)

    # ── Events 3000-3002: Alarms ──
    elif 3000 <= evt_num <= 3002:
        # Variable payload, skip safely
        epoch, pos = _read_uint32(data, pos)
        alarm_code, pos = _read_uint8(data, pos)
        result.tech_data.setdefault("alarms", []).append({
            "event": evt_num,
            "time": str(_epoch2000_to_datetime(epoch)),
            "code": alarm_code,
        })

    # ── Event 4000: Buoyancy action ──
    elif evt_num == 4000:
        epoch, pos = _read_uint32(data, pos)
        pres, pos = _read_uint16(data, pos)
        action_type, pos = _read_uint8(data, pos)
        volume, pos = _read_uint16(data, pos)
        duration, pos = _read_uint16(data, pos)
        result.tech_data.setdefault("buoyancy_actions", []).append({
            "time": str(_epoch2000_to_datetime(epoch)),
            "pressure_dbar": pres / 10.0 - 100.0,
            "action_type": action_type,
            "volume": volume,
            "duration_sec": duration,
        })

    # ── Event 5000: Pressure monitoring (spy) ──
    elif evt_num == 5000:
        epoch, pos = _read_uint32(data, pos)
        pres, pos = _read_uint16(data, pos)
        result.tech_data.setdefault("pressure_spy", []).append({
            "time": str(_epoch2000_to_datetime(epoch)),
            "pressure_dbar": pres / 10.0 - 100.0,
        })

    # ── Event 6000: Internal vacuum ──
    elif evt_num == 6000:
        vacuum, pos = _read_uint16(data, pos)
        result.tech_data["internal_vacuum_mbar"] = vacuum

    # ── Event 7000: GPS Location ──
    elif evt_num == 7000:
        epoch, pos = _read_uint32(data, pos)
        latitude, pos = _read_float32(data, pos)
        longitude, pos = _read_float32(data, pos)
        num_sat, pos = _read_uint8(data, pos)
        valid_fix, pos = _read_uint8(data, pos)
        clock_drift, pos = _read_float32(data, pos)
        session_dur, pos = _read_uint16(data, pos)

        gps = Pfv2GpsFix(
            time=_epoch2000_to_datetime(epoch),
            latitude=latitude,
            longitude=longitude,
            num_satellites=num_sat,
            valid=(valid_fix == 1),
            clock_drift_sec=clock_drift,
            session_duration_sec=session_dur,
        )
        result.gps_fixes.append(gps)

    # ── Event 7001: Last Iridium Session ──
    elif evt_num == 7001:
        epoch, pos = _read_uint32(data, pos)
        session_dur, pos = _read_uint16(data, pos)
        sbdi_success, pos = _read_uint16(data, pos)
        sbdi_total, pos = _read_uint16(data, pos)
        cmd_accepted, pos = _read_uint8(data, pos)
        cmd_refused, pos = _read_uint8(data, pos)
        cmd_unknown, pos = _read_uint8(data, pos)
        recv_files, pos = _read_uint16(data, pos)
        recv_size, pos = _read_float32(data, pos)  # float32 for large sizes
        result.tech_data["last_iridium_session"] = {
            "time": str(_epoch2000_to_datetime(epoch)),
            "duration_sec": session_dur,
            "sbdi_success": sbdi_success,
            "sbdi_total": sbdi_total,
            "commands_accepted": cmd_accepted,
            "commands_refused": cmd_refused,
            "received_files": recv_files,
            "received_bytes": recv_size,
        }

    else:
        # Unknown event — we can't know the payload size, so we must stop
        logger.debug("PFV2 tech: Unknown event %d at pos %d, stopping", evt_num, pos)
        # Return a position past end to break the loop
        return len(data)

    return pos


def _decode_phase_event(data: bytes, pos: int, evt_num: int,
                        decoder_id: int, result: Pfv2TechResult) -> int:
    """Decode phase timing events (1000-1021)."""

    # Phase event labels (from MATLAB decode_pfv2_tech_file_401.m)
    PHASE_LABELS = {
        1000: "buoyancy_reduction_start",
        1001: "buoyancy_reduction_end",
        1002: "buoyancy_inversion_start",
        1003: "buoyancy_inversion_end",
        1004: "descent_to_park_start",
        1005: "descent_to_park_end",
        1006: "descent_to_prof_start",
        1007: "descent_to_prof_end",
        1008: "prof_drift_start",
        1009: "park_end",
        1010: "deep_profile_start",
        1011: "deep_profile_end",
        1012: "ascent_start",
        1013: "ascent_end",
        1014: "near_surface_start",
        1015: "near_surface_end",
        1016: "transmission_start",
        1017: "transmission_end",
        1018: "in_air_start",
        1019: "in_air_end",
        1020: "ice_detection",
        1021: "surface_abort",
    }

    label = PHASE_LABELS.get(evt_num, f"phase_{evt_num}")

    # All phase events start with a 4-byte epoch timestamp
    epoch, pos = _read_uint32(data, pos)
    time = _epoch2000_to_datetime(epoch)

    pressure = None
    extra: Dict[str, Any] = {}

    # Events that have additional pressure field in decoder 402
    EVENTS_WITH_PRESSURE_402 = {1000, 1002, 1004}

    if evt_num in EVENTS_WITH_PRESSURE_402 and decoder_id >= 402:
        pres_raw, pos = _read_uint16(data, pos)
        pressure = pres_raw / 10.0 - 100.0

    # Events with extended payload (common to 401 and 402)
    if evt_num == 1001:  # buoyancy_reduction_end
        ev_lump, pos = _read_uint16(data, pos)
        ev_transferred, pos = _read_uint16(data, pos)
        actions, pos = _read_uint8(data, pos)
        extra = {"ev_lump": ev_lump, "ev_transferred": ev_transferred, "actions": actions}

    elif evt_num == 1003:  # buoyancy_inversion_end
        pres_offset, pos = _read_uint16(data, pos)
        pres_min, pos = _read_uint16(data, pos)
        ev_vol, pos = _read_uint16(data, pos)
        actions, pos = _read_uint8(data, pos)
        extra = {
            "pressure_offset": pres_offset / 10.0,
            "pressure_min_dbar": pres_min / 10.0 - 100.0,
            "ev_volume": ev_vol,
            "actions": actions,
        }

    elif evt_num == 1005:  # descent_to_park_end
        ev_vol, pos = _read_uint16(data, pos)
        actions, pos = _read_uint8(data, pos)
        max_pres, pos = _read_uint16(data, pos)
        extra = {
            "ev_volume": ev_vol,
            "actions": actions,
            "max_pressure_dbar": max_pres / 10.0 - 100.0,
        }
        pressure = max_pres / 10.0 - 100.0

    elif evt_num == 1009:  # park_end
        min_pres, pos = _read_uint16(data, pos)
        max_pres, pos = _read_uint16(data, pos)
        stabilized, pos = _read_uint8(data, pos)
        unstabilized, pos = _read_uint8(data, pos)
        repositioning, pos = _read_uint8(data, pos)
        ev_vol, pos = _read_uint16(data, pos)
        ev_actions, pos = _read_uint8(data, pos)
        pump_vol, pos = _read_uint16(data, pos)
        pump_actions, pos = _read_uint8(data, pos)
        extra = {
            "min_pressure_dbar": min_pres / 10.0 - 100.0,
            "max_pressure_dbar": max_pres / 10.0 - 100.0,
            "stabilized": stabilized,
            "unstabilized": unstabilized,
            "repositioning": repositioning,
            "ev_volume": ev_vol,
            "ev_actions": ev_actions,
            "pump_volume": pump_vol,
            "pump_actions": pump_actions,
        }

    elif evt_num == 1013:  # ascent_end
        pump_vol, pos = _read_uint16(data, pos)
        actions, pos = _read_uint8(data, pos)
        extra = {"pump_volume": pump_vol, "actions": actions}

    evt = Pfv2PhaseEvent(
        event_id=evt_num,
        label=label,
        time=time,
        pressure_dbar=pressure,
        extra=extra,
    )
    result.phase_events.append(evt)

    return pos
