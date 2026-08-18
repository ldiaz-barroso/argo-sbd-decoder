"""
bit_utils.py
============
Low-level bit manipulation utilities.

Direct Python translation of Coriolis MATLAB functions:
  - get_bits.m
  - twos_complement_dec_argo.m

Reference:
  Coriolis data processing chain for Argo floats
  https://github.com/euroargodev/Coriolis-data-processing-chain-for-Argo-floats
  DOI: https://doi.org/10.17882/45589
"""

from typing import List


def get_bits(first_bit: int, tab_nb_bits: List[int], data: bytes) -> List[int]:
    """
    Retrieve values from a byte sequence given bit positions and lengths.

    Direct translation of get_bits.m (Coriolis MATLAB decoder).

    Parameters
    ----------
    first_bit : int
        1-based position of the first bit to read.
    tab_nb_bits : list of int
        Bit-length of each successive field to extract.
    data : bytes or list of int
        Raw message bytes (99 bytes = the payload after stripping packet-type byte,
        or 100 bytes when the packet-type byte is included — caller decides).

    Returns
    -------
    list of int
        Decoded unsigned integer values, one per entry in tab_nb_bits.
        Returns partial list if bits run out of range.

    Notes
    -----
    MATLAB uses 1-based indexing and column vectors; Python uses 0-based.
    The algorithm is kept identical to the MATLAB original for traceability.
    Bits are numbered MSB-first within each byte (big-endian bit order).
    """
    values = []
    data_bytes = list(data) if not isinstance(data, list) else data
    data_length = len(data_bytes) * 8

    # Build array of starting bit positions (1-based, like MATLAB)
    tab_first_bit = [first_bit]
    for i in range(1, len(tab_nb_bits)):
        tab_first_bit.append(tab_first_bit[i - 1] + tab_nb_bits[i - 1])

    for idx, fb in enumerate(tab_first_bit):
        nb_bits = tab_nb_bits[idx]
        last_bit = fb + nb_bits - 1

        if not (fb >= 1 and last_bit <= data_length):
            # Out of range — stop (mirrors MATLAB behaviour: return early)
            break

        # Convert to 0-based byte indices
        first_byte_num = (fb - 1) // 8          # 0-based
        last_byte_num  = (last_bit - 1) // 8    # 0-based

        if first_byte_num == last_byte_num:
            # All bits inside a single byte
            last_bit_in_byte = last_bit - first_byte_num * 8   # 1-based within byte
            shift = 8 - last_bit_in_byte
            mask  = (1 << nb_bits) - 1
            value = (data_bytes[first_byte_num] >> shift) & mask
        else:
            # Bits span multiple bytes
            nb_bits_in_first_byte = 8 - (fb - first_byte_num * 8) + 1
            nb_bits_in_last_byte  = last_bit - last_byte_num * 8

            mask_first = (1 << nb_bits_in_first_byte) - 1
            from_first = data_bytes[first_byte_num] & mask_first
            from_last  = data_bytes[last_byte_num] >> (8 - nb_bits_in_last_byte)

            value = from_last
            factor_exp = nb_bits_in_last_byte
            for byte_idx in range(last_byte_num - 1, first_byte_num, -1):
                value += data_bytes[byte_idx] * (1 << factor_exp)
                factor_exp += 8
            value += from_first * (1 << factor_exp)

        values.append(int(value))

    return values


def twos_complement(x: int, bits: int) -> int:
    """
    Convert an unsigned integer to its signed two's-complement value.

    Direct translation of twos_complement_dec_argo.m (Coriolis MATLAB decoder).

    Parameters
    ----------
    x : int
        Unsigned integer value.
    bits : int
        Bit width (e.g. 8, 16).

    Returns
    -------
    int
        Signed integer.

    Examples
    --------
    >>> twos_complement(255, 8)
    -1
    >>> twos_complement(127, 8)
    127
    """
    if (x >> (bits - 1)) & 1:          # MSB is 1 → negative
        return -((x ^ ((1 << bits) - 1)) + 1)
    return x


def decode_gps(deg: int, minutes: int, minutes_frac: int, sign: int) -> float:
    """
    Convert NKE-encoded GPS degrees/minutes to decimal degrees.

    NKE floats transmit:  degrees (int) + minutes (int) + minutes/10000 (frac)
    with a separate sign byte (0=positive, 1=negative).

    Parameters
    ----------
    deg : int
        Integer degrees.
    minutes : int
        Integer part of arc-minutes.
    minutes_frac : int
        Fractional arc-minutes × 10000.
    sign : int
        0 = positive (N or E), 1 = negative (S or W).

    Returns
    -------
    float
        Decimal degrees.
    """
    sign_val = -1 if sign else 1
    return sign_val * (deg + (minutes + minutes_frac / 10000.0) / 60.0)
