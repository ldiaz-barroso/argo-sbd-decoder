"""
arvor_pfv2.py
=============
Main decoder class for NKE ARVOR PFV2 floats (decoder IDs 401, 402).

This decoder uses a fundamentally different pipeline from the NKE 2xx decoders:
  1. SBD files are reassembled into complete .hex data files
  2. Tech files are decoded for GPS, phase timing, and engineering data
  3. Data files are decoded for CTD/DOXY measurements
  4. Results are mapped to the standard DecodedCycle structure

The decoder supports two modes:
  - SBD mode: input is a directory of raw .sbd files (fragments)
  - HEX mode: input is a directory of already-reconstructed .hex files

Reference:
  decode_arvor_pfv2_iridium_sbd.m (Coriolis MATLAB v085h)
  DOI: https://doi.org/10.17882/45589
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import (
    BaseDecoder,
    CTDMeasurement,
    GPSFix,
    HydraulicAction,
    TechnicalData,
)
from .pfv2_sbd_reassembly import ReassembledFile, reassemble_sbd_directory
from .pfv2_tech_decoder import Pfv2TechResult, decode_tech_file
from .pfv2_data_decoder import Pfv2DataFileResult, decode_data_file

logger = logging.getLogger(__name__)


class ArvorPfv2Decoder(BaseDecoder):
    """
    Decoder for NKE ARVOR PFV2 Iridium SBD floats (IDs 401, 402).

    PFV2 uses a file-based architecture:
      - SBD messages are fragments that reassemble into .hex files
      - Tech files (M{m}C{c}TEC{n}.hex) contain GPS, timing, engineering
      - Data files (M{m}C{c}S{s}F{f}{phase}{freq}.hex) contain measurements

    Unlike NKE 2xx decoders, PFV2 does NOT use fixed 100-byte SBD frames.
    The decode_packet() method is implemented for interface compatibility
    but the primary entry point is decode_directory().
    """

    def __init__(self, decoder_id: int, float_info: Dict[str, Any]):
        super().__init__(decoder_id, float_info)
        self._reassembled_files: List[ReassembledFile] = []
        self._tech_results: List[Pfv2TechResult] = []
        self._data_results: List[Pfv2DataFileResult] = []

    def decode_packet(self, pack_type: int, payload: bytes,
                      file_name: str = "", file_date: Optional[datetime] = None) -> None:
        """
        Interface compatibility method.

        PFV2 does not use packet-by-packet decoding. This method accepts
        pre-reassembled .hex file content passed as payload with metadata
        encoded in pack_type:
          - pack_type 100+ = tech file (actual type stored in payload)
          - pack_type 200+ = data file (actual type stored in payload)

        For the primary pipeline, use decode_directory() instead.
        """
        # This method is called by the standard pipeline with reassembled data
        # pack_type encoding for PFV2:
        #   pack_type = 0xFF → raw .hex file content (reassembled)
        #   The file metadata is encoded in file_name

        if not payload:
            return

        # Parse file_name to determine type
        from .pfv2_sbd_reassembly import _create_reassembled_file
        rf = _create_reassembled_file(file_name, payload, [])
        if rf is None:
            return

        if rf.file_type == "tech":
            tech_result = decode_tech_file(payload, self.decoder_id)
            self._tech_results.append(tech_result)
            self._apply_tech_result(tech_result)
        elif rf.file_type == "data":
            data_result = decode_data_file(
                payload,
                sensor=rf.sensor,
                format_num=rf.format_num,
                phase=rf.phase,
                date_freq=rf.date_freq,
                mission=rf.mission,
                cycle=rf.cycle,
            )
            self._data_results.append(data_result)
            self._apply_data_result(data_result)

    def decode_directory(self, sbd_dir: Path) -> None:
        """
        Primary decode entry point for PFV2.

        Reassembles SBD fragments into .hex files, then decodes all
        tech and data files.

        Parameters
        ----------
        sbd_dir : Path
            Directory containing .sbd files (raw Iridium SBD fragments).
        """
        sbd_dir = Path(sbd_dir)

        # Also support direct .hex input (if user provides pre-assembled files)
        hex_files = sorted(sbd_dir.glob("*.hex"))
        if hex_files:
            logger.info("PFV2: Found %d .hex files directly, skipping SBD reassembly", len(hex_files))
            self._decode_hex_files(hex_files)
            return

        # Standard pipeline: reassemble SBD fragments
        self._reassembled_files = reassemble_sbd_directory(sbd_dir)

        if not self._reassembled_files:
            logger.warning("PFV2: No files could be reassembled from %s", sbd_dir)
            return

        logger.info(
            "PFV2: Reassembled %d files, decoding...",
            len(self._reassembled_files)
        )

        # Decode tech files first (for GPS and cycle metadata)
        for rf in self._reassembled_files:
            if rf.file_type == "tech":
                tech_result = decode_tech_file(rf.data, self.decoder_id)
                self._tech_results.append(tech_result)
                self._apply_tech_result(tech_result)

        # Decode data files (CTD measurements)
        for rf in self._reassembled_files:
            if rf.file_type == "data":
                data_result = decode_data_file(
                    rf.data,
                    sensor=rf.sensor,
                    format_num=rf.format_num,
                    phase=rf.phase,
                    date_freq=rf.date_freq,
                    mission=rf.mission,
                    cycle=rf.cycle,
                )
                self._data_results.append(data_result)
                self._apply_data_result(data_result)

    def _decode_hex_files(self, hex_files: List[Path]) -> None:
        """Decode pre-assembled .hex files directly."""
        from .pfv2_sbd_reassembly import _create_reassembled_file

        for hex_path in hex_files:
            data = hex_path.read_bytes()
            rf = _create_reassembled_file(hex_path.name, data, [hex_path.name])
            if rf is None:
                continue

            if rf.file_type == "tech":
                tech_result = decode_tech_file(rf.data, self.decoder_id)
                self._tech_results.append(tech_result)
                self._apply_tech_result(tech_result)
            elif rf.file_type == "data":
                data_result = decode_data_file(
                    rf.data,
                    sensor=rf.sensor,
                    format_num=rf.format_num,
                    phase=rf.phase,
                    date_freq=rf.date_freq,
                    mission=rf.mission,
                    cycle=rf.cycle,
                )
                self._data_results.append(data_result)
                self._apply_data_result(data_result)

    def _apply_tech_result(self, tech: Pfv2TechResult) -> None:
        """Map PFV2 tech result to standard DecodedCycle structure."""
        cycle_num = tech.cycle
        if cycle_num == 0 and tech.mission == 0:
            return  # Self-test or unknown, skip

        cycle = self.get_or_create_cycle(cycle_num)

        # GPS fixes
        for gps in tech.gps_fixes:
            fix = GPSFix(
                cycle=cycle_num,
                latitude=gps.latitude,
                longitude=gps.longitude,
                date=gps.time,
                valid=gps.valid,
            )
            cycle.gps_fixes.append(fix)

        # Technical data
        tech_dict = {
            "float_id": tech.float_id,
            "firmware": tech.firmware,
            "mission": tech.mission,
        }
        tech_dict.update(tech.tech_data)

        # Add phase event summaries
        for evt in tech.phase_events:
            tech_dict[evt.label] = str(evt.time) if evt.time else "unknown"
            if evt.pressure_dbar is not None:
                tech_dict[f"{evt.label}_pressure_dbar"] = evt.pressure_dbar

        cycle.technical.append(TechnicalData(cycle=cycle_num, data=tech_dict))

        # Buoyancy actions as hydraulics
        for action in tech.tech_data.get("buoyancy_actions", []):
            cycle.hydraulics.append(HydraulicAction(
                cycle=cycle_num,
                action_type=f"buoyancy_{action.get('action_type', 0)}",
                pressure_dbar=action.get("pressure_dbar", 0.0),
                duration_sec=action.get("duration_sec", 0),
            ))

    def _apply_data_result(self, data_result: Pfv2DataFileResult) -> None:
        """Map PFV2 data measurements to standard DecodedCycle structure."""
        cycle_num = data_result.cycle
        cycle = self.get_or_create_cycle(cycle_num)

        for meas in data_result.measurements:
            # Only add CTD measurements (sensor 1 with T and S)
            if meas.temperature_degc is not None and meas.salinity_psu is not None:
                ctd = CTDMeasurement(
                    cycle=cycle_num,
                    pressure_dbar=meas.pressure_dbar,
                    temperature_degc=meas.temperature_degc,
                    salinity_psu=meas.salinity_psu,
                    date=meas.date,
                    direction=meas.direction,
                )
                if meas.direction == "ascent":
                    cycle.ctd_ascent.append(ctd)
                elif meas.direction == "descent":
                    cycle.ctd_descent.append(ctd)
                else:
                    cycle.ctd_drift.append(ctd)

    def get_sensor_conversions(self) -> Dict[str, Any]:
        """Return PFV2 sensor conversion info."""
        return {
            "pressure_format_0": "raw / 10 - 100 (dbar)",
            "pressure_format_2": "raw / 20 - 100 (dbar, high-res)",
            "temperature": "raw / 1000 - 10 (degC)",
            "salinity": "raw / 1000 (PSU)",
            "c1phase_doxy": "raw / 500 - 40",
            "c2phase_doxy": "raw / 500 - 40",
            "temp_doxy": "raw / 1000 - 10 (degC)",
        }
