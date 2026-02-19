"""
run_solar_analysis.py — Unified orchestrator.

Modes
-----
1. Single-file solar analysis (default):
       python -m final_pipeline.run_solar_analysis  path/to/model.ifc

2. Batch metadata scan (no API calls):
       python -m final_pipeline.run_solar_analysis --scan-only --root "Sample projects/projects"

3. Batch solar analysis:
       python -m final_pipeline.run_solar_analysis --batch --root "Sample projects/projects"

Location is auto-extracted from IfcSite.  Override with --lat / --lon / --name
when the IFC file has no geographic coordinates.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# ── Ensure package is importable when run as a script ─────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from final_pipeline.config import (  # noqa: E402
    DEFAULT_CONSUMPTION_KWH_PER_M2,
    DEFAULT_SAMPLE_ROOT,
    FALLBACK_CONSUMPTION_KWH,
)
from final_pipeline.ifc_metadata_extractor import (  # noqa: E402
    Location,
    extract_all,
    extract_floor_area,
    extract_location,
    extract_true_north,
    find_ifc_files,
    open_model,
    print_summary_table,
    scan_all,
)
from final_pipeline.ifc_roof_parser import parse_roof_segments  # noqa: E402
from final_pipeline.solar_production_engine import run_production_analysis  # noqa: E402

log = logging.getLogger(__name__)


# ── Single-file solar analysis ────────────────────────────────────────────────

def run_single(
    ifc_path: Path,
    lat: float | None = None,
    lon: float | None = None,
    name: str | None = None,
) -> dict | None:
    """
    Full pipeline: IFC → metadata + roof geometry → PVWatts → report.

    Returns the production-analysis result dict, or None on failure.
    """
    if not ifc_path.is_file():
        print(f"[Error] IFC file not found: {ifc_path}")
        return None

    project = ifc_path.parent.name

    # ── Step 1: Metadata extraction ───────────────────────────────────────
    print("=" * 70)
    print("  STEP 1 — Extracting IFC metadata")
    print("=" * 70)

    metadata = extract_all(ifc_path)
    for key in ("window_area_m2", "floor_area_m2", "roof_area_m2",
                "true_north_angle_deg", "latitude", "longitude"):
        val = metadata.get(key)
        label = key.replace("_", " ").title()
        print(f"  {label:.<30s} {val if val is not None else 'N/A'}")

    # ── Resolve location ──────────────────────────────────────────────────
    if lat is not None and lon is not None:
        location = Location(latitude=lat, longitude=lon, name=name or project)
        print(f"\n  Location (CLI override): {location.latitude}, {location.longitude}")
    elif metadata.get("latitude") is not None and metadata.get("longitude") is not None:
        location = Location(
            latitude=metadata["latitude"],
            longitude=metadata["longitude"],
            name=name or project,
        )
        print(f"\n  Location (auto from IfcSite): {location.latitude}, {location.longitude}")
    else:
        print("\n  [Error] No location available. Use --lat / --lon or provide an IFC file with IfcSite coordinates.")
        return None

    # ── Step 2: Roof geometry parsing ─────────────────────────────────────
    print()
    print("=" * 70)
    print("  STEP 2 — Parsing roof geometry")
    print("=" * 70)

    segments = parse_roof_segments(ifc_path)
    if not segments:
        print("  No roof segments found. Cannot run solar analysis.")
        return None

    for seg in segments:
        print(f"  {seg['id']}  |  Area: {seg['area']:>7.1f} m²  |  "
              f"Tilt: {seg['tilt']:>5.1f}°  |  Azimuth: {seg['azimuth']:>5.1f}°")

    # ── Step 3: Solar production analysis ─────────────────────────────────
    print()
    print("=" * 70)
    print("  STEP 3 — Querying NREL PVWatts v8")
    print("=" * 70)

    result = run_production_analysis(segments=segments, location=location)

    # ── Step 4: Summary & LEED estimate ───────────────────────────────────
    print()
    print("=" * 70)
    print("  STEP 4 — Summary")
    print("=" * 70)

    total_kwh = result["total_kwh"]
    total_area = sum(s["area"] for s in result["segments"])
    print(f"  IFC file      : {ifc_path.name}")
    print(f"  Project       : {project}")
    print(f"  Location      : {location.name} ({location.latitude}, {location.longitude})")
    print(f"  Segments      : {len(result['segments'])}")
    print(f"  Total roof    : {total_area:,.2f} m²")
    print(f"  Total prod.   : {total_kwh:,.2f} kWh/yr")

    # LEED estimate — use floor area if available
    floor_area = metadata.get("floor_area_m2")
    if floor_area and floor_area > 0:
        consumption = floor_area * DEFAULT_CONSUMPTION_KWH_PER_M2
        print(f"\n  LEED estimate (floor area = {floor_area:,.1f} m² × "
              f"{DEFAULT_CONSUMPTION_KWH_PER_M2} kWh/m²/yr):")
    else:
        consumption = FALLBACK_CONSUMPTION_KWH
        print(f"\n  LEED estimate (assumed consumption = {consumption:,} kWh/yr):")

    leed_pct = (total_kwh / consumption) * 100 if consumption > 0 else 0
    print(f"    Consumption : {consumption:,.0f} kWh/yr")
    print(f"    Production  : {total_kwh:,.0f} kWh/yr")
    print(f"    Score       : {total_kwh:,.0f} / {consumption:,.0f} × 100 = {leed_pct:.1f}%")
    print()

    return result


# ── Batch modes ───────────────────────────────────────────────────────────────

def run_batch_scan(root: Path, output_csv: Path) -> list[dict]:
    """Scan all IFC files and write metadata CSV (no API calls)."""
    results = scan_all(root, output_csv)
    print_summary_table(results)
    return results


def run_batch_solar(root: Path, output_csv: Path) -> None:
    """
    Scan all IFC files, then run solar analysis on each ``arc.ifc``
    that has both roof elements and location data.
    """
    # First do the metadata scan
    results = scan_all(root, output_csv)
    print_summary_table(results)

    # Then run solar on architectural files with location
    print("\n" + "=" * 70)
    print("  BATCH SOLAR ANALYSIS — processing arc.ifc files with location data")
    print("=" * 70 + "\n")

    solar_results: list[dict] = []
    for r in results:
        if r.get("error"):
            continue
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        # Only process architectural files
        if r["ifc_file"] != "arc.ifc":
            continue

        ifc_path = root / r["project_name"] / r["ifc_file"]
        if not ifc_path.is_file():
            continue

        print(f"\n--- {r['project_name']} ---")
        try:
            segments = parse_roof_segments(ifc_path)
            if not segments:
                print(f"  No roof segments — skipped")
                continue

            location = Location(
                latitude=r["latitude"],
                longitude=r["longitude"],
                name=r["project_name"],
            )
            result = run_production_analysis(segments, location, verbose=True)
            solar_results.append({
                "project": r["project_name"],
                "total_kwh": result["total_kwh"],
                "segments": len(result["segments"]),
                "roof_area_m2": sum(s["area"] for s in result["segments"]),
            })
        except Exception as exc:
            log.warning("  Solar analysis failed for %s: %s", r["project_name"], exc)

    if solar_results:
        print("\n\n" + "=" * 70)
        print("  BATCH SOLAR SUMMARY")
        print("=" * 70)
        for sr in solar_results:
            print(f"  {sr['project']:.<30s} {sr['total_kwh']:>10,.0f} kWh/yr  "
                  f"({sr['segments']} segs, {sr['roof_area_m2']:,.0f} m²)")
        total = sum(sr["total_kwh"] for sr in solar_results)
        print(f"\n  Grand total: {total:,.0f} kWh/yr across {len(solar_results)} buildings")


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="final_pipeline",
        description="Unified IFC metadata extraction + solar production analysis.",
    )

    # Modes (mutually exclusive)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--scan-only",
        action="store_true",
        help="Batch metadata scan only (no PVWatts API calls).",
    )
    mode.add_argument(
        "--batch",
        action="store_true",
        help="Batch solar analysis of all arc.ifc files under --root.",
    )

    # Paths
    parser.add_argument(
        "ifc_file",
        nargs="?",
        type=Path,
        default=None,
        help="Path to a single IFC file (for single-file mode).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SAMPLE_ROOT,
        help=f"Root directory for batch scanning (default: {DEFAULT_SAMPLE_ROOT}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT / "ifc_scan_results.csv",
        help="Output CSV file for batch scan results.",
    )

    # Location override
    parser.add_argument("--lat", type=float, default=None, help="Site latitude (override IfcSite).")
    parser.add_argument("--lon", type=float, default=None, help="Site longitude (override IfcSite).")
    parser.add_argument("--name", type=str, default=None, help="Project name for report.")

    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-8s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                _REPO_ROOT / "final_pipeline.log", mode="w", encoding="utf-8",
            ),
        ],
    )

    parser = build_parser()
    args = parser.parse_args()

    if args.scan_only:
        # ── Batch metadata scan ───────────────────────────────────────────
        if not args.root.exists():
            print(f"[Error] Root directory not found: {args.root}")
            sys.exit(1)
        run_batch_scan(args.root, args.output)

    elif args.batch:
        # ── Batch solar analysis ──────────────────────────────────────────
        if not args.root.exists():
            print(f"[Error] Root directory not found: {args.root}")
            sys.exit(1)
        run_batch_solar(args.root, args.output)

    else:
        # ── Single-file solar analysis ────────────────────────────────────
        if args.ifc_file is None:
            parser.print_help()
            print("\n[Error] Provide an IFC file path or use --scan-only / --batch.")
            sys.exit(1)
        result = run_single(
            args.ifc_file,
            lat=args.lat,
            lon=args.lon,
            name=args.name,
        )
        if result is None:
            sys.exit(1)


if __name__ == "__main__":
    main()
