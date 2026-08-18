"""
decode_sbd.py
=============
Command-line entry point for argofluxdecoder.

Usage:
  python decode_sbd.py --sbd_dir /path/to/sbd/ --decoder_id 212 --wmo 6901477 --outdir /output/
  python decode_sbd.py --config float_info.json --sbd_dir /path/to/sbd/ --outdir /output/

Reference:
  Based on the Coriolis data processing chain for Argo floats.
  DOI: https://doi.org/10.17882/45589
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running as: python -m argofluxdecoder.decode_sbd
# or directly: python decode_sbd.py
try:
    from argofluxdecoder.core.sbd_reader import collect_all_frames
    from argofluxdecoder.floats.registry import get_decoder, list_supported_decoders
    from argofluxdecoder.output.csv_writer import write_all_csvs
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from argofluxdecoder.core.sbd_reader import collect_all_frames
    from argofluxdecoder.floats.registry import get_decoder, list_supported_decoders
    from argofluxdecoder.output.csv_writer import write_all_csvs


def main():
    parser = argparse.ArgumentParser(
        description="Argo float Iridium SBD decoder (Python/Coriolis)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--sbd_dir", required=True, help="Directory with .sbd files")
    parser.add_argument("--outdir", required=True, help="Output directory for decoded CSVs")
    parser.add_argument("--decoder_id", type=int, help="Coriolis decoder ID (e.g. 212, 219)")
    parser.add_argument("--wmo", default="", help="WMO float number")
    parser.add_argument("--imei", default="", help="Iridium IMEI (filter SBD files)")
    parser.add_argument("--launch_date", default="", help="Launch date: YYYYMMDDHHMMSS")
    parser.add_argument("--launch_lat", type=float, default=0.0, help="Launch latitude")
    parser.add_argument("--launch_lon", type=float, default=0.0, help="Launch longitude")
    parser.add_argument("--config", default="", help="Path to float_info.json (overrides other args)")
    parser.add_argument("--list_decoders", action="store_true", help="List supported decoder IDs and exit")

    args = parser.parse_args()

    # List decoders mode
    if args.list_decoders:
        print("\nSupported decoder IDs:")
        print("-" * 50)
        for did, desc in sorted(list_supported_decoders().items()):
            print(f"  {did:>4d}  {desc}")
        print()
        return 0

    # Build float_info dict
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"ERROR: Config file not found: {args.config}", file=sys.stderr)
            return 1
        with open(config_path, "r", encoding="utf-8") as f:
            float_info = json.load(f)
        decoder_id = int(float_info.get("DECODER_ID", 0))
        imei_filter = float_info.get("PTT", "")
    else:
        if not args.decoder_id:
            print("ERROR: --decoder_id is required (or provide --config)", file=sys.stderr)
            return 1
        decoder_id = args.decoder_id
        imei_filter = args.imei
        float_info = {
            "WMO": args.wmo,
            "PTT": args.imei,
            "DECODER_ID": str(decoder_id),
            "LAUNCH_DATE": args.launch_date,
            "LAUNCH_LAT": str(args.launch_lat),
            "LAUNCH_LON": str(args.launch_lon),
        }

    sbd_dir = Path(args.sbd_dir)
    outdir = Path(args.outdir)

    if not sbd_dir.exists():
        print(f"ERROR: SBD directory not found: {sbd_dir}", file=sys.stderr)
        return 1

    # Get decoder
    try:
        decoder = get_decoder(decoder_id, float_info)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Decoder: {decoder.name}")
    print(f"SBD dir: {sbd_dir}")
    print(f"Output:  {outdir}")
    print()

    # Read all SBD frames
    # PFV2 (IDs 401, 402) uses a directory-based pipeline (SBD reassembly → .hex files)
    # APEX (IDs 1001-1016, 1314) uses directory-based pipeline (.sbd → .msg/.log text)
    # NOVA/DOVA (IDs 2001-2003) use variable-length messages; NKE uses fixed 100-byte frames
    # SBD2 (IDs 301-303) uses 140-byte frames
    PFV2_IDS = (401, 402)
    APEX_IDS = tuple(range(1001, 1017)) + (1314,) + tuple(range(1101, 1133)) + (1321, 1322, 1323)
    NOVA_IDS = (2001, 2002, 2003)
    SBD2_IDS = (301, 302, 303)

    if decoder_id in PFV2_IDS:
        # PFV2: directory-based decode (SBD fragments → .hex → decode)
        print("Pipeline: PFV2 (SBD fragment reassembly)")
        decoder.decode_directory(sbd_dir)
    elif decoder_id in APEX_IDS:
        # APEX: directory-based decode (.sbd → .msg/.log text → parse)
        print("Pipeline: APEX (text-based .msg/.log)")
        decoder.decode_directory(sbd_dir)
    else:
        if decoder_id in NOVA_IDS:
            sbd_mode = "nova"
        elif decoder_id in SBD2_IDS:
            sbd_mode = "sbd2"
        else:
            sbd_mode = "nke"
        frames = collect_all_frames(sbd_dir, imei_filter=imei_filter, mode=sbd_mode)
        print(f"SBD frames read: {len(frames)}")

        if not frames:
            print("WARNING: No SBD frames found.")
            return 0

        # Decode all frames
        for frame in frames:
            decoder.decode_packet(
                pack_type=frame.pack_type,
                payload=frame.payload,
                file_name=frame.file_name,
                file_date=frame.file_date,
            )

    # Get results
    cycles = decoder.get_all_profiles()
    print(f"Cycles decoded: {len(cycles)}")

    if not cycles:
        print("WARNING: No cycles could be decoded.")
        return 0

    # Count measurements
    total_asc = sum(len(c.ctd_ascent) for c in cycles)
    total_desc = sum(len(c.ctd_descent) for c in cycles)
    total_drift = sum(len(c.ctd_drift) for c in cycles)
    total_hyd = sum(len(c.hydraulics) for c in cycles)
    print(f"  Ascent measurements:  {total_asc}")
    print(f"  Descent measurements: {total_desc}")
    print(f"  Drift measurements:   {total_drift}")
    print(f"  Hydraulic actions:    {total_hyd}")
    print()

    # Write outputs
    created = write_all_csvs(cycles, outdir)
    print(f"Output files created in: {outdir}")
    for p in created:
        print(f"  {p.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
