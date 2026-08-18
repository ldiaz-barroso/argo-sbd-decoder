"""
nova_sbd.py
===========
Decoder for NOVA/DOVA floats with Iridium SBD transmission.

Decoder IDs:
  - 2001: NOVA 1.0 (CTD only)
  - 2002: DOVA 2.0 (CTD + dissolved oxygen)
  - 2003: NOVA 0.9 (CTD only, older firmware with different housekeeping layout)

Reference:
  Coriolis MATLAB decoder v085h (2026-07-10):
    decode_nva_data_ir_sbd_2001.m
    decode_nva_data_ir_sbd_2002.m
    decode_nva_data_ir_sbd_2003.m
    sensor_2_value_for_pressure_nva.m
    sensor_2_value_for_temperature_nva.m
    sensor_2_value_for_salinity_nva.m
    sensor_2_value_for_temp_doxy_nva_2.m
    sensor_2_value_for_phase_delay_doxy_nva_2.m
  DOI: https://doi.org/10.17882/45589
  Author: Jean-Philippe Rannou (Capgemini/Ifremer)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import (
    BaseDecoder,
    CTDMeasurement,
    GPSFix,
    HydraulicAction,
    TechnicalData,
)

try:
    from ...core.bit_utils import get_bits
except ImportError:
    from argofluxdecoder.core.bit_utils import get_bits

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# NOVA/DOVA Packet Types
# ─────────────────────────────────────────────────────────────────────────────
PACK_HOUSEKEEPING = 1
PACK_HYDRAULIC = (2, 3, 4)
PACK_ACK = 5
PACK_CTD_ASCENT = range(10, 30)     # 10-29
PACK_CTD_DESCENT = range(30, 50)    # 30-49
PACK_CTD_DRIFT = range(50, 56)      # 50-55

# Fill value for raw counts (raw 65535 = 0xFFFF)
FILL_VALUE = 65535

# NOVA-specific fill detection (from decode_nva_data_ir_sbd_2001.m)
# A measurement is considered fill if: pres==65306 && temp==0 && sal==55536
NOVA_FILL_PRES = 65306
NOVA_FILL_TEMP = 0
NOVA_FILL_SAL = 55536


# ─────────────────────────────────────────────────────────────────────────────
# Sensor conversions (from MATLAB sensor_2_value_for_*_nva*.m)
# ─────────────────────────────────────────────────────────────────────────────

def _pressure_nva(raw: int) -> float:
    """Convert pressure counts to dbar. Source: sensor_2_value_for_pressure_nva.m"""
    return raw * 0.1 - 10.0


def _temperature_nva(raw: int) -> float:
    """Convert temperature counts to degC. Source: sensor_2_value_for_temperature_nva.m"""
    return raw * 0.001 - 5.0


def _salinity_nva(raw: int) -> float:
    """Convert salinity counts to PSU. Source: sensor_2_value_for_salinity_nva.m"""
    return raw * 0.001 + 10.0


def _temp_doxy_nva(raw: int) -> float:
    """Convert TEMP_DOXY counts to degC. Source: sensor_2_value_for_temp_doxy_nva_2.m"""
    return raw * 0.001 - 5.0


def _phase_delay_doxy_nva(raw: int) -> float:
    """Convert PHASE_DELAY_DOXY counts. Source: sensor_2_value_for_phase_delay_doxy_nva_2.m"""
    return raw * 0.001


# ─────────────────────────────────────────────────────────────────────────────
# Housekeeping bit layouts per decoder ID
# ─────────────────────────────────────────────────────────────────────────────

# IDs 2001, 2002 (NOVA 1.0, DOVA 2.0): first 6 fields are 16-bit
_TECH_LAYOUT_2001_2002 = (
    [16] * 6 +         # indices 1-6: voltages (raw * 0.001)
    [8] * 12 +         # indices 7-18
    [16, 8, 16, 8, 16, 8, 8] +    # indices 19-25
    [16, 16] +         # indices 26-27
    [8] * 10 +         # indices 28-37
    [32, 32] +         # indices 38-39: GPS lat/lon
    [8, 16, 8, 8, 8, 16, 8, 8, 8, 16, 8]  # indices 40-50
)

# ID 2002 has 3 additional bytes at end compared to 2001
_TECH_LAYOUT_2002_EXTRA = [8, 8, 8]

# ID 2003 (NOVA 0.9): first 6 fields are 8-bit (not 16!)
_TECH_LAYOUT_2003 = (
    [8] * 6 +          # indices 1-6: voltages (raw * 0.1)
    [8] * 12 +         # indices 7-18
    [16, 8, 16, 8, 16, 8, 8] +    # indices 19-25
    [16, 16] +         # indices 26-27
    [8] * 9 +          # indices 28-36
    [32, 32] +         # indices 37-38: GPS lat/lon
    [8] * 11           # indices 39-49
)


class NovaSbdDecoder(BaseDecoder):
    """
    Decoder for NOVA/DOVA Iridium SBD floats (IDs 2001-2003).

    NOVA floats use a different frame format than NKE:
    - One SBD message per file (variable length)
    - Byte 0 = packet type
    - Bytes 1-2 = total message length (uint16, big-endian)
    - Bytes 3+ = payload data

    For our decoder interface, the sbd_reader passes:
    - pack_type = byte 0
    - payload = bytes starting after the type byte (i.e. from byte 1 onward)

    Packet types:
      1       = Housekeeping (technical data + GPS)
      2, 3, 4 = Hydraulic actions
      5       = Acknowledgment
      10-29   = CTD ascent profiles
      30-49   = CTD descent profiles
      50-55   = CTD drift data
    """

    def __init__(self, decoder_id: int, float_info: Dict[str, Any]):
        super().__init__(decoder_id, float_info)
        self._has_doxy = (decoder_id == 2002)

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        """
        Decode a single NOVA/DOVA SBD packet.

        Parameters
        ----------
        pack_type : int
            Packet type byte (1, 2-4, 5, 10-55).
        payload : bytes
            Message payload (everything after the type byte).
            For NOVA: starts with 2-byte length field, then data.
        file_name : str
            Source SBD filename.
        file_date : datetime, optional
            Date from SBD filename.
        """
        # Store raw packet for traceability
        self._raw_packets.append({
            "pack_type": pack_type,
            "payload_len": len(payload),
            "file_name": file_name,
        })

        if pack_type == PACK_HOUSEKEEPING:
            self._decode_housekeeping(payload, file_name, file_date)
        elif pack_type in PACK_HYDRAULIC:
            self._decode_hydraulic(pack_type, payload, file_name, file_date)
        elif pack_type == PACK_ACK:
            self._decode_ack(payload, file_name, file_date)
        elif pack_type in PACK_CTD_ASCENT:
            self._decode_ctd(payload, "ascent", file_name, file_date)
        elif pack_type in PACK_CTD_DESCENT:
            self._decode_ctd(payload, "descent", file_name, file_date)
        elif pack_type in PACK_CTD_DRIFT:
            self._decode_ctd(payload, "drift", file_name, file_date)
        else:
            logger.warning(
                "NOVA decoder %d: Unknown packet type %d in %s",
                self.decoder_id, pack_type, file_name
            )

    def get_sensor_conversions(self) -> Dict[str, Any]:
        """Return sensor conversion functions for NOVA/DOVA."""
        conversions = {
            "pressure": _pressure_nva,
            "temperature": _temperature_nva,
            "salinity": _salinity_nva,
        }
        if self._has_doxy:
            conversions["temp_doxy"] = _temp_doxy_nva
            conversions["phase_delay_doxy"] = _phase_delay_doxy_nva
        return conversions

    # ─────────────────────────────────────────────────────────────────────────
    # Private: Housekeeping packet (type 1)
    # ─────────────────────────────────────────────────────────────────────────

    def _decode_housekeeping(self, payload: bytes, file_name: str,
                             file_date: Optional[datetime]) -> None:
        """
        Decode NOVA housekeeping packet.

        Contains: voltages, cycle timing, GPS position, internal counters.
        The bit layout differs between decoder IDs 2001/2002 and 2003.

        Source: decode_nva_data_ir_sbd_2001.m (case 1), decode_nva_data_ir_sbd_2003.m (case 1)
        """
        # NOVA payload starts with 2-byte length; actual data begins at byte 2
        # (length field already consumed by reader; payload is raw data after type byte)
        # In our architecture: payload includes the length bytes, so we skip them
        msg_data = payload[2:]  # Skip 2-byte length field

        if self.decoder_id == 2003:
            layout = _TECH_LAYOUT_2003
        else:
            layout = list(_TECH_LAYOUT_2001_2002)
            if self.decoder_id == 2002:
                layout += _TECH_LAYOUT_2002_EXTRA

        values = get_bits(1, layout, msg_data)
        if len(values) < 30:
            logger.warning("NOVA: Housekeeping packet too short in %s", file_name)
            return

        # ── Extract cycle number ──
        # Index 30 in MATLAB (1-based) → index 29 in Python (0-based)
        cycle_num = values[29]

        cycle = self.get_or_create_cycle(cycle_num)

        # ── GPS coordinates ──
        # IDs 2001/2002: indices 38-39 (1-based) → 37-38 (0-based)
        # ID 2003: indices 37-38 (1-based) → 36-37 (0-based)
        if self.decoder_id == 2003:
            lat_idx, lon_idx = 36, 37
        else:
            lat_idx, lon_idx = 37, 38

        if lat_idx < len(values) and lon_idx < len(values):
            # GPS conversion: raw * 1e-7 - 214.7483648
            latitude = values[lat_idx] * 1e-7 - 214.7483648
            longitude = values[lon_idx] * 1e-7 - 214.7483648

            # Build GPS date from tech fields
            gps_date = self._extract_gps_date(values)

            gps_fix = GPSFix(
                cycle=cycle_num,
                latitude=latitude,
                longitude=longitude,
                date=gps_date,
                valid=True,
            )
            cycle.gps_fixes.append(gps_fix)

        # ── Technical data ──
        tech_dict = self._build_tech_dict(values)
        tech_dict["sbd_file"] = file_name
        cycle.technical.append(TechnicalData(cycle=cycle_num, data=tech_dict))

    def _extract_gps_date(self, values: List[int]) -> Optional[datetime]:
        """
        Extract GPS fix date from housekeeping values.

        For IDs 2001/2002:
          - Hour: index 40 (0-based: 39)
          - Minute: index 41 (0-based: 40)
          - Day: index 42 (0-based: 41)
          - Month: index 43 (0-based: 42)
          - Year: index 48 (0-based: 47), needs +2000

        For ID 2003:
          - Hour: index 39 (0-based: 38)
          - Minute: index 40 (0-based: 39)
          - Day: index 41 (0-based: 40)
          - Month: index 42 (0-based: 41)
          - Year: index 47 (0-based: 46), needs +2000
        """
        try:
            if self.decoder_id == 2003:
                hour = values[38]
                minute = values[39]
                day = values[40]
                month = values[41]
                year = values[46] + 2000
            else:
                hour = values[39]
                minute = values[40]
                day = values[41]
                month = values[42]
                year = values[47] + 2000

            if year < 2000 or month < 1 or month > 12 or day < 1 or day > 31:
                return None
            return datetime(year, month, day, hour, minute, 0)
        except (IndexError, ValueError):
            return None

    def _build_tech_dict(self, values: List[int]) -> Dict[str, Any]:
        """
        Build a technical data dictionary from housekeeping fields.

        Applies physical conversions as per MATLAB source.
        """
        tech = {}

        if self.decoder_id == 2003:
            # ID 2003: first 6 values are voltages * 0.1
            for i in range(6):
                if i < len(values):
                    tech[f"voltage_{i+1}"] = values[i] * 0.1
            # Pressure at parking depth
            if 25 < len(values):
                tech["park_pressure_dbar"] = values[25] * 0.1 - 3276.8
        else:
            # IDs 2001/2002: first 6 values are voltages * 0.001
            for i in range(6):
                if i < len(values):
                    tech[f"voltage_{i+1}_v"] = values[i] * 0.001
            # Pressure at parking depth: index 26 (0-based: 25)
            if 25 < len(values):
                tech["park_pressure_dbar"] = values[25] * 0.1 - 3276.8
            # Internal vacuum: index 31 (0-based: 30)
            if 30 < len(values):
                tech["internal_pressure_dbar"] = values[30] * 0.1

        # Cycle number is already stored externally
        if 29 < len(values):
            tech["cycle_number"] = values[29]

        return tech

    # ─────────────────────────────────────────────────────────────────────────
    # Private: Hydraulic packet (types 2, 3, 4)
    # ─────────────────────────────────────────────────────────────────────────

    def _decode_hydraulic(self, pack_type: int, payload: bytes,
                          file_name: str, file_date: Optional[datetime]) -> None:
        """
        Decode hydraulic packet.

        Structure (from MATLAB):
          - 1 byte: cycle number
          - N actions, each 7 bytes: type(8) + time_offset(16) + pressure(16) + duration(16)
          - nb_actions = floor((payload_data_len - 1) / 7)

        Pressure conversion: raw * 0.1 (to dbar)

        Source: decode_nva_data_ir_sbd_2001.m (case 2/3/4)
        """
        msg_data = payload[2:]  # Skip 2-byte length
        data_len = len(msg_data)

        if data_len < 1:
            return

        # Bit layout: 8-bit cycle num, then N * [8, 16, 16, 16]
        nb_actions = (data_len - 1) // 7

        if nb_actions < 1:
            return

        layout = [8] + [8, 16, 16, 16] * nb_actions
        values = get_bits(1, layout, msg_data)

        if not values:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        for i in range(nb_actions):
            base = 1 + i * 4
            if base + 3 >= len(values):
                break

            action_type_raw = values[base]
            # time_offset = values[base + 1]  # Not used in CSV output
            pressure_raw = values[base + 2]
            duration_raw = values[base + 3]

            # Pressure conversion: raw * 0.1
            pressure_dbar = pressure_raw * 0.1

            # Map action type to string
            action_str = self._hydraulic_action_name(action_type_raw)

            cycle.hydraulics.append(HydraulicAction(
                cycle=cycle_num,
                action_type=action_str,
                date=file_date,
                pressure_dbar=pressure_dbar,
                duration_sec=duration_raw,
            ))

    @staticmethod
    def _hydraulic_action_name(action_type: int) -> str:
        """Convert NOVA hydraulic action type code to readable string."""
        # NOVA action types (from MATLAB tech labels):
        # 0=descent_ev, 1=descent_pump, 2=repositioning, 3=ascent_pump
        names = {
            0: "descent_ev",
            1: "descent_pump",
            2: "repositioning",
            3: "ascent_pump",
            4: "surface_pump",
        }
        return names.get(action_type, f"action_{action_type}")

    # ─────────────────────────────────────────────────────────────────────────
    # Private: Acknowledgment packet (type 5)
    # ─────────────────────────────────────────────────────────────────────────

    def _decode_ack(self, payload: bytes, file_name: str,
                    file_date: Optional[datetime]) -> None:
        """
        Decode acknowledgment packet.

        Structure: N commands, each 5 bytes: [8, 8, 16, 8]
        These are command acknowledgments; we log them but don't add to profiles.

        Source: decode_nva_data_ir_sbd_2001.m (case 5)
        """
        msg_data = payload[2:]  # Skip 2-byte length
        data_len = len(msg_data)
        nb_cmd = data_len // 5

        if nb_cmd < 1:
            return

        layout = [8, 8, 16, 8] * nb_cmd
        values = get_bits(1, layout, msg_data)

        # ACK packets don't carry cycle numbers or profile data.
        # We log them for completeness but don't add to decoded cycles.
        logger.debug(
            "NOVA ACK packet: %d commands in %s", nb_cmd, file_name
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Private: CTD data packets (types 10-55)
    # ─────────────────────────────────────────────────────────────────────────

    def _decode_ctd(self, payload: bytes, direction: str,
                    file_name: str, file_date: Optional[datetime]) -> None:
        """
        Decode CTD (or CTDO for ID 2002) data packet.

        Structure:
          - 1 byte: cycle number (8-bit)
          - 1 byte: time reference in hours * 10 (8-bit)
          - N measurements:
            - For IDs 2001, 2003 (CTD): 6 bytes each = [16, 16, 16] → (S, T, P)
            - For ID 2002 (CTDO): 10 bytes each = [16, 16, 16, 16, 16] → (S, T, P, PhaseDelayDoxy, TempDoxy)

        NOTE: In MATLAB the triplet order is (salinity, temperature, pressure) for data extraction
        indices, but the actual storage indices show:
          tabPres(idM) = tabData(3*(idM-1)+5)   → 3rd of triplet
          tabTemp(idM) = tabData(3*(idM-1)+4)   → 2nd of triplet
          tabPsal(idM) = tabData(3*(idM-1)+3)   → 1st of triplet

        Source: decode_nva_data_ir_sbd_2001.m (case 10-55), decode_nva_data_ir_sbd_2002.m
        """
        msg_data = payload[2:]  # Skip 2-byte length
        data_len = len(msg_data)

        if data_len < 2:
            return

        if self._has_doxy:
            bytes_per_sample = 10  # CTDO: 5 x 16-bit
            fields_per_sample = 5
        else:
            bytes_per_sample = 6   # CTD: 3 x 16-bit
            fields_per_sample = 3

        # Number of samples: (total_data_bytes - 2_header_bytes) / bytes_per_sample
        nb_meas = (data_len - 2) // bytes_per_sample

        if nb_meas < 1:
            return

        # Layout: cycle(8) + time_ref(8) + N * [16] * fields_per_sample
        layout = [8, 8] + [16] * (fields_per_sample * nb_meas)
        values = get_bits(1, layout, msg_data)

        if len(values) < 2:
            return

        cycle_num = values[0]
        # time_ref_hours = values[1] * 0.1  # Hours since cycle start (not used in CSV)

        cycle = self.get_or_create_cycle(cycle_num)

        for i in range(nb_meas):
            base = 2 + i * fields_per_sample

            if base + fields_per_sample - 1 >= len(values):
                break

            if self._has_doxy:
                # DOVA 2002: order is (S, T, P, PhaseDelayDoxy, TempDoxy)
                sal_raw = values[base]
                temp_raw = values[base + 1]
                pres_raw = values[base + 2]
                phase_delay_raw = values[base + 3]
                temp_doxy_raw = values[base + 4]
            else:
                # NOVA 2001/2003: order is (S, T, P)
                sal_raw = values[base]
                temp_raw = values[base + 1]
                pres_raw = values[base + 2]

            # Fill value detection
            if self._is_fill_value(pres_raw, temp_raw, sal_raw):
                continue

            # Convert to physical values
            pressure = _pressure_nva(pres_raw)
            temperature = _temperature_nva(temp_raw)
            salinity = _salinity_nva(sal_raw)

            measurement = CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=pressure,
                temperature_degc=temperature,
                salinity_psu=salinity,
                date=file_date,
                direction=direction,
            )

            if direction == "ascent":
                cycle.ctd_ascent.append(measurement)
            elif direction == "descent":
                cycle.ctd_descent.append(measurement)
            else:
                cycle.ctd_drift.append(measurement)

    def _is_fill_value(self, pres_raw: int, temp_raw: int, sal_raw: int) -> bool:
        """
        Check if a CTD measurement contains fill values.

        NOVA uses two fill patterns:
          1. Standard: any field == 65535 (0xFFFF)
          2. NOVA-specific: pres==65306, temp==0, sal==55536
             (from decode_nva_data_ir_sbd_2001.m line ~227)
        """
        # Standard fill
        if pres_raw == FILL_VALUE or temp_raw == FILL_VALUE or sal_raw == FILL_VALUE:
            return True
        # NOVA-specific fill pattern
        if pres_raw == NOVA_FILL_PRES and temp_raw == NOVA_FILL_TEMP and sal_raw == NOVA_FILL_SAL:
            return True
        return False
