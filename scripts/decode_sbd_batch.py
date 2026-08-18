"""
decode_sbd_batch.py
===================
Batch SBD decoder — direct replacement for the NKE Instrumentation Parser.

Called by the SOCIB Argo GUI with the same workflow:
  python decode_sbd_batch.py --root <float_root> --decoder_id <id> --wmo <wmo>

This script:
  1. Finds all .sbd files in <root>/sbd_raw/
  2. Decodes them using argofluxdecoder (Coriolis-based Python decoder)
  3. Writes output CSVs to <root>/decoded/

Cross-platform: works on Windows, Linux, macOS.

Based on the Coriolis data processing chain for Argo floats:
  DOI: https://doi.org/10.17882/45589
"""

import argparse
import sys
from pathlib import Path

# Allow running from scripts/ directory
sys.path.insert(0, str(Path(__file__).parent))

from argofluxdecoder.core.sbd_reader import collect_all_frames
from argofluxdecoder.floats.registry import get_decoder, list_supported_decoders
from argofluxdecoder.output.csv_writer import write_all_csvs


def find_sbd_dir(root: Path) -> Path:
    """Find the directory containing SBD files."""
    # Preferred: root/sbd_raw/
    sbd_raw = root / "sbd_raw"
    if sbd_raw.is_dir() and list(sbd_raw.glob("*.sbd")):
        return sbd_raw

    # Also check root directly
    if list(root.glob("*.sbd")):
        return root

    # Check nested sbd_raw/sbd_raw (as in user's setup)
    nested = sbd_raw / "sbd_raw"
    if nested.is_dir() and list(nested.glob("*.sbd")):
        return nested

    return sbd_raw


def main():
    parser = argparse.ArgumentParser(
        description="Batch SBD decoder (replaces NKE Instrumentation Parser)",
    )
    parser.add_argument("--root", required=True, help="Float root folder")
    parser.add_argument("--decoder_id", type=int, required=True, help="Coriolis decoder ID")
    parser.add_argument("--wmo", default="", help="WMO float number")
    parser.add_argument("--imei", default="", help="Filter SBD files by IMEI")
    parser.add_argument("--launch_date", default="", help="Launch date YYYYMMDDHHMMSS")
    parser.add_argument("--technical_csv", default="Technical Message.csv")
    parser.add_argument("--ascent_csv", default="Ascent profile CTD Message.csv")

    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: Root folder not found: {root}", file=sys.stderr)
        return 1

    # Find SBD files
    sbd_dir = find_sbd_dir(root)
    sbd_files = list(sbd_dir.glob("*.sbd"))

    if not sbd_files:
        print(f"ERROR: No .sbd files found in {sbd_dir}", file=sys.stderr)
        print(f"  Searched: {root}/sbd_raw/ and {root}/", file=sys.stderr)
        return 1

    print(f"SBD directory: {sbd_dir}")
    print(f"SBD files found: {len(sbd_files)}")
    print(f"Decoder ID: {args.decoder_id}")
    print(f"WMO: {args.wmo or '(not set)'}")
    print()

    # Build float info
    float_info = {
        "WMO": args.wmo,
        "PTT": args.imei,
        "DECODER_ID": str(args.decoder_id),
        "LAUNCH_DATE": args.launch_date,
    }

    # Get decoder
    try:
        decoder = get_decoder(args.decoder_id, float_info)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("\nSupported decoders:", file=sys.stderr)
        for did, desc in sorted(list_supported_decoders().items()):
            print(f"  {did:>4d}  {desc}", file=sys.stderr)
        return 1

    print(f"Using decoder: {decoder.name}")

    # Read and decode
    PFV2_IDS = (401, 402)
    APEX_IDS = tuple(range(1001, 1017)) + (1314,) + tuple(range(1101, 1133)) + (1321, 1322, 1323)
    NOVA_IDS = (2001, 2002, 2003)
    SBD2_IDS = (301, 302, 303)

    if args.decoder_id in PFV2_IDS:
        # PFV2: directory-based decode (SBD fragment reassembly)
        print("Pipeline: PFV2 (SBD fragment reassembly)")
        decoder.decode_directory(sbd_dir)
    elif args.decoder_id in APEX_IDS:
        # APEX: directory-based decode (.sbd → .msg/.log text → parse)
        print("Pipeline: APEX (text-based .msg/.log)")
        decoder.decode_directory(sbd_dir)
    else:
        if args.decoder_id in NOVA_IDS:
            sbd_mode = "nova"
        elif args.decoder_id in SBD2_IDS:
            sbd_mode = "sbd2"
        else:
            sbd_mode = "nke"
        frames = collect_all_frames(sbd_dir, imei_filter=args.imei, mode=sbd_mode)
        print(f"Frames extracted: {len(frames)}")

        if not frames:
            print("WARNING: No valid SBD frames found.")
            return 0

        for frame in frames:
            decoder.decode_packet(
                pack_type=frame.pack_type,
                payload=frame.payload,
                file_name=frame.file_name,
                file_date=frame.file_date,
            )

    cycles = decoder.get_all_profiles()
    print(f"Cycles decoded: {len(cycles)}")

    if not cycles:
        print("WARNING: No cycles decoded.")
        return 0

    # Summary
    total_asc = sum(len(c.ctd_ascent) for c in cycles)
    total_desc = sum(len(c.ctd_descent) for c in cycles)
    total_drift = sum(len(c.ctd_drift) for c in cycles)
    total_hyd = sum(len(c.hydraulics) for c in cycles)
    total_gps = sum(len(c.gps_fixes) for c in cycles)
    print(f"  CTD ascent:    {total_asc} measurements")
    print(f"  CTD descent:   {total_desc} measurements")
    print(f"  CTD drift:     {total_drift} measurements")
    print(f"  Hydraulics:    {total_hyd} actions")
    print(f"  GPS fixes:     {total_gps}")

    # Output to root/decoded/
    outdir = root / "decoded"
    created = write_all_csvs(cycles, outdir)

    print(f"\nOutput written to: {outdir}")
    for p in created:
        print(f"  {p.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
