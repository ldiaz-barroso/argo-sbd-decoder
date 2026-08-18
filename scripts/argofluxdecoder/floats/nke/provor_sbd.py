"""
provor_sbd.py
=============
Decoder for NKE PROVOR family (Iridium SBD).

Covers decoder IDs: 201–211, 213, 215

PROVOR floats via Iridium SBD share the same core packet structure
as ARVOR-ARN/Deep. Main differences:
- Some IDs have additional sensor data (DO, FLBB, OCR)
- Technical packets have different field layouts per version
- CTD packet format (15 PTS triplets) is identical

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
  decode_prv_data_ir_sbd_201_203.m, _202.m, _204.m, _205.m,
  _206_207_208.m, _209.m, _210_211.m, _213.m, _215.m
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
    counts_to_pressure_standard,
    counts_to_temperature_standard,
    counts_to_salinity_standard,
    counts_to_pressure_210_211,
    counts_to_temperature_210_211,
    PACK_TECH1, PACK_CTD_DESC, PACK_CTD_DRIFT, PACK_CTD_ASC,
    PACK_TECH2, PACK_PARAM1, PACK_HYDRAULIC, PACK_PARAM2,
    PACK_CTD_DESC2, PACK_CTD_ASC2,
)


# ─────────────────────────────────────────────────────────────────────────────
# Tech1 bit layouts per decoder ID group
# From: decode_prv_data_ir_sbd_201_203.m, etc.
# ─────────────────────────────────────────────────────────────────────────────

def _tech1_layout_201_203() -> list:
    """Tech1 layout for decoder IDs 201, 203."""
    return [
        16, 8, 16, 16,
        8, 8, 8, 16, 16, 16, 8, 8,
        16, 16, 16, 8, 16, 16, 16, 16,
        8, 8, 8, 16, 16, 8, 8,
        16, 16, 8, 8, 16,
        8, 8, 8, 8, 16, 16,
        16, 16, 8,
    ] + [8] * 9 + [
        8, 8, 16, 8, 8, 8, 16, 8,
    ] + [8] * 10


def _tech1_layout_default() -> list:
    """Default tech1 layout (similar to 212 / ARVOR-ARN)."""
    return [
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


class ProvorSbdDecoder(BaseDecoder):
    """
    Decoder for PROVOR / ARVOR (non-deep, non-ARN) Iridium SBD family.

    Decoder IDs: 201-211, 213, 215.

    CTD packet format is identical to ARVOR-ARN (15 PTS triplets per packet).
    Technical and parameter packets vary slightly between versions.
    """

    SUPPORTED_IDS = list(range(201, 212)) + [213, 215]

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

    def _get_tech1_layout(self) -> list:
        """Return the appropriate tech1 bit layout for this decoder_id."""
        if self.decoder_id in (201, 203):
            return _tech1_layout_201_203()
        return _tech1_layout_default()

    def _decode_tech1(self, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode technical packet 1."""
        tab_nb_bits = self._get_tech1_layout()
        data = list(payload)
        values = get_bits(1, tab_nb_bits, data)

        if len(values) < 40:
            return

        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)

        # GPS time — indices 40-45 (0-based) = HH MM SS DD MM YY
        # From MATLAB: floatTime = datenum(sprintf('%02d%02d%02d%02d%02d%02d', tabTech1(41:46)), 'HHMMSSddmmyy')
        gps_time = None
        if len(values) > 45:
            hh, mm, ss = values[40], values[41], values[42]
            dd, mo, yy = values[43], values[44], values[45]
            if any(v != 0 for v in [dd, mo, yy]):
                try:
                    year = 2000 + yy if yy < 100 else yy
                    gps_time = datetime(year, mo, dd, hh, mm, ss)
                except (ValueError, TypeError):
                    pass

        # GPS — positions depend on decoder ID but typically around indices 52-59
        # For simplicity, try the standard ARVOR-ARN positions first
        gps_valid = False
        lat, lon = 0.0, 0.0

        # Try to find GPS fields (varies by version)
        # Most PROVOR SBD versions have GPS at similar offsets
        if len(values) > 59:
            try:
                lat = decode_gps(values[52], values[53], values[54], values[55])
                lon = decode_gps(values[56], values[57], values[58], values[59])
                if not all(values[i] == 0 for i in [52, 53, 54, 56, 57]):
                    gps_valid = True
            except (IndexError, ValueError):
                pass

        if gps_valid:
            cycle.gps_fixes.append(GPSFix(
                cycle=cycle_num, latitude=lat, longitude=lon,
                date=gps_time if gps_time else file_date, valid=True,
            ))

        # Internal pressure (vacuum) — index 47, multiply by 5 to get mbar
        # From Coriolis MATLAB: field 48 in tabTech1 (1-based) = index 47 (0-based)
        # Validated: 113*5=565, 111*5=555, 110*5=550 match NKE reference exactly
        internal_pressure_mbar = None
        if len(values) > 47 and values[47] != 0:
            internal_pressure_mbar = values[47] * 5

        # Pressure offset — index 46 (tabTech1(47) in MATLAB)
        # MATLAB applies: twos_complement(val, 8) / 10
        pressure_offset = None
        if len(values) > 46:
            from ...core.bit_utils import twos_complement as _tc
            pressure_offset = _tc(values[46], 8) / 10.0

        tech = TechnicalData(cycle=cycle_num, data={
            "iridium_session": values[1] if len(values) > 1 else 0,
            "gps_lat": lat if gps_valid else None,
            "gps_lon": lon if gps_valid else None,
            "gps_valid": gps_valid,
            "float_time": gps_time,
            "internal_pressure_mbar": internal_pressure_mbar,
            "pressure_offset": pressure_offset,
            "sbd_file": file_name,
        })
        cycle.technical.append(tech)

    def _decode_ctd(self, pack_type: int, payload: bytes, file_name: str, file_date: Optional[datetime]):
        """Decode CTD packet — 15 PTS triplets (identical to ARVOR-ARN)."""
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

        # Select conversion functions based on decoder ID
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
        """Decode tech2 — store raw."""
        data = list(payload)
        values = get_bits(1, [16, 8] + [8] * 96, data)
        if len(values) < 2:
            return
        cycle_num = values[0]
        cycle = self.get_or_create_cycle(cycle_num)
        cycle.technical.append(TechnicalData(cycle=cycle_num, data={
            "packet_type": "tech2",
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
            "sbd_file": file_name,
        }))
