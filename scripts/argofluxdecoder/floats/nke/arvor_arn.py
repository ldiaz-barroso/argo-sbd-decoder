"""
arvor_arn.py
============
Decoder for NKE ARVOR-ARN family (Iridium SBD).

Covers decoder IDs: 212, 214, 217, 222, 223, 225, 231, 232

Packet types:
  0 = Technical message 1 (cycle info, GPS, timestamps, voltages)
  1 = CTD descending profile (15 PTS triplets per packet)
  2 = CTD drift measurements
  3 = CTD ascending profile
  4 = Technical message 2 (hydraulics summary, groundings)
  5 = Parameter packet 1 (mission config)
  6 = Hydraulic actions (EV/pump events)
  7 = Parameter packet 2 (calibration)

Reference:
  decode_prv_data_ir_sbd_212.m (Coriolis MATLAB decoder)
  DOI: https://doi.org/10.17882/45589
"""

import math
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from ..base import (
    BaseDecoder, CTDMeasurement, TechnicalData,
    HydraulicAction, GPSFix, DecodedCycle
)
from ...core.bit_utils import get_bits, twos_complement, decode_gps
from ...core.time_utils import nke_date_to_datetime
from .common import (
    get_conversions_for_decoder,
    PRES_COUNTS_DEF, TEMP_COUNTS_DEF, SAL_COUNTS_DEF,
    PACK_TECH1, PACK_CTD_DESC, PACK_CTD_DRIFT, PACK_CTD_ASC,
    PACK_TECH2, PACK_PARAM1, PACK_HYDRAULIC, PACK_PARAM2,
    PACK_CTD_DESC2, PACK_CTD_ASC2,
)


class ArvorArnDecoder(BaseDecoder):
    """
    Decoder for ARVOR-ARN / ARVOR-ARN-DO / ARVOR-ARN-ICE families.

    Decoder IDs: 212, 214, 217, 222, 223, 225, 231, 232.
    """

    SUPPORTED_IDS = [212, 214, 217, 222, 223, 225, 231, 232]

    def get_sensor_conversions(self) -> Dict[str, Any]:
        return get_conversions_for_decoder(self.decoder_id)

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        """Decode a single packet and accumulate data."""

        if pack_type == PACK_TECH1:
            self._decode_tech1(payload, file_name, file_date)
        elif pack_type in (PACK_CTD_DESC, PACK_CTD_DRIFT, PACK_CTD_ASC, PACK_CTD_DESC2, PACK_CTD_ASC2):
            self._decode_ctd(pack_type, payload, file_name, file_date)
        elif pack_type == PACK_TECH2:
            self._decode_tech2(payload, file_name, file_date)
        elif pack_type == PACK_PARAM1:
            self._decode_param1(payload, file_name, file_date)
        elif pack_type == PACK_HYDRAULIC:
            self._decode_hydraulic(payload, file_name, file_date)
        elif pack_type == PACK_PARAM2:
            self._decode_param2(payload, file_name, file_date)

    # ─────────────────────────────────────────────────────────────────────────
    # Technical packet 1 (type 0)
    # From decode_prv_data_ir_sbd_212.m, case 0
    # ─────────────────────────────────────────────────────────────────────────
    def _decode_tech1(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """
        Decode technical packet 1.

        Bit layout (from MATLAB):
          16 8 16 16  8 8 8 16 16 16 8 8  16 16 16 8 8 16 16
          8 8 8 16 16 8 8  16 16 8 8 16  8 8 8 8 16 16  16 16 8
          repmat(8,1,12)  8 8 16 8 8 8 16 8 8 16 8 16 8
          repmat(8,1,7)  repmat(8,1,3)
        """
        tab_nb_bits = [
            16, 8, 16, 16,
            8, 8, 8, 16, 16, 16, 8, 8,
            16, 16, 16, 8, 8, 16, 16,
            8, 8, 8, 16, 16, 8, 8,
            16, 16, 8, 8, 16,
            8, 8, 8, 8, 16, 16,
            16, 16, 8,
        ] + [8] * 12 + [
            8, 8, 16, 8, 8, 8, 16, 8, 8, 16, 8, 16, 8,
        ] + [8] * 7 + [8] * 3

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 72:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        # Float time: HHMMSS DDMMYY (values[40:46], 0-indexed from tab)
        try:
            float_time = nke_date_to_datetime(
                day=values[43], month=values[44], year=values[45],
                hour=values[40], minute=values[41], second=values[42]
            )
        except (ValueError, IndexError):
            float_time = file_date

        # Pressure sensor offset (signed 8-bit at index 46)
        pres_offset = twos_complement(values[46], 8) / 10.0

        # GPS location (indices 52-59)
        # Lat: deg=52, min=53, min_frac=54, sign=55
        # Lon: deg=56, min=57, min_frac=58, sign=59
        gps_valid = True
        try:
            lat = decode_gps(values[52], values[53], values[54], values[55])
            lon = decode_gps(values[56], values[57], values[58], values[59])
            # Check if all GPS fields are zero → invalid
            if values[52] == 0 and values[53] == 0 and values[54] == 0 and values[56] == 0 and values[57] == 0:
                gps_valid = False
        except (IndexError, ZeroDivisionError):
            lat, lon = 0.0, 0.0
            gps_valid = False

        if gps_valid:
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num,
                latitude=lat,
                longitude=lon,
                date=float_time,
                valid=True,
            ))

        # Store technical data
        tech = TechnicalData(cycle=cycle_num, data={
            "iridium_session": values[1],
            "firmware_version": values[2],
            "cycle_start_day": values[4],
            "cycle_start_month": values[5],
            "cycle_start_year": values[6],
            "cycle_start_float_day": values[7],
            "descent_start_float_day": values[8],
            "descent_start_time": values[9],
            "descent_end_float_day": values[10],
            "descent_end_time": values[11],
            "park_start_pressure": values[12],
            "park_end_pressure": values[13],
            "deep_profile_pressure": values[14],
            "pressure_offset": pres_offset,
            "internal_pressure_mbar": values[47] * 5 if len(values) > 47 and values[47] != 0 else None,
            "battery_voltage_start": values[17],
            "battery_voltage_end": values[18],
            "float_time": float_time,
            "gps_lat": lat if gps_valid else None,
            "gps_lon": lon if gps_valid else None,
            "gps_valid": gps_valid,
            "sbd_file": file_name,
        })
        cycle.technical.append(tech)

    # ─────────────────────────────────────────────────────────────────────────
    # CTD packets (types 1, 2, 3, 13, 14)
    # From decode_prv_data_ir_sbd_212.m, case {1, 2, 3, 13, 14}
    # ─────────────────────────────────────────────────────────────────────────
    def _decode_ctd(self, pack_type: int, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """
        Decode CTD data packet.

        Layout: 16 16 8 8 + 45×16 + 3×8 = 792 bits
        - values[0]: cycle number
        - values[1]: time (hours since ref) → date in days/24
        - values[2]: time (minutes)
        - values[3]: time (seconds)
        - values[4..48]: 15 triplets of (P, T, S) each 16-bit
        """
        tab_nb_bits = [16, 16, 8, 8] + [16] * 45 + [8] * 3

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 49:
            return

        cycle_num = values[0]

        if not any(v != 0 for v in values[1:]):
            return  # Empty packet

        cycle = self.get_or_create_cycle(cycle_num)

        # Determine direction
        if pack_type in (PACK_CTD_DESC, PACK_CTD_DESC2):
            direction = "descent"
        elif pack_type == PACK_CTD_DRIFT:
            direction = "drift"
        else:
            direction = "ascent"

        # First measurement timestamp
        meas_time_hours = values[1] / 24.0 + values[2] / 1440.0 + values[3] / 86400.0

        # Extract 15 PTS triplets (indices 4 onwards, grouped by 3)
        conversions = get_conversions_for_decoder(self.decoder_id)
        convert_pressure = conversions["pressure"]
        convert_temperature = conversions["temperature"]
        convert_salinity = conversions["salinity"]

        for i in range(15):
            p_raw = values[4 + i * 3]
            t_raw = values[5 + i * 3]
            s_raw = values[6 + i * 3]

            # Skip zero triplets
            if p_raw == 0 and t_raw == 0 and s_raw == 0:
                continue

            pressure = convert_pressure(p_raw)
            temperature = convert_temperature(t_raw)
            salinity = convert_salinity(s_raw)

            meas = CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=pressure,
                temperature_degc=temperature,
                salinity_psu=salinity,
                direction=direction,
            )

            if direction == "ascent":
                cycle.ctd_ascent.append(meas)
            elif direction == "descent":
                cycle.ctd_descent.append(meas)
            else:
                cycle.ctd_drift.append(meas)

    # ─────────────────────────────────────────────────────────────────────────
    # Technical packet 2 (type 4)
    # ─────────────────────────────────────────────────────────────────────────
    def _decode_tech2(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode tech packet 2 — hydraulic summary, grounding info."""
        tab_nb_bits = [
            16, 8,
            8, 8, 8, 8, 8, 16, 16, 8, 16, 16, 8, 8,
        ] + [16] * 6 + [
            8, 16, 8, 16, 8, 8, 16, 8, 16, 8, 8,
            8, 16, 16, 8, 8,
        ] + [8] * 4 + [
            16, 8, 16,
        ] + [8] * 9 + [16] + [8] * 6 + [8] * 20

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 10:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        tech = TechnicalData(cycle=cycle_num, data={
            "packet_type": "tech2",
            "iridium_session": values[1],
            "nb_desc_meas": values[2],
            "nb_drift_meas": values[3],
            "nb_asc_meas": values[4],
            "sbd_file": file_name,
        })
        cycle.technical.append(tech)

    # ─────────────────────────────────────────────────────────────────────────
    # Hydraulic actions (type 6)
    # From decode_prv_data_ir_sbd_212.m, case 6
    # ─────────────────────────────────────────────────────────────────────────
    def _decode_hydraulic(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """
        Decode hydraulic packet: 13 EV/pump actions.

        Layout: 16 16 16 + 13×(8 16 16 16) + 2×8
        - values[0]: cycle number
        - values[1]: reference date (float days)
        - values[2]: reference time (minutes in day)
        - then 13 actions: type(8), time_offset(16), pressure(16), duration(16)
        """
        tab_nb_bits = [16, 16, 16] + [8, 16, 16, 16] * 13 + [8, 8]

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 55:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        ref_date_days = values[1]
        ref_time_min = values[2]

        for i in range(13):
            base = 3 + i * 4
            action_type_raw = values[base]
            time_offset = values[base + 1]
            pressure_raw = values[base + 2]
            duration = values[base + 3]

            if time_offset == 0 and pressure_raw == 0 and duration == 0:
                continue

            pressure = twos_complement(pressure_raw, 16) / 10.0
            action_type = "pump" if action_type_raw else "ev"

            action = HydraulicAction(
                cycle=cycle_num,
                action_type=action_type,
                pressure_dbar=pressure,
                duration_sec=duration,
            )
            cycle.hydraulics.append(action)

    # ─────────────────────────────────────────────────────────────────────────
    # Parameter packets (types 5 and 7) — store raw for now
    # ─────────────────────────────────────────────────────────────────────────
    def _decode_param1(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode parameter packet 1 (mission configuration)."""
        tab_nb_bits = [
            16,
        ] + [8] * 7 + [16] + [16] * 4 + [8] * 7 + [16] * 4 + [
            8, 16, 16, 16, 8, 8, 8, 16, 16,
        ] + [8] * 6 + [16, 16] + [
            16,
        ] + [8] * 5 + [16] + [8] * 5 + [16, 8, 16] + [8] * 9 + [16, 16] + [8] * 9

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 2:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        tech = TechnicalData(cycle=cycle_num, data={
            "packet_type": "param1",
            "raw_values": values,
            "sbd_file": file_name,
        })
        cycle.technical.append(tech)

    def _decode_param2(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode parameter packet 2 (calibration)."""
        tab_nb_bits = [
            16,
        ] + [8] * 7 + [16, 16, 16, 8, 8, 8, 16, 16, 8, 8, 16] + [8] * 5 + [16] + [8] * 66

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 2:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        tech = TechnicalData(cycle=cycle_num, data={
            "packet_type": "param2",
            "raw_values": values,
            "sbd_file": file_name,
        })
        cycle.technical.append(tech)
