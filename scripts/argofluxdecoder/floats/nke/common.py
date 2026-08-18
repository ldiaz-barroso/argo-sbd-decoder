"""
common.py
=========
Shared NKE packet parsing routines used by all NKE decoder classes.

Sensor value conversions for standard NKE CTD (SBE41-equivalent).
GPS decoding from technical packets.

Reference:
  Coriolis MATLAB decoder (DOI: 10.17882/45589)
  https://github.com/euroargodev/Coriolis-data-processing-chain-for-Argo-floats

  Pressure functions (3 groups with different offsets):
    sensor_2_value_for_pressure_201_203_215_216_218_221_228_229_230.m  → (tc+30000)/10
    sensor_2_value_for_pressure_2xx_2_10_to_14_17_22_to_27_31_32.m    → (tc+10000)/10
    sensor_2_value_for_pressure_204_to_209_219_220.m                  → tc/10

  Temperature function (same for all NKE Iridium SBD IDs):
    sensor_2_value_for_temp_2xx_1_to_3_15_16_18_21_28_29_30.m         → tc/1000

  Salinity function:
    sensor_2_value_for_salinity_2xx_1_to_3_15_16_18_21_28_29_30.m     → raw/1000
    (empirically validated; see note below)
"""

from typing import Dict, Any

from ...core.bit_utils import twos_complement

# Default fill values (from Coriolis: init_default_values.m)
PRES_COUNTS_DEF = 65535
TEMP_COUNTS_DEF = 65535
SAL_COUNTS_DEF = 65535


# ─────────────────────────────────────────────────────────────────────────────
# PRESSURE conversion functions — 3 groups based on offset
#
# All NKE Iridium SBD decoders (201-232) encode pressure as a signed 16-bit
# value with an additive offset, divided by 10 to get dbar:
#   P_dbar = (twos_complement(raw, 16) + OFFSET) / 10.0
#
# The offset varies by decoder group:
#   Group A (deep): +30000  → range up to ~3553.5 dbar max positive
#   Group B (standard): +10000  → range up to ~4553.5 dbar (with deep profiles)
#   Group C (old firmware): 0  → range ±3276.7 dbar
# ─────────────────────────────────────────────────────────────────────────────

def counts_to_pressure_group_a(counts: int) -> float:
    """
    Pressure conversion for Group A decoder IDs.
    IDs: 201, 203, 215, 216, 218, 221, 228, 229, 230

    Formula: P_dbar = (twos_complement(raw, 16) + 30000) / 10.0

    Source: sensor_2_value_for_pressure_201_203_215_216_218_221_228_229_230.m
    """
    if counts == PRES_COUNTS_DEF or counts == 0:
        return float('nan')
    signed = twos_complement(counts, 16)
    return (signed + 30000) / 10.0


def counts_to_pressure_group_b(counts: int) -> float:
    """
    Pressure conversion for Group B decoder IDs.
    IDs: 202, 210, 211, 212, 213, 214, 217, 222, 223, 224, 225, 226, 227, 231, 232

    Formula: P_dbar = (twos_complement(raw, 16) + 10000) / 10.0

    Source: sensor_2_value_for_pressure_2xx_2_10_to_14_17_22_to_27_31_32.m

    Validated against:
      - NKE parser output for IMEI 300534065460740 (ID 211): exact match
      - IMEI 300534065469590 (ID 212): P range 0.3-763 dbar, all correct
    """
    if counts == PRES_COUNTS_DEF or counts == 0:
        return float('nan')
    signed = twos_complement(counts, 16)
    return (signed + 10000) / 10.0


def counts_to_pressure_group_c(counts: int) -> float:
    """
    Pressure conversion for Group C decoder IDs.
    IDs: 204, 205, 206, 207, 208, 209, 219, 220

    Formula: P_dbar = twos_complement(raw, 16) / 10.0

    Source: sensor_2_value_for_pressure_204_to_209_219_220.m
    """
    if counts == PRES_COUNTS_DEF or counts == 0:
        return float('nan')
    signed = twos_complement(counts, 16)
    return signed / 10.0


# ─────────────────────────────────────────────────────────────────────────────
# TEMPERATURE conversion — same for ALL NKE Iridium SBD IDs (201-232)
#
# Formula: T_degC = twos_complement(raw, 16) / 1000.0
#
# Uses signed 16-bit encoding. Positive values for warm water, negative for
# cold (Arctic/Antarctic). Range: -32.768 to +32.767 °C.
#
# Source: sensor_2_value_for_temp_2xx_1_to_3_15_16_18_21_28_29_30.m
# Validated: ID 211 (Arctic, T=-1.444°C ✓), ID 212 (temperate, T=7.431°C ✓)
# ─────────────────────────────────────────────────────────────────────────────

def counts_to_temperature(counts: int) -> float:
    """
    Temperature conversion for all NKE Iridium SBD decoder IDs (201-232).

    Formula: T_degC = twos_complement(raw, 16) / 1000.0
    """
    if counts == TEMP_COUNTS_DEF:
        return float('nan')
    signed = twos_complement(counts, 16)
    return signed / 1000.0


# ─────────────────────────────────────────────────────────────────────────────
# SALINITY conversion — THREE groups
#
# Group A (IDs 201, 203, 215, 216, 218, 221, 228-230):
#   S_PSU = (raw + 10000) / 1000.0
#   Source: sensor_2_value_for_salinity_2xx_1_to_3_15_16_18_21_28_29_30.m
#
# Group B (IDs 202, 204-214, 217, 219-220, 222-227, 231, 232):
#   S_PSU = raw / 1000.0
#   Source: sensor_2_value_for_salinity_2xx_10_to_14_17_20_22_to_27_31_32.m
#   Validated: ID 211 (S=34.885 ✓), ID 212 (S=34.062 ✓)
#
# Group C (ID 233 — ARVOR-C 5603A12, firmware not yet in Coriolis catalogue):
#   S_PSU = (raw + 25000) / 1000.0
#   Validated against NKE parser output: raw=12046 → S=37.046 ✓
# ─────────────────────────────────────────────────────────────────────────────

def counts_to_salinity_group_a(counts: int) -> float:
    """
    Salinity conversion for Group A decoder IDs (201, 203, 215, 216, 218, 221, 228-230).

    Formula: S_PSU = (raw + 10000) / 1000.0

    Source: sensor_2_value_for_salinity_2xx_1_to_3_15_16_18_21_28_29_30.m
    """
    if counts == SAL_COUNTS_DEF:
        return float('nan')
    return (counts + 10000) / 1000.0


def counts_to_salinity(counts: int) -> float:
    """
    Salinity conversion for Group B decoder IDs (202, 204-214, 217, 219-220, 222-227, 231, 232).

    Formula: S_PSU = raw / 1000.0

    Source: sensor_2_value_for_salinity_2xx_10_to_14_17_20_22_to_27_31_32.m
    """
    if counts == SAL_COUNTS_DEF:
        return float('nan')
    return counts / 1000.0


def counts_to_salinity_group_c(counts: int) -> float:
    """
    Salinity conversion for ARVOR-C 5603A12 (decoder ID 233).

    Formula: S_PSU = (raw + 25000) / 1000.0

    This firmware encodes salinity with a -25000 offset to provide
    higher resolution in the oceanic range (25-42 PSU).
    Validated: raw=12046 → S=37.046 (matches NKE parser output).
    """
    if counts == SAL_COUNTS_DEF:
        return float('nan')
    return (counts + 25000) / 1000.0


# ─────────────────────────────────────────────────────────────────────────────
# Decoder ID → conversion function mapping
# ─────────────────────────────────────────────────────────────────────────────

# Pressure group membership (from Coriolis MATLAB)
_PRESSURE_GROUP_A_IDS = frozenset([201, 203, 215, 216, 218, 221, 228, 229, 230])
_PRESSURE_GROUP_B_IDS = frozenset([202, 210, 211, 212, 213, 214, 217, 222, 223,
                                   224, 225, 226, 227, 231, 232])
_PRESSURE_GROUP_C_IDS = frozenset([204, 205, 206, 207, 208, 209, 219, 220, 233])

# Salinity Group C: ARVOR-C 5603A12 (ID 233) uses (raw+25000)/1000
_SALINITY_GROUP_C_IDS = frozenset([233])


def get_conversions_for_decoder(decoder_id: int) -> Dict[str, Any]:
    """
    Return the appropriate sensor conversion functions for a decoder ID.

    Pressure varies by group (offset differs). Temperature and salinity
    use the same formula for all NKE Iridium SBD decoders.

    Parameters
    ----------
    decoder_id : int
        Coriolis decoder ID (201-232).

    Returns
    -------
    dict
        Keys: 'pressure', 'temperature', 'salinity' → callable.
    """
    if decoder_id in _PRESSURE_GROUP_A_IDS:
        pressure_fn = counts_to_pressure_group_a
    elif decoder_id in _PRESSURE_GROUP_C_IDS:
        pressure_fn = counts_to_pressure_group_c
    else:
        # Group B is the default (most common, includes 210-214, 217, 222+)
        pressure_fn = counts_to_pressure_group_b

    # Salinity: Group A uses (raw+10000)/1000, Group C uses (raw+25000)/1000, others use raw/1000
    if decoder_id in _SALINITY_GROUP_C_IDS:
        salinity_fn = counts_to_salinity_group_c
    elif decoder_id in _PRESSURE_GROUP_A_IDS:
        salinity_fn = counts_to_salinity_group_a
    else:
        salinity_fn = counts_to_salinity

    return {
        "pressure": pressure_fn,
        "temperature": counts_to_temperature,
        "salinity": salinity_fn,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Legacy aliases (for backward compatibility with existing imports)
# ─────────────────────────────────────────────────────────────────────────────

# These are kept so that any other module importing them doesn't break.
counts_to_pressure_standard = counts_to_pressure_group_b
counts_to_temperature_standard = counts_to_temperature
counts_to_salinity_standard = counts_to_salinity
counts_to_pressure_210_211 = counts_to_pressure_group_b
counts_to_temperature_210_211 = counts_to_temperature


# ─────────────────────────────────────────────────────────────────────────────
# Packet type constants (common across NKE decoders)
# ─────────────────────────────────────────────────────────────────────────────

PACK_TECH1 = 0       # Technical message 1
PACK_CTD_DESC = 1    # CTD descending profile
PACK_CTD_DRIFT = 2   # CTD drift measurements
PACK_CTD_ASC = 3     # CTD ascending profile
PACK_TECH2 = 4       # Technical message 2
PACK_PARAM1 = 5      # Parameter packet 1 (float configuration)
PACK_HYDRAULIC = 6   # Hydraulic actions (EV/pump)
PACK_PARAM2 = 7      # Parameter packet 2 (calibration)
PACK_CTD_DESC2 = 13  # Extended CTD descending (some IDs)
PACK_CTD_ASC2 = 14   # Extended CTD ascending (some IDs)
