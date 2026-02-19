"""
scan_ifc_models.py
Scans all IFC files in the Sample projects directory and extracts:
  - Window area (m²)
  - Floor area (m²)
  - Roof area (m²)
  - Orientation (true north angle, latitude, longitude)

Output: ifc_scan_results.csv + console table + ifc_scan.log
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
from pathlib import Path
from typing import Optional

import ifcopenshell
import ifcopenshell.util.element as ifc_util
import ifcopenshell.util.unit as ifc_unit_util
from tabulate import tabulate


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "ifc_scan.log", mode="w", encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "project_name",
    "ifc_file",
    "window_area_m2",
    "floor_area_m2",
    "roof_area_m2",
    "true_north_angle_deg",
    "latitude",
    "longitude",
    "error",
]

WINDOW_QSETS = ["Qto_WindowBaseQuantities", "BaseQuantities"]
SPACE_QSETS = ["Qto_SpaceBaseQuantities", "BaseQuantities"]
SLAB_QSETS = ["Qto_SlabBaseQuantities", "BaseQuantities"]
ROOF_QSETS = ["Qto_RoofBaseQuantities", "BaseQuantities"]


# ── Discovery ─────────────────────────────────────────────────────────────────

def find_ifc_files(root_dir: Path) -> list[Path]:
    """Recursively find all .ifc files under root_dir, sorted by path."""
    files = sorted(root_dir.rglob("*.ifc"))
    return files


# ── Unit helpers ──────────────────────────────────────────────────────────────

def get_length_scale(model: ifcopenshell.file) -> float:
    """Return scale factor to convert model length units to metres."""
    try:
        return ifc_unit_util.calculate_unit_scale(model, "LENGTHUNIT")
    except Exception:
        return 1.0


def get_area_scale(model: ifcopenshell.file) -> float:
    """Return scale factor to convert model area units to m²."""
    try:
        return ifc_unit_util.calculate_unit_scale(model, "AREAMEASURE")
    except Exception:
        return 1.0


# ── Quantity extraction helpers ───────────────────────────────────────────────

def get_quantity(element, qset_name: str, qty_name: str) -> Optional[float]:
    """
    Walk IsDefinedBy relationships to find an IfcElementQuantity named
    qset_name and return the numeric value of qty_name within it.
    Returns None if not found.
    """
    for rel in getattr(element, "IsDefinedBy", []):
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        qset = rel.RelatingPropertyDefinition
        if not qset.is_a("IfcElementQuantity"):
            continue
        if qset.Name != qset_name:
            continue
        for qty in qset.Quantities:
            if qty.Name != qty_name:
                continue
            if hasattr(qty, "AreaValue") and qty.AreaValue is not None:
                return float(qty.AreaValue)
            if hasattr(qty, "LengthValue") and qty.LengthValue is not None:
                return float(qty.LengthValue)
            if hasattr(qty, "VolumeValue") and qty.VolumeValue is not None:
                return float(qty.VolumeValue)
    return None


def get_quantity_multi(
    element, qset_names: list[str], qty_name: str
) -> Optional[float]:
    """Try multiple quantity set names in order, return first match."""
    for qset_name in qset_names:
        val = get_quantity(element, qset_name, qty_name)
        if val is not None:
            return val
    return None


# ── Four metric extractors ────────────────────────────────────────────────────

def extract_window_area(model: ifcopenshell.file) -> Optional[float]:
    """
    Total window area in m².
    Strategy 1: Qto_WindowBaseQuantities / Area (IFC4) or BaseQuantities / Area (IFC2x3)
    Strategy 2: OverallHeight × OverallWidth (direct attributes)
    """
    windows = model.by_type("IfcWindow")
    if not windows:
        return None

    area_scale = get_area_scale(model)
    length_scale = get_length_scale(model)
    total = 0.0
    found_any = False

    for win in windows:
        # Strategy 1 — quantity set
        area = get_quantity_multi(win, WINDOW_QSETS, "Area")
        if area is not None:
            total += area * area_scale
            found_any = True
            continue

        # Strategy 2 — direct attributes
        h = getattr(win, "OverallHeight", None)
        w = getattr(win, "OverallWidth", None)
        if h and w:
            total += float(h) * float(w) * (length_scale ** 2)
            found_any = True

    return round(total, 4) if found_any else None


def extract_floor_area(model: ifcopenshell.file) -> Optional[float]:
    """
    Total floor area in m².
    Strategy 1: IfcSpace → Qto_SpaceBaseQuantities / NetFloorArea
    Strategy 2: IfcSpace → Qto_SpaceBaseQuantities / GrossFloorArea
    Strategy 3: IfcSlab[FLOOR/BASESLAB] → Qto_SlabBaseQuantities / NetArea
    """
    area_scale = get_area_scale(model)
    total = 0.0
    found_any = False

    # Strategies 1 & 2 — IfcSpace
    spaces = model.by_type("IfcSpace")
    for space in spaces:
        area = get_quantity_multi(space, SPACE_QSETS, "NetFloorArea")
        if area is None:
            area = get_quantity_multi(space, SPACE_QSETS, "GrossFloorArea")
        if area is not None:
            total += area * area_scale
            found_any = True

    if found_any:
        return round(total, 4)

    # Strategy 3 — IfcSlab FLOOR type
    for slab in model.by_type("IfcSlab"):
        pred = getattr(slab, "PredefinedType", None)
        if pred in ("FLOOR", "BASESLAB"):
            area = get_quantity_multi(slab, SLAB_QSETS, "NetArea")
            if area is None:
                area = get_quantity_multi(slab, SLAB_QSETS, "GrossArea")
            if area is not None:
                total += area * area_scale
                found_any = True

    return round(total, 4) if found_any else None


def extract_roof_area(model: ifcopenshell.file) -> Optional[float]:
    """
    Total roof area in m².
    Strategy 1: IfcRoof → Qto_RoofBaseQuantities / NetArea
    Strategy 2: IfcSlab[ROOF] → Qto_SlabBaseQuantities / NetArea
    Strategy 3: IfcSlab[ROOF] → Qto_SlabBaseQuantities / GrossArea
    """
    area_scale = get_area_scale(model)
    total = 0.0
    found_any = False

    # Strategy 1 — IfcRoof entity
    for roof in model.by_type("IfcRoof"):
        area = get_quantity_multi(roof, ROOF_QSETS, "NetArea")
        if area is None:
            area = get_quantity_multi(roof, ROOF_QSETS, "GrossArea")
        if area is not None:
            total += area * area_scale
            found_any = True

    if found_any:
        return round(total, 4)

    # Strategies 2 & 3 — IfcSlab ROOF type
    for slab in model.by_type("IfcSlab"):
        pred = getattr(slab, "PredefinedType", None)
        if pred == "ROOF":
            area = get_quantity_multi(slab, SLAB_QSETS, "NetArea")
            if area is None:
                area = get_quantity_multi(slab, SLAB_QSETS, "GrossArea")
            if area is not None:
                total += area * area_scale
                found_any = True

    return round(total, 4) if found_any else None


def decode_compound_angle(compound) -> Optional[float]:
    """Convert IfcCompoundPlaneAngleMeasure [deg, min, sec, microsec] to decimal degrees."""
    if compound is None:
        return None
    parts = list(compound)
    if not parts:
        return None
    deg = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    secs = int(parts[2]) if len(parts) > 2 else 0
    microsecs = int(parts[3]) if len(parts) > 3 else 0
    sign = -1 if deg < 0 else 1
    return sign * (abs(deg) + abs(minutes) / 60 + abs(secs) / 3600 + abs(microsecs) / 3_600_000_000)


def extract_orientation(model: ifcopenshell.file) -> dict:
    """
    Extract true north angle (compass bearing, degrees CW from north)
    and geographic coordinates from IfcSite.
    """
    result = {
        "true_north_angle_deg": None,
        "latitude": None,
        "longitude": None,
    }

    # True North from IfcGeometricRepresentationContext
    for ctx in model.by_type("IfcGeometricRepresentationContext"):
        if ctx.is_a("IfcGeometricRepresentationSubContext"):
            continue
        true_north = getattr(ctx, "TrueNorth", None)
        if true_north is not None:
            ratios = true_north.DirectionRatios
            x = float(ratios[0])
            y = float(ratios[1])
            # Angle from +Y axis, measured CCW, converted to clockwise compass bearing
            angle_ccw = math.degrees(math.atan2(x, y))
            result["true_north_angle_deg"] = round((-angle_ccw) % 360.0, 2)
            break

    # Lat/Lon from IfcSite
    sites = model.by_type("IfcSite")
    if sites:
        site = sites[0]
        result["latitude"] = decode_compound_angle(getattr(site, "RefLatitude", None))
        result["longitude"] = decode_compound_angle(getattr(site, "RefLongitude", None))
        if result["latitude"] is not None:
            result["latitude"] = round(result["latitude"], 6)
        if result["longitude"] is not None:
            result["longitude"] = round(result["longitude"], 6)

    return result


# ── Per-file orchestration ────────────────────────────────────────────────────

def process_ifc_file(ifc_path: Path) -> dict:
    """Open one IFC file and extract all metrics."""
    base = {
        "project_name": ifc_path.parent.name,
        "ifc_file": ifc_path.name,
        "window_area_m2": None,
        "floor_area_m2": None,
        "roof_area_m2": None,
        "true_north_angle_deg": None,
        "latitude": None,
        "longitude": None,
        "error": None,
    }

    try:
        model = ifcopenshell.open(str(ifc_path))
    except Exception as exc:
        log.error(f"Cannot open {ifc_path}: {exc}")
        base["error"] = str(exc)
        return base

    schema = model.schema
    log.info(f"  Schema: {schema}")

    extractors = {
        "window_area_m2": extract_window_area,
        "floor_area_m2": extract_floor_area,
        "roof_area_m2": extract_roof_area,
    }
    for key, fn in extractors.items():
        try:
            base[key] = fn(model)
        except Exception as exc:
            log.warning(f"  {key} extraction failed: {exc}")

    try:
        orientation = extract_orientation(model)
        base.update(orientation)
    except Exception as exc:
        log.warning(f"  orientation extraction failed: {exc}")

    return base


# ── Output ────────────────────────────────────────────────────────────────────

def write_csv(results: list[dict], output_path: Path) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    log.info(f"CSV written to: {output_path}")


def print_summary_table(results: list[dict]) -> None:
    display_cols = [
        "project_name", "ifc_file",
        "window_area_m2", "floor_area_m2", "roof_area_m2",
        "true_north_angle_deg", "latitude", "longitude",
    ]
    headers = [
        "Project", "File",
        "Window m²", "Floor m²", "Roof m²",
        "TrueNorth°", "Lat", "Lon",
    ]

    table_data = []
    for r in results:
        row = []
        for col in display_cols:
            val = r.get(col)
            if val is None:
                row.append("N/A")
            elif isinstance(val, float):
                row.append(f"{val:.2f}")
            else:
                row.append(str(val))
        if r.get("error"):
            row[-1] = "ERROR"
        table_data.append(row)

    print("\n" + tabulate(table_data, headers=headers, tablefmt="github"))

    processed = [r for r in results if not r.get("error")]
    errors = [r for r in results if r.get("error")]
    print(f"\nFiles scanned  : {len(results)}")
    print(f"Errors         : {len(errors)}")
    print(f"Window data    : {sum(1 for r in processed if r['window_area_m2'] is not None)}/{len(processed)} files")
    print(f"Floor data     : {sum(1 for r in processed if r['floor_area_m2'] is not None)}/{len(processed)} files")
    print(f"Roof data      : {sum(1 for r in processed if r['roof_area_m2'] is not None)}/{len(processed)} files")
    print(f"Orientation    : {sum(1 for r in processed if r['true_north_angle_deg'] is not None)}/{len(processed)} files")
    if errors:
        print("\nFiles with errors:")
        for r in errors:
            print(f"  {r['project_name']}/{r['ifc_file']}: {r['error']}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan IFC files for window/floor/roof area and orientation."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).parent / "Sample projects" / "projects",
        help="Root directory to search for .ifc files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "ifc_scan_results.csv",
        help="Output CSV file path",
    )
    args = parser.parse_args()

    if not args.root.exists():
        log.error(f"Root directory not found: {args.root}")
        return

    ifc_files = find_ifc_files(args.root)
    log.info(f"Found {len(ifc_files)} IFC file(s) under: {args.root}")

    results = []
    for i, ifc_path in enumerate(ifc_files, 1):
        log.info(f"[{i}/{len(ifc_files)}] {ifc_path.parent.name}/{ifc_path.name}")
        result = process_ifc_file(ifc_path)
        results.append(result)

    write_csv(results, args.output)
    print_summary_table(results)


if __name__ == "__main__":
    main()
