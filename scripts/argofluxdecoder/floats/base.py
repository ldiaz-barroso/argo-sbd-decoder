"""
base.py
=======
Abstract base class for all float decoders.

Every float family implements a concrete subclass that knows how to
decode its specific packet types and produce standardized output.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class CTDMeasurement:
    """A single CTD measurement."""
    cycle: int
    pressure_dbar: float
    temperature_degc: float
    salinity_psu: float
    date: Optional[datetime] = None
    direction: str = "ascent"  # "ascent", "descent", "drift"


@dataclass
class TechnicalData:
    """Technical/engineering data for one cycle."""
    cycle: int
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HydraulicAction:
    """A single EV or pump action."""
    cycle: int
    action_type: str  # "ev" or "pump"
    date: Optional[datetime] = None
    pressure_dbar: float = 0.0
    duration_sec: int = 0


@dataclass
class GPSFix:
    """GPS position fix."""
    cycle: int
    latitude: float
    longitude: float
    date: Optional[datetime] = None
    valid: bool = True


@dataclass
class DecodedCycle:
    """All decoded data from one float cycle."""
    cycle: int
    technical: List[TechnicalData] = field(default_factory=list)
    ctd_ascent: List[CTDMeasurement] = field(default_factory=list)
    ctd_descent: List[CTDMeasurement] = field(default_factory=list)
    ctd_drift: List[CTDMeasurement] = field(default_factory=list)
    hydraulics: List[HydraulicAction] = field(default_factory=list)
    gps_fixes: List[GPSFix] = field(default_factory=list)


class BaseDecoder(ABC):
    """
    Abstract decoder for Argo float SBD data.

    Subclasses implement decode_packet() for their specific packet formats.
    The base class accumulates decoded data and provides access methods.
    """

    def __init__(self, decoder_id: int, float_info: Dict[str, Any]):
        """
        Parameters
        ----------
        decoder_id : int
            Coriolis decoder ID (e.g. 212, 219).
        float_info : dict
            Float metadata: WMO, IMEI, launch_date, launch_lat, launch_lon, etc.
        """
        self.decoder_id = decoder_id
        self.float_info = float_info
        self.cycles: Dict[int, DecodedCycle] = {}
        self._raw_packets: List[Dict] = []

    def get_or_create_cycle(self, cycle_num: int) -> DecodedCycle:
        """Get existing cycle data or create a new one."""
        if cycle_num not in self.cycles:
            self.cycles[cycle_num] = DecodedCycle(cycle=cycle_num)
        return self.cycles[cycle_num]

    @abstractmethod
    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        """
        Decode a single SBD packet and store results internally.

        Parameters
        ----------
        pack_type : int
            Packet type byte (0x00, 0x01, etc.).
        payload : bytes
            99-byte payload (frame without the type byte).
        file_name : str
            Source SBD filename (for logging/tracing).
        file_date : datetime, optional
            Date from SBD filename.
        """
        ...

    @abstractmethod
    def get_sensor_conversions(self) -> Dict[str, Any]:
        """Return sensor conversion parameters for this decoder ID."""
        ...

    def get_all_profiles(self) -> List[DecodedCycle]:
        """Return all decoded cycles sorted by cycle number."""
        return [self.cycles[k] for k in sorted(self.cycles.keys())]

    @property
    def name(self) -> str:
        """Human-readable decoder name."""
        return f"{self.__class__.__name__} (ID={self.decoder_id})"
