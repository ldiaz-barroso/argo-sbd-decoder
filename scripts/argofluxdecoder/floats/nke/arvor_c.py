"""
arvor_c.py
==========
Decoder for NKE ARVOR-C family (Iridium SBD).

Decoder IDs: 219, 220, 233

The ARVOR-C transmits SBD frames grouped by transmission session:
  - Each session corresponds to one float cycle
  - A session starts with a tech packet (type 0) followed by N CTD packets (type 1)
  - The tech packet contains: GPS, expected number of CTD packets, deep_cycle flag
  - CTD packets belong to the SAME cycle as the preceding tech packet

Cycle assignment logic (mirrors Coriolis decode_provor_iridium_sbd.m):
  - Tech packet (type 0) opens a new cycle
  - CTD packets (type 1) are assigned to the current open cycle
  - deep_cycle=0 means the float did not dive → CTD data is discarded

Packet types:
  0 = Technical packet (GPS, timestamps, pressures, battery, deep_cycle flag)
  1 = CTD ascending profile (1 PTS + 23 TS with derived pressure)

Reference:
  decode_prv_data_ir_sbd_219_220.m (Coriolis MATLAB decoder)
  DOI: https://doi.org/10.17882/45589
"""

from typing import Dict, Any, Optional
from datetime import datetime

from ..base import (
    BaseDecoder, CTDMeasurement, TechnicalData, GPSFix,
)
from ...core.bit_utils import get_bits, twos_complement, decode_gps
from .common import get_conversions_for_decoder


class ArvorCDecoder(BaseDecoder):
    """
    Decoder for ARVOR-C (IDs 219, 220, 233).

    Cycle assignment: each tech packet (type 0) opens a new cycle.
    CTD packets (type 1) are assigned to the most recent cycle opened by a tech packet.
    If deep_cycle=False, CTD data is discarded (float didn't profile).

    The number of CTD packets accepted per cycle is limited by exp_nb_asc
    (expected number of ascent messages) from the tech packet. Any extra CTD
    packets beyond that limit are orphans (retransmissions or lost cycles)
    and are discarded.
    """

    def __init__(self, decoder_id: int, float_info: Dict[str, Any]):
        super().__init__(decoder_id, float_info)
        self._cycle_counter = 0
        self._current_deep_cycle = False
        self._current_exp_nb_asc = 0
        self._ctd_packets_received = 0  # Count CTD packets for current cycle

    def get_sensor_conversions(self) -> Dict[str, Any]:
        return get_conversions_for_decoder(self.decoder_id)

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        if pack_type == 0:
            self._decode_tech(payload, file_name, file_date)
        elif pack_type == 1:
            self._decode_ctd_asc(payload, file_name, file_date)

    def _decode_tech(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """
        Decode technical packet (type 0). Opens a new cycle.

        Bit layout (from decode_prv_data_ir_sbd_219_220.m):
          6×16 + 16 16 16 8 8 8 + 10×8 + 8 8 16 8 8 8 16 8 + 8 8 + 448(padding)
        """
        tab_nb_bits = (
            [16] * 6 +              # indices 1-6
            [16, 16, 16, 8, 8, 8] + # indices 7-12 (includes hour/min/sec at 13-15 in MATLAB = 10-12 here 0-indexed... let me use MATLAB 1-based)
            [8] * 10 +              # indices 13-22
            [8, 8, 16, 8, 8, 8, 16, 8] + # indices 23-30
            [8, 8] +                # indices 31-32
            [448]                   # padding
        )

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 32:
            return

        # ── New cycle ──
        self._cycle_counter += 1
        self._ctd_packets_received = 0  # Reset CTD packet counter for new cycle
        cycle_num = self._cycle_counter
        cycle = self.get_or_create_cycle(cycle_num)

        # ── Deep cycle detection (MATLAB: ~any(tabTech([1:5 22]) ~= 0)) ──
        # indices 1-5 (0-based: 0-4) and index 22 (0-based: 21)
        self._current_deep_cycle = any(values[i] != 0 for i in range(5)) or values[21] != 0
        self._current_exp_nb_asc = values[21]  # Expected number of CTD ascent packets

        # ── Float time: MATLAB tabTech(13)=hour, tabTech(14)=minute, tabTech(15)=second
        # In 0-based indexing: values[12], values[13], values[14]
        hour = values[12]
        minute = values[13]
        second = values[14]

        # ── Pressure sensor offset (signed 8-bit) at MATLAB index 16 = our index 15 ──
        # Actually MATLAB tabTech(16) is the 16th value. Let me count our layout:
        # [0-5]=first 6, [6-8]=next 3 of 16-bit, [9-11]=next 3 of 8-bit, [12-21]=10×8
        # So MATLAB's tabTech(16) = our values[15] (within the 10×8 block starting at 12)
        pres_offset = twos_complement(values[15], 8) if values[15] != 0 else 0

        # ── GPS: MATLAB tabTech(23-30) = our values[22-29] ──
        gps_valid = True
        try:
            lat = decode_gps(values[22], values[23], values[24], values[25])
            lon = decode_gps(values[26], values[27], values[28], values[29])
            if values[22] == 0 and values[23] == 0 and values[24] == 0 and values[26] == 0 and values[27] == 0:
                gps_valid = False
        except (IndexError, ZeroDivisionError, ValueError):
            lat, lon = 0.0, 0.0
            gps_valid = False

        # ── Compute float time from SBD file date + hour/min/sec ──
        float_time = file_date
        if file_date and (hour < 24 and minute < 60 and second < 60):
            try:
                float_time = file_date.replace(hour=hour, minute=minute, second=second)
            except (ValueError, AttributeError):
                float_time = file_date

        # ── Store GPS ──
        if gps_valid:
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num, latitude=lat, longitude=lon,
                date=float_time, valid=True,
            ))

        # ── Store technical data ──
        cycle.technical.append(TechnicalData(cycle=cycle_num, data={
            "hour": hour, "minute": minute, "second": second,
            "float_time": float_time,
            "pressure_offset": pres_offset,
            "deep_cycle": self._current_deep_cycle,
            "exp_nb_asc": self._current_exp_nb_asc,
            "gps_lat": lat if gps_valid else None,
            "gps_lon": lon if gps_valid else None,
            "gps_valid": gps_valid,
            "sbd_file": file_name,
        }))

    def _decode_ctd_asc(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """
        Decode CTD ascending profile packet (type 1).
        Assigned to the current cycle (most recent tech packet).

        Layout: 49×16 + 8 = 792 bits
        - First triplet: P(16), T(16), S(16)
        - Remaining 23 pairs: T(16), S(16) — pressure decreases by 10 cbar per sample
        """
        # ── Discard if no deep cycle or no expected CTD packets ──
        if not self._current_deep_cycle:
            return
        if self._current_exp_nb_asc == 0:
            return

        # ── Discard if we already received the expected number of CTD packets ──
        if self._ctd_packets_received >= self._current_exp_nb_asc:
            return

        tab_nb_bits = [16] * 49 + [8]
        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 49:
            return

        # ── Skip empty frames (all zeros after first value) ──
        if not any(v != 0 for v in values[1:49]):
            return

        # ── Detect constant data (artifact: all T identical = stale data) ──
        t_values = [values[1]] + [values[3 + i * 2] for i in range(23)]
        t_nonzero = [v for v in t_values if v != 0]
        if len(t_nonzero) > 5 and len(set(t_nonzero)) == 1:
            return

        # ── Get current cycle ──
        if self._cycle_counter == 0:
            return  # No tech packet received yet
        cycle_num = self._cycle_counter
        cycle = self.get_or_create_cycle(cycle_num)

        # ── Count this CTD packet ──
        self._ctd_packets_received += 1

        # ── Conversions ──
        conversions = get_conversions_for_decoder(self.decoder_id)
        convert_pressure = conversions["pressure"]
        convert_temperature = conversions["temperature"]
        convert_salinity = conversions["salinity"]

        # ── First measurement: explicit P, T, S ──
        p_raw = values[0]
        t_raw = values[1]
        s_raw = values[2]

        if not (p_raw == 0 and t_raw == 0 and s_raw == 0):
            cycle.ctd_ascent.append(CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=convert_pressure(p_raw),
                temperature_degc=convert_temperature(t_raw),
                salinity_psu=convert_salinity(s_raw),
                date=file_date,
                direction="ascent",
            ))

        # ── Remaining 23 measurements: T, S only; P decreases by 10 cbar each ──
        for i in range(23):
            t_raw = values[3 + i * 2]
            s_raw = values[4 + i * 2]

            if t_raw == 0 and s_raw == 0:
                continue

            derived_pressure = (p_raw - (i + 1) * 10) / 10.0  # cbar to dbar

            cycle.ctd_ascent.append(CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=derived_pressure,
                temperature_degc=convert_temperature(t_raw),
                salinity_psu=convert_salinity(s_raw),
                date=file_date,
                direction="ascent",
            ))
