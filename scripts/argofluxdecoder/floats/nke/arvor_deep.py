"""
arvor_deep.py
=============
Decoder for NKE ARVOR-Deep family (Iridium SBD).

Covers decoder IDs: 216, 218, 221, 228, 229, 230

These are deep-capable floats (2000-4000m) with ice detection.
Packet structure is very similar to ARVOR-ARN (decoder 212) but with
additional fields in technical packets for deep operations.

Packet types:
  0 = Technical message 1
  1 = CTD descending profile
  2 = CTD drift measurements
  3 = CTD ascending profile
  4 = Technical message 2
  5 = Parameter packet 1
  6 = Hydraulic actions
  7 = Parameter packet 2

Reference:
  decode_prv_data_ir_sbd_216.m, _218.m, _221.m, _228.m, _229.m, _230.m
  (Coriolis MATLAB decoder, DOI: 10.17882/45589)
"""

from typing import Dict, Any, Optional
from datetime import datetime

from ..base import (
    BaseDecoder, CTDMeasurement, TechnicalData,
    HydraulicAction, GPSFix, DecodedCycle
)
from ...core.bit_utils import get_bits, twos_complement, decode_gps
from ...core.time_utils import nke_date_to_datetime
from .common import (
    get_conversions_for_decoder,
    PACK_TECH1, PACK_CTD_DESC, PACK_CTD_DRIFT, PACK_CTD_ASC,
    PACK_TECH2, PACK_PARAM1, PACK_HYDRAULIC, PACK_PARAM2,
    PACK_CTD_DESC2, PACK_CTD_ASC2,
)


class ArvorDeepDecoder(BaseDecoder):
    """
    Decoder for ARVOR-Deep-Ice family.

    Decoder IDs: 216, 218, 221, 228, 229, 230.

    The packet format is structurally identical to ARVOR-ARN (ID 212)
    for CTD and hydraulic packets. Technical packets have slight
    differences in field arrangement for deep-specific parameters.
    """

    SUPPORTED_IDS = [216, 218, 221, 228, 229, 230]

    def get_sensor_conversions(self) -> Dict[str, Any]:
        return get_conversions_for_decoder(self.decoder_id)

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:

        if pack_type == PACK_TECH1:
            self._decode_tech1(payload, file_name, file_date)
        elif pack_type in (PACK_CTD_DESC, PACK_CTD_DRIFT, PACK_CTD_ASC, PACK_CTD_DESC2, PACK_CTD_ASC2):
            self._decode_ctd(pack_type, payload, file_name, file_date)
        elif pack_type == PACK_TECH2:
            self._decode_tech2(payload, file_name, file_date)
        elif pack_type == PACK_HYDRAULIC:
            self._decode_hydraulic(payload, file_name, file_date)
        elif pack_type in (PACK_PARAM1, PACK_PARAM2):
            self._decode_param(pack_type, payload, file_name, file_date)

    def _decode_tech1(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """
        Decode technical packet 1 for ARVOR-Deep (decIds 216, 218, 221, 228-230).

        Bit layout from decode_prv_data_ir_sbd_221.m (Coriolis MATLAB v085h).
        This layout is DIFFERENT from decoders 210/211/212.

        Key field positions (1-based MATLAB → 0-based Python):
          tabTech1(1)    = cycle number      → values[0]
          tabTech1(38:43)= GPS time HHMMSS   → values[37:43]
          tabTech1(44)   = pressure offset    → values[43]
          tabTech1(50:53)= GPS lat            → values[49:53]
          tabTech1(54:57)= GPS lon            → values[53:57]
          tabTech1(58)   = GPS valid fix      → values[57]
        """
        # Exact layout from decode_prv_data_ir_sbd_221.m case 0
        tab_nb_bits = [
            16,                              # 0: cycle number
            8, 8, 8, 16, 16, 16, 8, 8,      # 1-8
            16, 16, 16, 8, 8, 16, 16,        # 9-15
            8, 8, 8, 16, 16, 8, 8,           # 16-22
            16, 16, 8, 8, 16,                 # 23-27
            8, 8, 8, 8, 16, 16,              # 28-33
            16, 16, 8,                        # 34-36
            8, 8, 8,                          # 37-39 (GPS time HH, MM, SS)
        ] + [8] * 9 + [                      # 40-48 (GPS time DD, MM, YY at 40-42, then misc)
            8, 8, 16, 8, 8, 8, 16, 8, 8, 16, 8,  # 49-59
        ] + [8] * 2 + [8] * 7 + [            # 60-68
            16, 8, 8,                         # 69-71
            16,                               # 72: clock offset
        ] + [8] * 3                           # 73-75

        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 58:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        # GPS time: tabTech1(38:43) = indices 37-42 (HH, MM, SS, DD, MM, YY)
        float_time = None
        if len(values) > 42:
            hh, mm, ss = values[37], values[38], values[39]
            dd, mo, yy = values[40], values[41], values[42]
            if any(v != 0 for v in [dd, mo, yy]):
                try:
                    year = 2000 + yy if yy < 100 else yy
                    float_time = datetime(year, mo, dd, hh, mm, ss)
                except (ValueError, TypeError):
                    pass
        if float_time is None:
            float_time = file_date

        # Pressure offset: tabTech1(44) = index 43
        pres_offset = twos_complement(values[43], 8) / 10.0 if len(values) > 43 else 0.0

        # Internal pressure: same position as other decoders (index 44 for 221)
        # In 221 layout, the internal pressure field may be at a different index.
        # For now use index 44 (needs validation with real ARVOR-Deep data).
        internal_pres = None
        if len(values) > 44 and values[44] != 0:
            internal_pres = values[44] * 5

        # GPS location: tabTech1(50:53) = indices 49-52 (lat: deg, min, frac, sign)
        #               tabTech1(54:57) = indices 53-56 (lon: deg, min, frac, sign)
        gps_valid = True
        try:
            lat = decode_gps(values[49], values[50], values[51], values[52])
            lon = decode_gps(values[53], values[54], values[55], values[56])
            if all(values[i] == 0 for i in [49, 50, 51, 53, 54]):
                gps_valid = False
        except (IndexError, ZeroDivisionError):
            lat, lon = 0.0, 0.0
            gps_valid = False

        # GPS valid fix flag: tabTech1(58) = index 57
        if len(values) > 57 and values[57] == 0:
            gps_valid = False

        if gps_valid:
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num, latitude=lat, longitude=lon,
                date=float_time, valid=True,
            ))

        tech = TechnicalData(cycle=cycle_num, data={
            "iridium_session": values[1] if len(values) > 1 else 0,
            "pressure_offset": pres_offset,
            "internal_pressure_mbar": internal_pres,
            "float_time": float_time,
            "gps_lat": lat if gps_valid else None,
            "gps_lon": lon if gps_valid else None,
            "gps_valid": gps_valid,
            "sbd_file": file_name,
        })
        cycle.technical.append(tech)

    def _decode_ctd(self, pack_type: int, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode CTD packet — identical format to ARVOR-ARN (15 PTS triplets)."""
        tab_nb_bits = [16, 16, 8, 8] + [16] * 45 + [8] * 3
        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 49:
            return

        cycle_num = values[0]
        if not any(v != 0 for v in values[1:]):
            return

        cycle = self.get_or_create_cycle(cycle_num)

        if pack_type in (PACK_CTD_DESC, PACK_CTD_DESC2):
            direction = "descent"
        elif pack_type == PACK_CTD_DRIFT:
            direction = "drift"
        else:
            direction = "ascent"

        conversions = get_conversions_for_decoder(self.decoder_id)
        convert_pressure = conversions["pressure"]
        convert_temperature = conversions["temperature"]
        convert_salinity = conversions["salinity"]

        for i in range(15):
            p_raw = values[4 + i * 3]
            t_raw = values[5 + i * 3]
            s_raw = values[6 + i * 3]

            if p_raw == 0 and t_raw == 0 and s_raw == 0:
                continue

            meas = CTDMeasurement(
                cycle=cycle_num,
                pressure_dbar=convert_pressure(p_raw),
                temperature_degc=convert_temperature(t_raw),
                salinity_psu=convert_salinity(s_raw),
                direction=direction,
            )

            if direction == "ascent":
                cycle.ctd_ascent.append(meas)
            elif direction == "descent":
                cycle.ctd_descent.append(meas)
            else:
                cycle.ctd_drift.append(meas)

    def _decode_tech2(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode tech2 — extract expected measurement counts."""
        tab_nb_bits = [16, 8] + [8] * 12 + [16] * 6 + [8] * 60
        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 5:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)
        cycle.technical.append(TechnicalData(cycle=cycle_num, data={
            "packet_type": "tech2",
            "iridium_session": values[1],
            "sbd_file": file_name,
        }))

    def _decode_hydraulic(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode hydraulic actions — same format as ARVOR-ARN."""
        tab_nb_bits = [16, 16, 16] + [8, 16, 16, 16] * 13 + [8, 8]
        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 55:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        for i in range(13):
            base = 3 + i * 4
            action_type_raw = values[base]
            time_offset = values[base + 1]
            pressure_raw = values[base + 2]
            duration = values[base + 3]

            if time_offset == 0 and pressure_raw == 0 and duration == 0:
                continue

            pressure = twos_complement(pressure_raw, 16) / 10.0

            cycle.hydraulics.append(HydraulicAction(
                cycle=cycle_num,
                action_type="pump" if action_type_raw else "ev",
                pressure_dbar=pressure,
                duration_sec=duration,
            ))

    def _decode_param(self, pack_type: int, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Store parameter packets as raw data."""
        data = list(payload)
        values = get_bits(1, [16] + [8] * 97, data)
        if len(values) < 2:
            return
        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)
        cycle.technical.append(TechnicalData(cycle=cycle_num, data={
            "packet_type": f"param{1 if pack_type == PACK_PARAM1 else 2}",
            "raw_values": values,
            "sbd_file": file_name,
        }))
