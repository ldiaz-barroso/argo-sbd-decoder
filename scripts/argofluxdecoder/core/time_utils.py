"""
time_utils.py
=============
Date/time conversion utilities for Argo float data.

Argo uses Julian days relative to 1950-01-01 00:00:00 UTC.
NKE floats transmit time as day-of-float + HMS within a cycle.

Reference:
  julian_2_gregorian_dec_argo.m, epoch_2_julian_dec_argo.m (Coriolis)
"""

from datetime import datetime, timedelta

# Argo reference epoch: January 1, 1950
ARGO_EPOCH = datetime(1950, 1, 1)

# MATLAB serial date of Jan 1, 1950
JAN_1_1950_MATLAB = 711858.0


def julian_to_datetime(juld: float) -> datetime:
    """Convert Argo Julian day (days since 1950-01-01) to Python datetime."""
    return ARGO_EPOCH + timedelta(days=juld)


def datetime_to_julian(dt: datetime) -> float:
    """Convert Python datetime to Argo Julian day."""
    return (dt - ARGO_EPOCH).total_seconds() / 86400.0


def datestr_to_datetime(s: str) -> datetime:
    """
    Parse NKE-style date strings.

    Supported formats:
      - YYYYMMDDHHMMSS (14 chars)
      - YYYYMMDD (8 chars)
    """
    s = s.strip()
    if len(s) == 14:
        return datetime.strptime(s, "%Y%m%d%H%M%S")
    elif len(s) == 8:
        return datetime.strptime(s, "%Y%m%d")
    else:
        raise ValueError(f"Cannot parse date string: '{s}'")


def float_time_to_datetime(sbd_file_date: datetime, hour: int, minute: int, second: int) -> datetime:
    """
    Reconstruct absolute time from float-transmitted HMS and SBD file date.

    The float transmits hour/minute/second of its internal clock.
    We combine with the date from the SBD filename.

    Parameters
    ----------
    sbd_file_date : datetime
        Date extracted from SBD filename (YYYYMMDD).
    hour, minute, second : int
        Time fields from the technical message.

    Returns
    -------
    datetime
        Reconstructed UTC timestamp.
    """
    base_date = sbd_file_date.replace(hour=0, minute=0, second=0, microsecond=0)
    return base_date + timedelta(hours=hour, minutes=minute, seconds=second)


def nke_date_to_datetime(day: int, month: int, year: int, hour: int, minute: int, second: int) -> datetime:
    """
    Convert NKE 6-field date (DD, MM, YY, HH, MM, SS) to datetime.

    NKE transmits 2-digit year.
    """
    full_year = 2000 + year if year < 100 else year
    return datetime(full_year, month, day, hour, minute, second)
