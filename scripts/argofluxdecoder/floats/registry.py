"""
registry.py
===========
Maps Coriolis decoder IDs to concrete decoder classes.

Usage:
    from argofluxdecoder.floats.registry import get_decoder
    decoder = get_decoder(decoder_id=212, float_info={...})
"""

from typing import Dict, Any
from .base import BaseDecoder


def _import_arvor_arn():
    from .nke.arvor_arn import ArvorArnDecoder
    return ArvorArnDecoder


def _import_arvor_c():
    from .nke.arvor_c import ArvorCDecoder
    return ArvorCDecoder


def _import_arvor_deep():
    from .nke.arvor_deep import ArvorDeepDecoder
    return ArvorDeepDecoder


def _import_provor_sbd():
    from .nke.provor_sbd import ProvorSbdDecoder
    return ProvorSbdDecoder


def _import_nova_sbd():
    from .nova.nova_sbd import NovaSbdDecoder
    return NovaSbdDecoder


def _import_arvor_pfv2():
    from .nke_pfv2.arvor_pfv2 import ArvorPfv2Decoder
    return ArvorPfv2Decoder


def _import_provor_sbd2():
    from .nke_sbd2.provor_sbd2 import ProvorSbd2Decoder
    return ProvorSbd2Decoder


def _import_apex_apf9():
    from .apex.apex_apf9 import ApexApf9Decoder
    return ApexApf9Decoder


def _import_apex_apf11():
    from .apex.apex_apf11 import ApexApf11Decoder
    return ApexApf11Decoder


# ─────────────────────────────────────────────────────────────────────────────
# Registry: decoder_id → (loader_function, description)
# ─────────────────────────────────────────────────────────────────────────────

DECODER_REGISTRY: Dict[int, tuple] = {
    # PROVOR Iridium SBD (IDs 201-215)
    201: (_import_provor_sbd, "PROVOR-II Iridium SBD"),
    202: (_import_provor_sbd, "PROVOR-II DO Iridium SBD"),
    203: (_import_provor_sbd, "PROVOR-III Iridium SBD"),
    204: (_import_provor_sbd, "ARVOR Iridium SBD 4.51"),
    205: (_import_provor_sbd, "ARVOR Iridium SBD 4.52"),
    206: (_import_provor_sbd, "ARVOR-DO Iridium SBD"),
    207: (_import_provor_sbd, "ARVOR-DO Iridium SBD 4.53"),
    208: (_import_provor_sbd, "ARVOR-DO Iridium SBD 4.54"),
    209: (_import_provor_sbd, "PROVOR-DO Iridium SBD (multi-sensor)"),
    210: (_import_provor_sbd, "ARVOR-I Iridium SBD 5.41"),
    211: (_import_provor_sbd, "ARVOR-I Iridium SBD 5.42"),
    213: (_import_provor_sbd, "ARVOR-I-DO Iridium SBD 5.43"),
    215: (_import_provor_sbd, "ARVOR-I Iridium SBD 5.44"),

    # ARVOR-ARN / ARVOR-ARN-DO / ARVOR-ARN-ICE (IDs 212, 214, 217, 222-227, 231-232)
    212: (_import_arvor_arn, "ARVOR-ARN-Ice 5.45"),
    214: (_import_arvor_arn, "PROVOR-ARN-DO-Ice 5.75"),
    217: (_import_arvor_arn, "ARVOR-ARN-DO-Ice 5.46"),
    222: (_import_arvor_arn, "ARVOR-ARN-Ice 5.47"),
    223: (_import_arvor_arn, "ARVOR-ARN-DO-Ice 5.48"),
    224: (_import_arvor_arn, "ARVOR-ARN-Ice RBR 5.49"),
    225: (_import_arvor_arn, "PROVOR-ARN-DO-Ice 5.76"),
    226: (_import_arvor_arn, "ARVOR-ARN-Ice RBR 1Hz 5.51"),
    227: (_import_arvor_arn, "ARVOR-ARN-Ice RBR 1Hz auto-PSAL 5.52"),
    231: (_import_arvor_arn, "ARVOR-ARN-Ice SBE 5.53"),
    232: (_import_arvor_arn, "ARVOR-ARN-Ice 5.54"),

    # ARVOR-Deep-Ice (IDs 216, 218, 221, 228-230)
    216: (_import_arvor_deep, "ARVOR-Deep-Ice 5.65 (IFREMER)"),
    218: (_import_arvor_deep, "ARVOR-Deep-Ice 5.66 (NKE)"),
    221: (_import_arvor_deep, "ARVOR-Deep-Ice 5.67"),
    228: (_import_arvor_deep, "ARVOR-Deep-Ice 5.68 (3T prototype)"),
    229: (_import_arvor_deep, "ARVOR-Deep-Ice 5.69 (2T prototype)"),
    230: (_import_arvor_deep, "ARVOR-Deep-Ice 5.77 (2DO)"),

    # ARVOR-C (IDs 219, 220, 233)
    219: (_import_arvor_c, "ARVOR-C 5.3"),
    220: (_import_arvor_c, "ARVOR-C 5.301"),
    233: (_import_arvor_c, "ARVOR-C 5603A12 (salinity offset +25000)"),

    # ───────────────────────────────────────────────────────────────────────
    # PROVOR SBD2 with bio-optical sensors (IDs 301-303, 140-byte frames)
    # ───────────────────────────────────────────────────────────────────────
    301: (_import_provor_sbd2, "PROVOR Remocean FLBB Iridium SBD2"),
    302: (_import_provor_sbd2, "ARVOR CM FLNTU Iridium SBD2"),
    303: (_import_provor_sbd2, "ARVOR CM FLNTU+CYCLOPS+SEAPOINT Iridium SBD2"),

    # ───────────────────────────────────────────────────────────────────────
    # NOVA/DOVA Iridium SBD (IDs 2001-2003)
    # ───────────────────────────────────────────────────────────────────────
    2001: (_import_nova_sbd, "NOVA 1.0 Iridium SBD (CTD)"),
    2002: (_import_nova_sbd, "DOVA 2.0 Iridium SBD (CTDO)"),
    2003: (_import_nova_sbd, "NOVA 0.9 Iridium SBD (CTD)"),

    # ───────────────────────────────────────────────────────────────────────
    # ARVOR PFV2 Iridium SBD (IDs 401, 402)
    # ───────────────────────────────────────────────────────────────────────
    401: (_import_arvor_pfv2, "ARVOR PFV2 Iridium SBD (2024 firmware)"),
    402: (_import_arvor_pfv2, "ARVOR PFV2 Iridium SBD (2025 firmware)"),

    # ───────────────────────────────────────────────────────────────────────
    # APEX APF9 Iridium SBD/RUDICS (IDs 1001-1016, 1314)
    # ───────────────────────────────────────────────────────────────────────
    1001: (_import_apex_apf9, "APEX APF9 Iridium (v1)"),
    1002: (_import_apex_apf9, "APEX APF9 Iridium (v2)"),
    1003: (_import_apex_apf9, "APEX APF9 Iridium (v3)"),
    1004: (_import_apex_apf9, "APEX APF9 Iridium (v4)"),
    1005: (_import_apex_apf9, "APEX APF9 Iridium (v5)"),
    1006: (_import_apex_apf9, "APEX APF9 Iridium (v6)"),
    1007: (_import_apex_apf9, "APEX APF9 Iridium (v7)"),
    1008: (_import_apex_apf9, "APEX APF9 Iridium (v8)"),
    1009: (_import_apex_apf9, "APEX APF9 Iridium (v9)"),
    1010: (_import_apex_apf9, "APEX APF9 Iridium (v10)"),
    1011: (_import_apex_apf9, "APEX APF9 Iridium (v11)"),
    1012: (_import_apex_apf9, "APEX APF9 Iridium (v12)"),
    1013: (_import_apex_apf9, "APEX APF9 Iridium (v13)"),
    1014: (_import_apex_apf9, "APEX APF9 Iridium (v14)"),
    1015: (_import_apex_apf9, "APEX APF9 Iridium (v15)"),
    1016: (_import_apex_apf9, "APEX APF9 Iridium (v16)"),
    1314: (_import_apex_apf9, "APEX APF9 Iridium SBD (090215)"),

    # ───────────────────────────────────────────────────────────────────────
    # APEX APF11 Iridium SBD/RUDICS (IDs 1101-1132, 1321-1323)
    # ───────────────────────────────────────────────────────────────────────
    1101: (_import_apex_apf11, "APEX APF11 Iridium (2.6.4)"),
    1102: (_import_apex_apf11, "APEX APF11 Iridium (2.7.5)"),
    1103: (_import_apex_apf11, "APEX APF11 Iridium (2.8.0)"),
    1104: (_import_apex_apf11, "APEX APF11 Iridium (2.8.1)"),
    1105: (_import_apex_apf11, "APEX APF11 Iridium (2.8.3)"),
    1106: (_import_apex_apf11, "APEX APF11 Iridium (2.9.0)"),
    1107: (_import_apex_apf11, "APEX APF11 Iridium (2.9.1)"),
    1108: (_import_apex_apf11, "APEX APF11 Iridium (2.9.2)"),
    1109: (_import_apex_apf11, "APEX APF11 Iridium (2.9.3)"),
    1110: (_import_apex_apf11, "APEX APF11 Iridium (2.9.4)"),
    1111: (_import_apex_apf11, "APEX APF11 Iridium (2.10.0)"),
    1112: (_import_apex_apf11, "APEX APF11 Iridium (2.10.1)"),
    1113: (_import_apex_apf11, "APEX APF11 Iridium (2.10.2)"),
    1114: (_import_apex_apf11, "APEX APF11 Iridium (2.10.3)"),
    1115: (_import_apex_apf11, "APEX APF11 Iridium (2.10.3.1)"),
    1121: (_import_apex_apf11, "APEX APF11 Iridium (2.11.3.R)"),
    1122: (_import_apex_apf11, "APEX APF11 Iridium (2.13.1.R)"),
    1123: (_import_apex_apf11, "APEX APF11 Iridium (2.12.3.R)"),
    1124: (_import_apex_apf11, "APEX APF11 Iridium (2.14.3.R)"),
    1125: (_import_apex_apf11, "APEX APF11 Iridium (2.15.0.R)"),
    1126: (_import_apex_apf11, "APEX APF11 Iridium (2.10.4.R)"),
    1127: (_import_apex_apf11, "APEX APF11 Iridium (2.12.2.1.R)"),
    1128: (_import_apex_apf11, "APEX APF11 Iridium (2.15.2.R)"),
    1129: (_import_apex_apf11, "APEX APF11 Iridium (2.16.0.R)"),
    1130: (_import_apex_apf11, "APEX APF11 Iridium (2.17.4.R)"),
    1131: (_import_apex_apf11, "APEX APF11 Iridium (2.18.1.R)"),
    1132: (_import_apex_apf11, "APEX APF11 Iridium (2.19.1.R)"),
    1321: (_import_apex_apf11, "APEX APF11 Iridium SBD (2.10.1.S)"),
    1322: (_import_apex_apf11, "APEX APF11 Iridium SBD (2.11.1.S)"),
    1323: (_import_apex_apf11, "APEX APF11 Iridium SBD (2.12.2.1.S)"),

    # ───────────────────────────────────────────────────────────────────────
    # Future decoders (not yet implemented)
    # ───────────────────────────────────────────────────────────────────────
    # NAVIS: 1301-1323 (RUDICS only, not SBD)
}


def get_decoder(decoder_id: int, float_info: Dict[str, Any]) -> BaseDecoder:
    """
    Instantiate the appropriate decoder for a given decoder ID.

    Parameters
    ----------
    decoder_id : int
        Coriolis decoder ID (e.g. 212, 219).
    float_info : dict
        Float metadata (WMO, IMEI, launch_date, etc.).

    Returns
    -------
    BaseDecoder
        Concrete decoder instance.

    Raises
    ------
    ValueError
        If decoder_id is not supported.
    """
    if decoder_id not in DECODER_REGISTRY:
        supported = sorted(DECODER_REGISTRY.keys())
        raise ValueError(
            f"Decoder ID {decoder_id} not supported. "
            f"Supported IDs: {supported}"
        )

    loader, description = DECODER_REGISTRY[decoder_id]
    decoder_class = loader()
    return decoder_class(decoder_id=decoder_id, float_info=float_info)


def list_supported_decoders() -> Dict[int, str]:
    """Return dict of {decoder_id: description} for all supported decoders."""
    return {did: desc for did, (_, desc) in DECODER_REGISTRY.items()}
