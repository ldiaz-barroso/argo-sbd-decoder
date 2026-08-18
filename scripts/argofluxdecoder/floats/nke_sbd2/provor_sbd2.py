"""
provor_sbd2.py
==============
Decoder for NKE PROVOR SBD2 floats with bio-optical sensors (IDs 301-303).

These floats use 140-byte SBD frames (vs 100 bytes for NKE 2xx).
They carry CTD + dissolved oxygen (optode) + bio-optical sensors:
  - ID 301: PROVOR Remocean with FLBB (chlorophyll + backscatter)
  - ID 302: ARVOR CM with FLNTU (chlorophyll + turbidity)
  - ID 303: ARVOR CM with FLNTU + CYCLOPS + SEAPOINT

Packet types:
  0   = Sensor data (CTD, OXY, FLBB/FLNTU)
  250 = Sensor technical data (70-byte payloads packed 2 per frame)
  251 = Sensor parameter modifications
  252 = Float pressure actions (pump/EV)
  253 = Float technical data (GPS, cycle timing)
  254 = Float programmed technical parameters
  255 = Float programmed sampling parameters

Sensor conversions:
  Pressure:    raw / 10 (dbar)
  Temperature: raw / 1000 - 2 (degC)
  Salinity:    raw / 1000 (PSU)
  C1Phase/C2Phase/DPhase: twos_complement(raw, 32) / 1000 (degrees)
  ChloroA/Backscatter: raw (counts, no conversion at this level)

Reference:
  decode_prv_data_ir_sbd2_301.m, decode_prv_data_ir_sbd2_302_303.m
  sensor_2_value_for_pressure_ir_rudics_sbd2.m
  Coriolis MATLAB decoder v085h (2026-07-10)
  DOI: https://doi.org/10.17882/45589
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
    from ...core.bit_utils import get_bits, twos_complement, decode_gps
except ImportError:
    from argofluxdecoder.core.bit_utils import get_bits, twos_complement, decode_gps

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
FRAME_SIZE_SBD2 = 140

# Packet types
PACK_SENSOR_DATA = 0
PACK_SENSOR_TECH = 250
PACK_SENSOR_PARAM = 251
PACK_FLOAT_PRESSURE = 252
PACK_FLOAT_TECH = 253
PACK_FLOAT_PROG_TECH = 254
PACK_FLOAT_PROG_PARAM = 255

# Sensor data sub-types
SENSOR_CTD_MEAN = 0
SENSOR_CTD_STDMED = 1
SENSOR_OXY_MEAN = 3
SENSOR_OXY_STDMED = 4
SENSOR_FLBB_MEAN = 6
SENSOR_FLBB_STDMED = 7
SENSOR_FLNTU_MEAN = 15
SENSOR_FLNTU_STDMED = 16

# Phase codes for direction mapping
PHASE_DESC_TO_PARK = 4
PHASE_PARK_DRIFT = 6
PHASE_DESC_TO_PROF = 8
PHASE_PROF_DRIFT = 9
PHASE_ASC_PROF = 10
PHASE_SAT_TRANS = 12


# ─────────────────────────────────────────────────────────────────────────────
# Sensor Conversions
# ─────────────────────────────────────────────────────────────────────────────

def _pressure_sbd2(raw: int) -> float:
    """Pressure: raw / 10. Source: sensor_2_value_for_pressure_ir_rudics_sbd2.m"""
    return raw / 10.0


def _temperature_sbd2(raw: int) -> float:
    """Temperature: raw / 1000 - 2. Handles range -2 to +40 degC."""
    return raw / 1000.0 - 2.0


def _salinity_sbd2(raw: int) -> float:
    """Salinity: raw / 1000."""
    return raw / 1000.0


def _phase_sbd2(raw: int) -> float:
    """C1Phase/C2Phase/DPhase: signed 32-bit / 1000 (degrees)."""
    return twos_complement(raw, 32) / 1000.0


def _phase_to_direction(phase_num: int) -> str:
    """Map SBD2 phase number to profile direction."""
    if phase_num in (PHASE_DESC_TO_PARK, PHASE_DESC_TO_PROF):
        return "descent"
    elif phase_num in (PHASE_PARK_DRIFT, PHASE_PROF_DRIFT):
        return "drift"
    elif phase_num == PHASE_ASC_PROF:
        return "ascent"
    return "ascent"  # Default


class ProvorSbd2Decoder(BaseDecoder):
    """
    Decoder for NKE PROVOR SBD2 Iridium floats (IDs 301-303).

    Uses 140-byte SBD frames. The first byte of each frame is the packet type.
    For sensor data packets (type 0), byte 1 is the sensor data sub-type.
    """

    def __init__(self, decoder_id: int, float_info: Dict[str, Any]):
        super().__init__(decoder_id, float_info)

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        """
        Decode a single 139-byte SBD2 payload (140-byte frame minus type byte).

        Parameters
        ----------
        pack_type : int
            Packet type (first byte of the 140-byte frame).
        payload : bytes
            139-byte payload (frame bytes 1-139).
        file_name : str
            Source SBD filename.
        file_date : datetime, optional
            Date from SBD filename.
        """
        self._raw_packets.append({
            "pack_type": pack_type,
            "file_name": file_name,
        })

        if pack_type == PACK_SENSOR_DATA:
            self._decode_sensor_data(payload, file_name, file_date)
        elif pack_type == PACK_FLOAT_TECH:
            self._decode_tech(payload, file_name, file_date)
        elif pack_type == PACK_FLOAT_PRESSURE:
            self._decode_pressure_actions(payload, file_name, file_date)
        elif pack_type == PACK_SENSOR_TECH:
            # Sensor tech: 70+70 bytes packed. We store for metadata but
            # don't extract CTD profiles from it.
            pass
        elif pack_type in (PACK_SENSOR_PARAM, PACK_FLOAT_PROG_TECH, PACK_FLOAT_PROG_PARAM):
            # Configuration packets — not needed for profile extraction
            pass
        else:
            logger.debug("SBD2: Unhandled packet type %d in %s", pack_type, file_name)

    def get_sensor_conversions(self) -> Dict[str, Any]:
        """Return sensor conversion functions for SBD2."""
        return {
            "pressure": _pressure_sbd2,
            "temperature": _temperature_sbd2,
            "salinity": _salinity_sbd2,
            "phase": _phase_sbd2,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Sensor Data (type 0)
    # ─────────────────────────────────────────────────────────────────────────

    def _decode_sensor_data(self, payload: bytes, file_name: str,
                            file_date: Optional[datetime]) -> None:
        """Decode sensor data packet (type 0). Byte 0 of payload = sensorDataType."""
        if len(payload) < 2:
            return

        sensor_data_type = payload[0]
        msg_data = payload[1:]  # Data after the sensor sub-type byte

        if sensor_data_type == SENSOR_CTD_MEAN:
            self._decode_ctd_mean(msg_data, file_name, file_date)
        elif sensor_data_type == SENSOR_OXY_MEAN:
            self._decode_oxy_mean(msg_data, file_name, file_date)
        elif sensor_data_type in (SENSOR_FLBB_MEAN, SENSOR_FLNTU_MEAN):
            self._decode_flbb_mean(msg_data, file_name, file_date)
        # StDev/Median types (1, 4, 7, 16) are statistical summaries —
        # we decode only mean profiles for our visualization tool
        else:
            logger.debug("SBD2: Sensor data type %d not decoded (file: %s)",
                         sensor_data_type, file_name)

    def _decode_ctd_mean(self, msg_data: bytes, file_name: str,
                         file_date: Optional[datetime]) -> None:
        """
        Decode CTD mean data (sensorDataType=0).

        Layout: [cycleNum(16) profNum(8) phaseNum(8) epoch(32)]
                + 21 × [P(16) T(16) S(16)] + padding(32)
        Total: 64 + 21*48 + 32 = 1104 bits = 138 bytes
        """
        layout = [16, 8, 8, 32] + [16, 16, 16] * 21 + [32]
        values = get_bits(1, layout, msg_data)

        if len(values) < 4 + 63:
            return

        cycle_num = values[0]
        # prof_num = values[1]
        phase_num = values[2]
        # epoch_val = values[3]

        direction = _phase_to_direction(phase_num)
        cycle = self.get_or_create_cycle(cycle_num)

        for i in range(21):
            base = 4 + i * 3
            pres_raw = values[base]
            temp_raw = values[base + 1]
            sal_raw = values[base + 2]

            # Skip empty bins
            if pres_raw == 0 and temp_raw == 0 and sal_raw == 0:
                continue

            pressure = _pressure_sbd2(pres_raw)
            temperature = _temperature_sbd2(temp_raw)
            salinity = _salinity_sbd2(sal_raw)

            meas = CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=pressure,
                temperature_degc=temperature,
                salinity_psu=salinity,
                date=file_date,
                direction=direction,
            )

            if direction == "ascent":
                cycle.ctd_ascent.append(meas)
            elif direction == "descent":
                cycle.ctd_descent.append(meas)
            else:
                cycle.ctd_drift.append(meas)

    def _decode_oxy_mean(self, msg_data: bytes, file_name: str,
                         file_date: Optional[datetime]) -> None:
        """
        Decode oxygen mean data (sensorDataType=3).

        For ID 301: [P(16) C1Phase(32) C2Phase(32) Temp(16)] × 10
        For IDs 302/303: [P(16) DPhase(32) unused(32) Temp(16)] × 10
        Both use same layout: header(64) + 10×[16+32+32+16] + pad(80) = 1104 bits
        """
        layout = [16, 8, 8, 32] + [16, 32, 32, 16] * 10 + [80]
        values = get_bits(1, layout, msg_data)

        if len(values) < 4 + 40:
            return

        cycle_num = values[0]
        phase_num = values[2]
        direction = _phase_to_direction(phase_num)
        cycle = self.get_or_create_cycle(cycle_num)

        for i in range(10):
            base = 4 + i * 4
            pres_raw = values[base]
            phase1_raw = values[base + 1]
            phase2_raw = values[base + 2]
            temp_raw = values[base + 3]

            if pres_raw == 0 and phase1_raw == 0 and phase2_raw == 0 and temp_raw == 0:
                continue

            # Store as technical data (oxygen phases need calibration coefficients
            # to convert to DOXY — beyond our scope for visualization)
            cycle.technical.append(TechnicalData(
                cycle=cycle_num,
                data={
                    "sensor": "optode",
                    "pressure_dbar": _pressure_sbd2(pres_raw),
                    "phase1_deg": _phase_sbd2(phase1_raw),
                    "phase2_deg": _phase_sbd2(phase2_raw),
                    "temp_doxy_degc": _temperature_sbd2(temp_raw),
                    "direction": direction,
                }
            ))

    def _decode_flbb_mean(self, msg_data: bytes, file_name: str,
                          file_date: Optional[datetime]) -> None:
        """
        Decode FLBB/FLNTU mean data (sensorDataType=6 or 15).

        Layout: header(64) + 21 × [P(16) ChloroA(16) Backscat/Turbi(16)] + pad(32)
        Same structure for both FLBB (301) and FLNTU (302/303).
        """
        layout = [16, 8, 8, 32] + [16, 16, 16] * 21 + [32]
        values = get_bits(1, layout, msg_data)

        if len(values) < 4 + 63:
            return

        cycle_num = values[0]
        phase_num = values[2]
        direction = _phase_to_direction(phase_num)
        cycle = self.get_or_create_cycle(cycle_num)

        for i in range(21):
            base = 4 + i * 3
            pres_raw = values[base]
            chloro_raw = values[base + 1]
            backscat_raw = values[base + 2]

            if pres_raw == 0 and chloro_raw == 0 and backscat_raw == 0:
                continue

            # Bio-optical data stored as technical (needs calibration for physical units)
            cycle.technical.append(TechnicalData(
                cycle=cycle_num,
                data={
                    "sensor": "flbb" if self.decoder_id == 301 else "flntu",
                    "pressure_dbar": _pressure_sbd2(pres_raw),
                    "chloro_counts": chloro_raw,
                    "backscat_counts": backscat_raw,
                    "direction": direction,
                }
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # Technical Data (type 253)
    # ─────────────────────────────────────────────────────────────────────────

    def _decode_tech(self, payload: bytes, file_name: str,
                     file_date: Optional[datetime]) -> None:
        """
        Decode float technical packet (type 253).

        Contains GPS position, cycle number, internal pressure, and timing info.
        GPS is encoded as degrees + minutes + fractional minutes (NKE standard).
        """
        msg_data = payload

        # Tech packet layout (simplified — extract key fields)
        # Bytes 0-5: date (dd mm yy HH MM SS, each 8 bits)
        # Bytes 6-7: spare/cycle context
        # ... complex layout with GPS at indices 67-74 (1-based in MATLAB)
        tech_layout = (
            [8] * 6 +       # Date: dd, mm, yy, HH, MM, SS (indices 1-6)
            [16] +           # Index 7: spare
            [16, 16, 8] * 3 +  # Indices 8-16
            [8, 8, 16, 16, 8, 8] +  # Indices 17-22
            [16] * 6 +       # Indices 23-28
            [8] * 10 +       # Indices 29-38
            [16] * 4 +       # Indices 39-42
            [8] * 9 +        # Indices 43-51
            [16] * 4 +       # Indices 52-55
            [8, 8, 8, 16, 16, 8, 16] +  # Indices 56-62
            [8] * 7 +        # Indices 63-69
            [16, 8, 8, 8] * 2 +  # Indices 70-77
            [8, 32]          # Indices 78-79 + padding
        )

        values = get_bits(1, tech_layout, msg_data)

        if len(values) < 75:
            return

        # Extract date
        try:
            day, month, year = values[0], values[1], values[2]
            hour, minute, second = values[3], values[4], values[5]
            if year > 0 and month > 0 and day > 0:
                pack_date = datetime(2000 + year, month, day, hour, minute, second)
            else:
                pack_date = file_date
        except (ValueError, IndexError):
            pack_date = file_date

        # Cycle number at index 8 (0-based: 7) in the values after date
        # In MATLAB: tabTech(9) after prepending packJulD → values[7] is cycle
        cycle_num = values[7] if len(values) > 7 else 0
        if cycle_num == 0 or cycle_num > 500:
            # Fallback — try other field positions
            cycle_num = values[6] if len(values) > 6 and 0 < values[6] < 500 else 1

        cycle = self.get_or_create_cycle(cycle_num)

        # GPS coordinates (indices 68-74 in MATLAB 1-based → 67-73 in 0-based)
        # The exact position depends on the full layout; for now extract from
        # the known MATLAB positions relative to the tech_layout above
        # In MATLAB: tabTech(68) = lat_deg, (69) = lat_min, (70) = lat_frac, (71) = lat_sign
        #            tabTech(72) = lon_deg, (73) = lon_min, (74) = lon_frac, (75) = lon_sign
        # These correspond to indices in our values array around position 67+
        try:
            # GPS is typically near the end of the tech packet
            # Using the field count: 6+1+9+6+6+10+4+9+4+7+7+8+2 = 79 values
            # GPS lat at MATLAB indices 68-71, lon at 72-75
            # In 0-based: 67, 68, 69, 70 (lat), 71, 72, 73, 74 (lon)
            if len(values) >= 75:
                lat_deg = values[67]
                lat_min = values[68]
                lat_frac = values[69]
                lat_sign = values[70]
                lon_deg = values[71]
                lon_min = values[72]
                lon_frac = values[73]
                lon_sign = values[74]

                latitude = decode_gps(lat_deg, lat_min, lat_frac, lat_sign)
                longitude = decode_gps(lon_deg, lon_min, lon_frac, lon_sign)

                # Sanity check
                if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    gps_fix = GPSFix(
                        cycle=cycle_num,
                        latitude=latitude,
                        longitude=longitude,
                        date=pack_date,
                        valid=True,
                    )
                    cycle.gps_fixes.append(gps_fix)
        except (IndexError, ValueError):
            pass

        # Internal pressure (index 11 in MATLAB × 5 for mbar)
        if len(values) > 10:
            internal_pressure_mbar = values[10] * 5
            tech_dict = {
                "internal_pressure_mbar": internal_pressure_mbar,
                "sbd_file": file_name,
            }
            cycle.technical.append(TechnicalData(cycle=cycle_num, data=tech_dict))

    # ─────────────────────────────────────────────────────────────────────────
    # Float Pressure Actions (type 252)
    # ─────────────────────────────────────────────────────────────────────────

    def _decode_pressure_actions(self, payload: bytes, file_name: str,
                                 file_date: Optional[datetime]) -> None:
        """
        Decode float pressure actions (pump/EV) packet (type 252).

        Layout: cycleNum(16) + 27 × [profNum(4) phaseNum(4) pumpOrEv(8) actPres(8) time(16)]
                + padding(16)
        """
        layout = [16] + [4, 4, 8, 8, 16] * 27 + [16]
        values = get_bits(1, layout, payload)

        if len(values) < 2:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        for i in range(27):
            base = 1 + i * 5
            if base + 4 >= len(values):
                break

            prof_num = values[base]
            phase_num = values[base + 1]
            pump_or_ev = values[base + 2]
            act_pres = values[base + 3]
            time_val = values[base + 4]

            # Skip empty entries
            if prof_num == 0 and phase_num == 0 and pump_or_ev == 0 and act_pres == 0 and time_val == 0:
                continue

            action_type = "pump" if pump_or_ev == 1 else "ev"
            cycle.hydraulics.append(HydraulicAction(
                cycle=cycle_num,
                action_type=action_type,
                pressure_dbar=act_pres * 10.0,  # actPres in bars → dbar
                duration_sec=time_val,
                date=file_date,
            ))
