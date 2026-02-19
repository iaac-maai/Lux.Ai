"""
ifc_metadata_extractor.py — Alias-driven IFC property-set / quantity-set
metadata extraction.

Reads key_aliases.json to resolve exporter-specific naming differences
(Archicad, Revit, IFC2x3, IFC4, GSA, BOMA, …) and extracts:
    window_area, floor_area, roof_area, true_north_angle, latitude, longitude

Also exposes helpers consumed by the roof-geometry parser and the solar
production engine (extract_location, extract_true_north).
"""

from __future__ import annotations

import csv
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import ifcopenshell
import ifcopenshell.util.unit as ifc_unit_util

from final_pipeline.config import CSV_COLUMNS, KEY_ALIASES_PATH

# ── Logging ───────────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ── Load alias map once at module import ──────────────────────────────────────

def _load_aliases(path: Path | None = None) -> dict:
    p = path or KEY_ALIASES_PATH
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning("key_aliases.json not found at %s — using empty alias map", p)
        return {}

ALIASES: dict = _load_aliases()

# ── Location dataclass (re-used by solar engine) ─────────────────────────────

@dataclass
class Location:
    """Site coordinates extracted from IfcSite."""
    latitude: float
    longitude: float
    name: str = ""


# ── Unit helpers ──────────────────────────────────────────────────────────────

def get_length_scale(model: ifcopenshell.file) -> float:
    """Scale factor: model length units → metres."""
    try:
        return ifc_unit_util.calculate_unit_scale(model, "LENGTHUNIT")
    except Exception:
        return 1.0


def get_area_scale(model: ifcopenshell.file) -> float:
    """Scale factor: model area units → m²."""
    try:
        return ifc_unit_util.calculate_unit_scale(model, "AREAMEASURE")
    except Exception:
        return 1.0


# ── Low-level quantity / property getters ─────────────────────────────────────

def get_quantity(element, qset_name: str, qty_name: str) -> Optional[float]:
    """
    Walk IsDefinedBy → IfcRelDefinesByProperties → IfcElementQuantity
    and return the numeric value of *qty_name* inside *qset_name*.
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
            for attr in ("AreaValue", "LengthValue", "VolumeValue", "CountValue"):
                v = getattr(qty, attr, None)
                if v is not None:
                    return float(v)
    return None


def get_property(element, pset_name: str, prop_name: str) -> Optional[float]:
    """
    Walk IsDefinedBy → IfcRelDefinesByProperties → IfcPropertySet
    and return the nominal value of *prop_name* inside *pset_name*.
    """
    for rel in getattr(element, "IsDefinedBy", []):
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pset = rel.RelatingPropertyDefinition
        if not pset.is_a("IfcPropertySet"):
            continue
        if pset.Name != pset_name:
            continue
        for prop in pset.HasProperties:
            if prop.Name != prop_name:
                continue
            nv = getattr(prop, "NominalValue", None)
            if nv is not None:
                try:
                    return float(nv.wrappedValue)
                except (TypeError, ValueError, AttributeError):
                    return None
    return None


def get_quantity_multi(
    element, qset_names: list[str], qty_name: str
) -> Optional[float]:
    """Try multiple quantity-set names in priority order; return first match."""
    for qset_name in qset_names:
        val = get_quantity(element, qset_name, qty_name)
        if val is not None:
            return val
    return None


# ── Alias-driven generic extractor ───────────────────────────────────────────

def _extract_by_alias(
    model: ifcopenshell.file,
    canonical_key: str,
    area_scale: float,
    length_scale: float,
) -> Optional[float]:
    """
    Use the alias chain in key_aliases.json to extract a value.
    Iterates strategies in priority order; returns the first that yields data.
    """
    strategies = ALIASES.get(canonical_key, [])
    if not strategies:
        return None

    for strat in strategies:
        entity_type = strat.get("entity")
        source = strat.get("source")  # "qset", "pset", or "attr"

        # ── Attribute-based strategies ────────────────────────────────────
        if source == "attr":
            if strat.get("op") == "multiply":
                # e.g. OverallHeight × OverallWidth for windows
                elements = model.by_type(entity_type)
                total = 0.0
                found = False
                for elem in elements:
                    vals = []
                    for k in strat["keys"]:
                        v = getattr(elem, k, None)
                        if v is not None:
                            vals.append(float(v))
                    if len(vals) == len(strat["keys"]):
                        product = 1.0
                        for v in vals:
                            product *= v
                        total += product * (length_scale ** len(strat["keys"]))
                        found = True
                if found:
                    return round(total, 4)
            # Other attr strategies (TrueNorth, RefLatitude) are handled
            # by dedicated functions, not this generic extractor.
            continue

        # ── Quantity-set or property-set strategies ───────────────────────
        predefined_type = strat.get("predefined_type")
        set_name = strat["set_name"]
        key = strat["key"]

        elements = model.by_type(entity_type)
        total = 0.0
        found = False
        for elem in elements:
            # Filter by PredefinedType if required
            if predefined_type:
                pt = getattr(elem, "PredefinedType", None)
                if pt != predefined_type:
                    continue

            if source == "qset":
                val = get_quantity(elem, set_name, key)
            elif source == "pset":
                val = get_property(elem, set_name, key)
            else:
                continue

            if val is not None:
                total += val * area_scale
                found = True

        if found:
            return round(total, 4)

    return None


# ── Specific extractors ──────────────────────────────────────────────────────

def extract_window_area(model: ifcopenshell.file) -> Optional[float]:
    """Total window area in m², using alias fallback chain."""
    return _extract_by_alias(model, "window_area", get_area_scale(model), get_length_scale(model))


def extract_floor_area(model: ifcopenshell.file) -> Optional[float]:
    """Total floor area in m², using alias fallback chain."""
    return _extract_by_alias(model, "floor_area", get_area_scale(model), get_length_scale(model))


def extract_roof_area(model: ifcopenshell.file) -> Optional[float]:
    """Total roof area (metadata) in m², using alias fallback chain."""
    return _extract_by_alias(model, "roof_area", get_area_scale(model), get_length_scale(model))


def decode_compound_angle(compound) -> Optional[float]:
    """Convert IfcCompoundPlaneAngleMeasure [deg, min, sec, µsec] → decimal degrees."""
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
    return sign * (
        abs(deg) + abs(minutes) / 60 + abs(secs) / 3600 + abs(microsecs) / 3_600_000_000
    )


def extract_true_north(model: ifcopenshell.file) -> Optional[float]:
    """
    True north compass bearing (degrees clockwise from +Y axis).

    Returns None if the model lacks TrueNorth data.
    """
    for ctx in model.by_type("IfcGeometricRepresentationContext"):
        if ctx.is_a("IfcGeometricRepresentationSubContext"):
            continue
        true_north = getattr(ctx, "TrueNorth", None)
        if true_north is not None:
            ratios = true_north.DirectionRatios
            x, y = float(ratios[0]), float(ratios[1])
            angle_ccw = math.degrees(math.atan2(x, y))
            return round((-angle_ccw) % 360.0, 2)
    return None


def extract_location(model: ifcopenshell.file, project_name: str = "") -> Optional[Location]:
    """
    Extract lat/lon from IfcSite and return a Location dataclass.

    Returns None if the model has no geographic coordinates.
    """
    sites = model.by_type("IfcSite")
    if not sites:
        return None
    site = sites[0]
    lat = decode_compound_angle(getattr(site, "RefLatitude", None))
    lon = decode_compound_angle(getattr(site, "RefLongitude", None))
    if lat is None or lon is None:
        return None
    return Location(
        latitude=round(lat, 6),
        longitude=round(lon, 6),
        name=project_name or getattr(site, "Name", None) or "",
    )


def extract_orientation(model: ifcopenshell.file) -> dict:
    """
    Extract true north angle + latitude + longitude.

    Returns dict compatible with the original scan_ifc_models.py output.
    """
    result = {
        "true_north_angle_deg": extract_true_north(model),
        "latitude": None,
        "longitude": None,
    }
    loc = extract_location(model)
    if loc is not None:
        result["latitude"] = loc.latitude
        result["longitude"] = loc.longitude
    return result


# ── Per-file orchestration ────────────────────────────────────────────────────

def extract_all(ifc_path: Path | str) -> dict:
    """
    Open one IFC file and extract all metadata metrics.

    Returns a dict with keys matching CSV_COLUMNS.
    """
    ifc_path = Path(ifc_path)
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
        log.error("Cannot open %s: %s", ifc_path, exc)
        base["error"] = str(exc)
        return base

    log.info("  Schema: %s", model.schema)

    extractors = {
        "window_area_m2": extract_window_area,
        "floor_area_m2": extract_floor_area,
        "roof_area_m2": extract_roof_area,
    }
    for key, fn in extractors.items():
        try:
            base[key] = fn(model)
        except Exception as exc:
            log.warning("  %s extraction failed: %s", key, exc)

    try:
        orientation = extract_orientation(model)
        base.update(orientation)
    except Exception as exc:
        log.warning("  Orientation extraction failed: %s", exc)

    return base


def extract_all_with_elements(ifc_path: Path | str) -> dict:
    """
    Like extract_all(), but also returns per-metric source element GlobalIds
    for platform schema compliance.

    Returns a dict with the same keys as extract_all(), plus:
        "elements": {
            "window_area_m2": [{"global_id": str, "ifc_type": str, "value": float}, ...],
            "floor_area_m2":  [...],
            "roof_area_m2":   [...],
            "site":           [{"global_id": str, "ifc_type": "IfcSite",
                                "latitude": float, "longitude": float}],
        }
    """
    ifc_path = Path(ifc_path)
    base = extract_all(ifc_path)
    base["elements"] = {
        "window_area_m2": [],
        "floor_area_m2": [],
        "roof_area_m2": [],
        "site": [],
    }
    if base.get("error"):
        return base

    try:
        model = ifcopenshell.open(str(ifc_path))
    except Exception:
        return base

    area_scale = get_area_scale(model)
    length_scale = get_length_scale(model)

    # ── Window elements ────────────────────────────────────────────────────
    for win in model.by_type("IfcWindow"):
        h = getattr(win, "OverallHeight", None)
        w = getattr(win, "OverallWidth", None)
        if h is not None and w is not None:
            val = float(h) * float(w) * (length_scale ** 2)
            base["elements"]["window_area_m2"].append({
                "global_id": getattr(win, "GlobalId", None),
                "ifc_type": win.is_a(),
                "value": round(val, 4),
            })

    # ── Floor elements (IfcSlab FLOOR or quantity sets) ────────────────────
    for slab in model.by_type("IfcSlab"):
        pt = getattr(slab, "PredefinedType", None)
        if pt == "FLOOR" or pt is None:
            val = get_quantity(slab, "Qto_SlabBaseQuantities", "GrossArea")
            if val is not None:
                base["elements"]["floor_area_m2"].append({
                    "global_id": getattr(slab, "GlobalId", None),
                    "ifc_type": slab.is_a(),
                    "value": round(val * area_scale, 4),
                })

    # ── Roof elements ──────────────────────────────────────────────────────
    for roof in model.by_type("IfcRoof"):
        base["elements"]["roof_area_m2"].append({
            "global_id": getattr(roof, "GlobalId", None),
            "ifc_type": roof.is_a(),
            "value": None,  # aggregate value — not per-element
        })
    for slab in model.by_type("IfcSlab"):
        pt = getattr(slab, "PredefinedType", None)
        if pt == "ROOF":
            val = get_quantity(slab, "Qto_SlabBaseQuantities", "GrossArea")
            base["elements"]["roof_area_m2"].append({
                "global_id": getattr(slab, "GlobalId", None),
                "ifc_type": slab.is_a(),
                "value": round(val * area_scale, 4) if val else None,
            })

    # ── Site elements ──────────────────────────────────────────────────────
    for site in model.by_type("IfcSite"):
        lat = decode_compound_angle(getattr(site, "RefLatitude", None))
        lon = decode_compound_angle(getattr(site, "RefLongitude", None))
        base["elements"]["site"].append({
            "global_id": getattr(site, "GlobalId", None),
            "ifc_type": "IfcSite",
            "latitude": round(lat, 6) if lat is not None else None,
            "longitude": round(lon, 6) if lon is not None else None,
        })

    return base


def open_model(ifc_path: Path | str) -> ifcopenshell.file:
    """Open an IFC file and return the ifcopenshell model handle."""
    return ifcopenshell.open(str(ifc_path))


# ── Batch scanning ───────────────────────────────────────────────────────────

def find_ifc_files(root_dir: Path) -> list[Path]:
    """Recursively find all .ifc files under root_dir, sorted."""
    return sorted(root_dir.rglob("*.ifc"))


def scan_all(
    root_dir: Path,
    output_csv: Path | None = None,
) -> list[dict]:
    """
    Scan all IFC files under *root_dir* and return a list of result dicts.
    Optionally writes results to a CSV file.
    """
    ifc_files = find_ifc_files(root_dir)
    log.info("Found %d IFC file(s) under: %s", len(ifc_files), root_dir)

    results: list[dict] = []
    for i, ifc_path in enumerate(ifc_files, 1):
        log.info("[%d/%d] %s/%s", i, len(ifc_files), ifc_path.parent.name, ifc_path.name)
        result = extract_all(ifc_path)
        results.append(result)

    if output_csv:
        _write_csv(results, output_csv)

    return results


def _write_csv(results: list[dict], output_path: Path) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    log.info("CSV written to: %s", output_path)


def print_summary_table(results: list[dict]) -> None:
    """Print a tabulate-formatted summary to stdout."""
    try:
        from tabulate import tabulate
    except ImportError:
        log.warning("tabulate not installed — skipping summary table")
        return

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
