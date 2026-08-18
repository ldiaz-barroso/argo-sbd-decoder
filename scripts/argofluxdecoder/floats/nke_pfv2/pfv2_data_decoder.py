"""
pfv2_data_decoder.py
====================
Decode PFV2 measurement data .hex files (CTD, DOXY, IMU).

Data file format:
  Filename: M{mission}C{cycle}S{sensor}F{format}{phase}{dateFreq}.hex
  
  The file is a sequential byte stream alternating between:
    - Timestamp blocks (4-byte epoch2000, inserted every dateFreq+1 measurements)
    - Measurement blocks (variable size depending on sensor and format)

Sensors:
  0 = IMU (pressure + tilt)
  1 = SBE41 CTD (pressure, temperature, salinity)
  2 = Aanderaa 4330 Optode (pressure, C1Phase, C2Phase, tempDoxy)
  3 = RBRargo3 (not yet implemented in Coriolis source)

Formats:
  0 = Standard resolution (pressure/10 - 100)
  2 = High resolution (pressure/20 - 100, for shallow floats)

Phases:
  D = Descent to Park
  P = Park drift
  T = Descent to Profile
  B = Profile drift (bottom)
  A = Ascent
  I = In-air

Reference:
  decode_pfv2_data_file_401_402_404.m (Coriolis MATLAB v085h)
  DOI: https://doi.org/10.17882/45589
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

logger = logging.getLogger(__name__)

# PFV2 epoch: 2000-01-01 00:00:00 UTC
EPOCH_2000 = datetime(2000, 1, 1)

# Phase code to direction mapping
PHASE_TO_DIRECTION = {
    "D": "descent",
    "P": "drift",
    "T": "descent",
    "B": "drift",
    "A": "ascent",
    "I": "ascent",  # In-air treated as surface/ascent
}


def _epoch2000_to_datetime(epoch_seconds: int) -> Optional[datetime]:
    """Convert PFV2 epoch (seconds since 2000-01-01) to datetime."""
    if epoch_seconds == 0 or epoch_seconds == 0xFFFFFFFF:
        return None
    try:
        return EPOCH_2000 + timedelta(seconds=epoch_seconds)
    except (OverflowError, ValueError):
        return None


@dataclass
class Pfv2Measurement:
    """A single PFV2 measurement sample."""
    date: Optional[datetime] = None
    pressure_dbar: float = 0.0
    temperature_degc: Optional[float] = None
    salinity_psu: Optional[float] = None
    c1phase_doxy: Optional[float] = None
    c2phase_doxy: Optional[float] = None
    temp_doxy_degc: Optional[float] = None
    tilt: Optional[int] = None
    direction: str = "ascent"


@dataclass
class Pfv2DataFileResult:
    """Decoded result from a PFV2 measurement data file."""
    mission: int = 0
    cycle: int = 0
    sensor: int = 0
    format_num: int = 0
    phase: str = ""
    date_freq: int = 0
    direction: str = "ascent"
    measurements: List[Pfv2Measurement] = field(default_factory=list)


def decode_data_file(data: bytes, sensor: int, format_num: int,
                     phase: str, date_freq: int,
                     mission: int = 0, cycle: int = 0) -> Pfv2DataFileResult:
    """
    Decode a PFV2 measurement data file.

    Parameters
    ----------
    data : bytes
        Raw bytes of the decompressed data file.
    sensor : int
        Sensor number (0=IMU, 1=SBE41, 2=Aanderaa4330).
    format_num : int
        Data format (0=standard, 2=high-res).
    phase : str
        Phase letter (D, P, T, B, A, I).
    date_freq : int
        Date frequency (timestamp inserted every date_freq+1 measurements).
    mission : int
        Mission number from filename.
    cycle : int
        Cycle number from filename.

    Returns
    -------
    Pfv2DataFileResult
        Decoded measurements with metadata.
    """
    direction = PHASE_TO_DIRECTION.get(phase.upper(), "ascent")

    result = Pfv2DataFileResult(
        mission=mission,
        cycle=cycle,
        sensor=sensor,
        format_num=format_num,
        phase=phase,
        date_freq=date_freq,
        direction=direction,
    )

    if sensor == 0:
        _decode_imu(data, format_num, date_freq, direction, result)
    elif sensor == 1:
        _decode_sbe41(data, format_num, date_freq, direction, result)
    elif sensor == 2:
        _decode_aanderaa4330(data, format_num, date_freq, direction, result)
    else:
        logger.warning("PFV2 data: Sensor %d not supported", sensor)

    return result


def _decode_sbe41(data: bytes, format_num: int, date_freq: int,
                  direction: str, result: Pfv2DataFileResult) -> None:
    """
    Decode SBE41 CTD sensor data.

    Each measurement: P(2) + T(2) + S(2) = 6 bytes
    Timestamp: 4 bytes (every date_freq+1 samples)

    Conversions:
      Format 0: P = raw/10 - 100
      Format 2: P = raw/20 - 100
      Temperature: T = raw/1000 - 10
      Salinity: S = raw/1000
    """
    pos = 0
    meas_count = 0
    current_date: Optional[datetime] = None
    pres_divisor = 10.0 if format_num == 0 else 20.0

    while pos < len(data):
        # Check if this position should be a timestamp
        if meas_count % (date_freq + 1) == 0:
            if pos + 3 >= len(data):
                break
            epoch = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            current_date = _epoch2000_to_datetime(epoch)
            meas_count = 1
        else:
            # Read CTD measurement (6 bytes)
            if pos + 5 >= len(data):
                break
            pres_raw = struct.unpack_from("<H", data, pos)[0]
            temp_raw = struct.unpack_from("<H", data, pos + 2)[0]
            psal_raw = struct.unpack_from("<H", data, pos + 4)[0]
            pos += 6
            meas_count += 1

            pressure = pres_raw / pres_divisor - 100.0
            temperature = temp_raw / 1000.0 - 10.0
            salinity = psal_raw / 1000.0

            meas = Pfv2Measurement(
                date=current_date,
                pressure_dbar=pressure,
                temperature_degc=temperature,
                salinity_psu=salinity,
                direction=direction,
            )
            result.measurements.append(meas)


def _decode_aanderaa4330(data: bytes, format_num: int, date_freq: int,
                         direction: str, result: Pfv2DataFileResult) -> None:
    """
    Decode Aanderaa 4330 optode sensor data.

    Each measurement: P(2) + C1Phase(2) + C2Phase(2) + TempDoxy(2) = 8 bytes
    Timestamp: 4 bytes (every date_freq+1 samples)

    Conversions:
      Format 0: P = raw/10 - 100
      Format 2: P = raw/20 - 100
      C1Phase = raw/500 - 40
      C2Phase = raw/500 - 40
      TempDoxy = raw/1000 - 10
    """
    pos = 0
    meas_count = 0
    current_date: Optional[datetime] = None
    pres_divisor = 10.0 if format_num == 0 else 20.0

    while pos < len(data):
        if meas_count % (date_freq + 1) == 0:
            if pos + 3 >= len(data):
                break
            epoch = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            current_date = _epoch2000_to_datetime(epoch)
            meas_count = 1
        else:
            if pos + 7 >= len(data):
                break
            pres_raw = struct.unpack_from("<H", data, pos)[0]
            c1phase_raw = struct.unpack_from("<H", data, pos + 2)[0]
            c2phase_raw = struct.unpack_from("<H", data, pos + 4)[0]
            temp_doxy_raw = struct.unpack_from("<H", data, pos + 6)[0]
            pos += 8
            meas_count += 1

            pressure = pres_raw / pres_divisor - 100.0
            c1phase = c1phase_raw / 500.0 - 40.0
            c2phase = c2phase_raw / 500.0 - 40.0
            temp_doxy = temp_doxy_raw / 1000.0 - 10.0

            meas = Pfv2Measurement(
                date=current_date,
                pressure_dbar=pressure,
                c1phase_doxy=c1phase,
                c2phase_doxy=c2phase,
                temp_doxy_degc=temp_doxy,
                direction=direction,
            )
            result.measurements.append(meas)


def _decode_imu(data: bytes, format_num: int, date_freq: int,
                direction: str, result: Pfv2DataFileResult) -> None:
    """
    Decode IMU sensor data.

    Each measurement: P(2) + Tilt(1) = 3 bytes
    Timestamp: 4 bytes (every date_freq+1 samples)

    Conversions:
      Pressure: P = raw/10 - 100
      Tilt: raw value (degrees)
    """
    pos = 0
    meas_count = 0
    current_date: Optional[datetime] = None

    while pos < len(data):
        if meas_count % (date_freq + 1) == 0:
            if pos + 3 >= len(data):
                break
            epoch = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            current_date = _epoch2000_to_datetime(epoch)
            meas_count = 1
        else:
            if pos + 2 >= len(data):
                break
            pres_raw = struct.unpack_from("<H", data, pos)[0]
            tilt_raw = data[pos + 2]
            pos += 3
            meas_count += 1

            pressure = pres_raw / 10.0 - 100.0

            meas = Pfv2Measurement(
                date=current_date,
                pressure_dbar=pressure,
                tilt=tilt_raw,
                direction=direction,
            )
            result.measurements.append(meas)
