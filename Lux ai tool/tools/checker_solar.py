"""
checker_solar.py — IFCore-compliant check functions for solar energy analysis.

Five check_* functions, each following the platform contract:
    - First arg: model (ifcopenshell.file)
    - Returns: list[dict] — one dict per element, matching element_results schema
    - check_status values: "pass", "fail", "warning", "blocked", "log"

Functions
---------
check_location         — verifies IfcSite has lat/lon
check_building_areas   — verifies window/floor/roof areas present
check_roof_geometry    — verifies 3D roof segments extractable
check_solar_production — runs PVWatts per segment → kWh/yr
check_leed_score       — LEED renewable-energy score (pass ≥ 50 %)
"""

from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path

import ifcopenshell

# ── Ensure package root importable ─────────────────────────────────────────────
# Path: Lux ai tool/tools/checker_solar.py
_HERE = Path(__file__).resolve().parent          # tools/
_PACKAGE = _HERE.parent                          # Lux ai tool/
if str(_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_PACKAGE))

from final_pipeline.config import (
    DEFAULT_CONSUMPTION_KWH_PER_M2,
    FALLBACK_CONSUMPTION_KWH,
    PANEL_EFFICIENCY,
)
from final_pipeline.ifc_metadata_extractor import (
    Location,
    extract_floor_area,
    extract_location,
    extract_roof_area,
    extract_true_north,
    extract_window_area,
    get_area_scale,
    decode_compound_angle,
)
from final_pipeline.ifc_roof_parser import (
    get_roof_elements,
    extract_geometry,
    compute_face_normals,
    cluster_faces_by_normal,
    compute_segment_properties,
    _make_geom_settings,
)
from final_pipeline.solar_production_engine import run_production_analysis

import numpy as np

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

LEED_PASS_THRESHOLD = 50.0  # % — board decision


# ── Helpers ───────────────────────────────────────────────────────────────────

def _result(
    element_id: str | None,
    element_type: str | None,
    element_name: str | None,
    element_name_long: str | None,
    check_status: str,
    actual_value: str | None,
    required_value: str | None,
    comment: str | None = None,
    log_msg: str | None = None,
) -> dict:
    """Build one element_results row matching the platform contract."""
    return {
        "element_id": element_id,
        "element_type": element_type,
        "element_name": element_name or f"{element_type} #{element_id or '?'}",
        "element_name_long": element_name_long or element_name or "",
        "check_status": check_status,
        "actual_value": actual_value,
        "required_value": required_value,
        "comment": comment,
        "log": log_msg,
    }


def _parse_roof_segments_from_model(
    model: ifcopenshell.file,
    angle_tolerance: float = 15.0,
    min_area: float = 1.0,
    apply_true_north: bool = True,
) -> list[dict]:
    """
    Parse roof segments directly from an already-opened ifcopenshell model.

    Equivalent to final_pipeline.ifc_roof_parser.parse_roof_segments but
    accepts a model object instead of a file path (contract compliance).
    """
    elements = get_roof_elements(model)
    if not elements:
        return []

    settings = _make_geom_settings()
    geom_area_scale = 1.0  # world-coords are in metres

    all_normals: list[np.ndarray] = []
    all_areas: list[np.ndarray] = []
    all_face_elem_ids: list[str] = []
    all_face_elem_types: list[str] = []

    for elem in elements:
        geom_data = extract_geometry(elem, settings)
        if geom_data is None:
            continue
        verts, faces = geom_data
        normals, areas = compute_face_normals(verts, faces)
        all_normals.append(normals)
        all_areas.append(areas)
        gid = getattr(elem, "GlobalId", None) or ""
        etype = elem.is_a() if hasattr(elem, "is_a") else ""
        all_face_elem_ids.extend([gid] * len(areas))
        all_face_elem_types.extend([etype] * len(areas))

    if not all_normals:
        return []

    normals = np.vstack(all_normals)
    areas = np.concatenate(all_areas)
    clusters = cluster_faces_by_normal(normals, areas, angle_tolerance)

    segments: list[dict] = []
    seg_idx = 1
    for cluster in clusters:
        props = compute_segment_properties(normals, areas, cluster, geom_area_scale)
        if props["area"] < min_area:
            continue
        props["id"] = f"Roof_Seg_{seg_idx:02d}"

        elem_area: dict[str, float] = {}
        elem_type_map: dict[str, str] = {}
        for fi in cluster:
            gid = all_face_elem_ids[fi]
            elem_area[gid] = elem_area.get(gid, 0.0) + float(areas[fi])
            elem_type_map[gid] = all_face_elem_types[fi]
        dominant_gid = max(elem_area, key=elem_area.get) if elem_area else ""
        props["global_id"] = dominant_gid or None
        props["ifc_type"] = elem_type_map.get(dominant_gid) or None

        segments.append(props)
        seg_idx += 1

    if apply_true_north and segments:
        tn = extract_true_north(model)
        if tn is not None and abs(tn) > 0.01 and abs(tn - 360.0) > 0.01:
            for seg in segments:
                seg["azimuth"] = round((seg["azimuth"] + tn) % 360.0, 1)

    # Cross-validate against property-set roof area
    if segments:
        geom_total = sum(s["area"] for s in segments)
        pset_area = extract_roof_area(model)
        if pset_area is not None and pset_area > 0:
            diff_pct = abs(geom_total - pset_area) / pset_area * 100
            if diff_pct > 20:
                log.warning(
                    "Roof area mismatch: geometry=%.1f m², property-set=%.1f m² (%.0f%% diff)",
                    geom_total, pset_area, diff_pct,
                )

    return segments


# ═══════════════════════════════════════════════════════════════════════════════
# 1. check_location
# ═══════════════════════════════════════════════════════════════════════════════

def check_location(model: ifcopenshell.file) -> list[dict]:
    """
    Verify the IFC file has geographic coordinates (lat/lon) in IfcSite.

    Returns one element_results row per IfcSite found.
    """
    results: list[dict] = []
    sites = model.by_type("IfcSite")

    if not sites:
        results.append(_result(
            element_id=None,
            element_type="IfcSite",
            element_name="(no IfcSite found)",
            element_name_long="No IfcSite entity in model",
            check_status="fail",
            actual_value=None,
            required_value="IfcSite with latitude and longitude",
            comment="Model contains no IfcSite — cannot determine location.",
        ))
        return results

    for site in sites:
        gid = getattr(site, "GlobalId", None)
        name = getattr(site, "Name", None) or f"Site #{site.id()}"

        lat = decode_compound_angle(getattr(site, "RefLatitude", None))
        lon = decode_compound_angle(getattr(site, "RefLongitude", None))

        has_lat = lat is not None
        has_lon = lon is not None

        if has_lat and has_lon:
            status = "pass"
            actual = f"{lat:.6f}°N, {lon:.6f}°E"
            comment = None
        elif has_lat or has_lon:
            status = "fail"
            actual = f"lat={lat}, lon={lon}"
            comment = "Only one coordinate present — both latitude and longitude are required."
        else:
            status = "fail"
            actual = None
            comment = "No geographic coordinates found in IfcSite."

        results.append(_result(
            element_id=gid,
            element_type="IfcSite",
            element_name=name,
            element_name_long=f"{name} (GlobalId: {gid})",
            check_status=status,
            actual_value=actual,
            required_value="Latitude and longitude present",
            comment=comment,
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 2. check_building_areas
# ═══════════════════════════════════════════════════════════════════════════════

def check_building_areas(model: ifcopenshell.file) -> list[dict]:
    """
    Verify window, floor, and roof areas are present in the IFC model.

    Returns one element_results row per area metric (window / floor / roof).
    """
    results: list[dict] = []

    area_checks = [
        ("window_area", "Window area", extract_window_area),
        ("floor_area", "Floor area", extract_floor_area),
        ("roof_area", "Roof area", extract_roof_area),
    ]

    for key, label, extractor in area_checks:
        try:
            val = extractor(model)
        except Exception as exc:
            results.append(_result(
                element_id=None,
                element_type="IfcBuilding",
                element_name=label,
                element_name_long=f"{label} extraction",
                check_status="blocked",
                actual_value=None,
                required_value=f"{label} > 0 m²",
                comment=f"Extraction failed: {exc}",
            ))
            continue

        if val is not None and val > 0:
            status = "pass"
            actual = f"{val:,.2f} m²"
            comment = None
        elif val is None:
            status = "blocked"
            actual = None
            comment = f"{label} property not found in model metadata."
        else:
            status = "fail"
            actual = f"{val} m²"
            comment = f"{label} is zero or negative."

        results.append(_result(
            element_id=None,
            element_type="IfcBuilding",
            element_name=label,
            element_name_long=f"{label} (building-level metric)",
            check_status=status,
            actual_value=actual,
            required_value=f"{label} > 0 m²",
            comment=comment,
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 3. check_roof_geometry
# ═══════════════════════════════════════════════════════════════════════════════

def check_roof_geometry(model: ifcopenshell.file) -> list[dict]:
    """
    Verify that 3D roof segments can be extracted from the IFC geometry.

    Returns one element_results row per roof segment with tilt/azimuth/area.
    """
    results: list[dict] = []

    try:
        segments = _parse_roof_segments_from_model(model)
    except Exception as exc:
        results.append(_result(
            element_id=None,
            element_type="IfcRoof",
            element_name="Roof geometry",
            element_name_long="Roof geometry extraction",
            check_status="blocked",
            actual_value=None,
            required_value="Extractable 3D roof geometry",
            comment=f"Roof parsing failed: {exc}",
        ))
        return results

    if not segments:
        results.append(_result(
            element_id=None,
            element_type="IfcRoof",
            element_name="Roof geometry",
            element_name_long="Roof geometry extraction",
            check_status="fail",
            actual_value="0 segments",
            required_value="At least 1 roof segment",
            comment="No roof segments found — model may lack IfcRoof or IfcSlab(ROOF) geometry.",
        ))
        return results

    total_area = sum(s["area"] for s in segments)

    for seg in segments:
        area_ok = seg["area"] > 0
        tilt_ok = 0 <= seg["tilt"] <= 90
        ok = area_ok and tilt_ok

        seg_name = seg["id"]
        actual = (
            f"area={seg['area']:.1f} m², "
            f"tilt={seg['tilt']:.1f}°, "
            f"azimuth={seg['azimuth']:.1f}°"
        )
        comment = None if ok else (
            f"Invalid segment: area_ok={area_ok}, tilt_ok={tilt_ok}"
        )

        results.append(_result(
            element_id=seg.get("global_id"),
            element_type=seg.get("ifc_type", "IfcRoof"),
            element_name=seg_name,
            element_name_long=f"{seg_name} ({seg['area']:.1f} m² of {total_area:.1f} m² total)",
            check_status="pass" if ok else "fail",
            actual_value=actual,
            required_value="area > 0 m², tilt 0–90°",
            comment=comment,
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 4. check_solar_production
# ═══════════════════════════════════════════════════════════════════════════════

def check_solar_production(
    model: ifcopenshell.file,
    lat: float | None = None,
    lon: float | None = None,
) -> list[dict]:
    """
    Run PVWatts API for each roof segment and check production > 0 kWh.

    Requires location (auto-detected from IfcSite, or pass lat/lon).
    Returns one element_results row per segment with annual_kwh.
    """
    results: list[dict] = []

    # 1. Segments
    try:
        segments = _parse_roof_segments_from_model(model)
    except Exception as exc:
        results.append(_result(
            element_id=None,
            element_type="IfcRoof",
            element_name="Roof geometry",
            element_name_long="Roof geometry for solar analysis",
            check_status="blocked",
            actual_value=None,
            required_value="Extractable roof geometry",
            comment=f"Roof parsing failed: {exc}",
        ))
        return results

    if not segments:
        results.append(_result(
            element_id=None,
            element_type="IfcRoof",
            element_name="Roof segments",
            element_name_long="Roof segments for solar analysis",
            check_status="fail",
            actual_value="0 segments",
            required_value="At least 1 roof segment",
            comment="No roof segments to analyse.",
        ))
        return results

    # 2. Location
    if lat is not None and lon is not None:
        location = Location(latitude=lat, longitude=lon, name="override")
    else:
        location = extract_location(model)
        if location is None:
            results.append(_result(
                element_id=None,
                element_type="IfcSite",
                element_name="Site coordinates",
                element_name_long="IfcSite geographic coordinates",
                check_status="blocked",
                actual_value=None,
                required_value="Latitude and longitude for PVWatts API",
                comment="No coordinates in IfcSite — cannot query PVWatts. Pass lat/lon manually.",
            ))
            return results

    # 3. API call
    try:
        prod = run_production_analysis(segments, location, verbose=False)
    except Exception as exc:
        results.append(_result(
            element_id=None,
            element_type="IfcRoof",
            element_name="PVWatts API",
            element_name_long="PVWatts solar production API call",
            check_status="blocked",
            actual_value=None,
            required_value="Successful PVWatts API response",
            comment=f"PVWatts API error: {exc}",
        ))
        return results

    # 4. Per-segment results
    total_kwh = prod.get("total_kwh", 0.0)
    for seg_result in prod.get("segments", []):
        kwh = seg_result.get("annual_kwh", 0.0)
        seg_id = seg_result.get("id", "?")

        results.append(_result(
            element_id=seg_result.get("global_id"),
            element_type=seg_result.get("ifc_type", "IfcRoof"),
            element_name=seg_id,
            element_name_long=(
                f"{seg_id} — {seg_result.get('area', 0):.1f} m², "
                f"{seg_result.get('capacity_kw', 0):.1f} kW capacity"
            ),
            check_status="pass" if kwh > 0 else "fail",
            actual_value=f"{kwh:,.2f} kWh/yr",
            required_value="> 0 kWh/yr",
            comment=None if kwh > 0 else "Segment produces 0 kWh — check orientation or API response.",
        ))

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 5. check_leed_score
# ═══════════════════════════════════════════════════════════════════════════════

def check_leed_score(
    model: ifcopenshell.file,
    lat: float | None = None,
    lon: float | None = None,
    consumption_kwh_per_m2: float | None = None,
) -> list[dict]:
    """
    Calculate LEED renewable-energy score.
    pass if score >= 50 %, fail otherwise.

    Returns one element_results row with the building-level LEED score.
    """
    results: list[dict] = []

    # 1. Roof segments
    try:
        segments = _parse_roof_segments_from_model(model)
    except Exception as exc:
        results.append(_result(
            element_id=None,
            element_type="IfcBuilding",
            element_name="LEED score",
            element_name_long="LEED renewable-energy score",
            check_status="blocked",
            actual_value=None,
            required_value=f"≥ {LEED_PASS_THRESHOLD:.0f}%",
            comment=f"Roof parsing failed: {exc}",
        ))
        return results

    if not segments:
        results.append(_result(
            element_id=None,
            element_type="IfcBuilding",
            element_name="LEED score",
            element_name_long="LEED renewable-energy score",
            check_status="fail",
            actual_value="0%",
            required_value=f"≥ {LEED_PASS_THRESHOLD:.0f}%",
            comment="No roof segments found — cannot calculate solar production.",
        ))
        return results

    # 2. Location
    if lat is not None and lon is not None:
        location = Location(latitude=lat, longitude=lon, name="override")
    else:
        location = extract_location(model)
        if location is None:
            results.append(_result(
                element_id=None,
                element_type="IfcSite",
                element_name="LEED score",
                element_name_long="LEED renewable-energy score",
                check_status="blocked",
                actual_value=None,
                required_value=f"≥ {LEED_PASS_THRESHOLD:.0f}%",
                comment="No IfcSite coordinates — cannot query PVWatts for LEED calculation.",
            ))
            return results

    # 3. Solar production
    try:
        prod = run_production_analysis(segments, location, verbose=False)
    except Exception as exc:
        results.append(_result(
            element_id=None,
            element_type="IfcBuilding",
            element_name="LEED score",
            element_name_long="LEED renewable-energy score",
            check_status="blocked",
            actual_value=None,
            required_value=f"≥ {LEED_PASS_THRESHOLD:.0f}%",
            comment=f"PVWatts API error: {exc}",
        ))
        return results

    total_kwh = prod.get("total_kwh", 0.0)

    # 4. Consumption estimate
    bench = consumption_kwh_per_m2 or DEFAULT_CONSUMPTION_KWH_PER_M2
    floor = extract_floor_area(model)
    if floor and floor > 0:
        consumption = floor * bench
    else:
        consumption = FALLBACK_CONSUMPTION_KWH

    # 5. LEED score
    score = (total_kwh / consumption * 100) if consumption > 0 else 0.0

    status = "pass" if score >= LEED_PASS_THRESHOLD else "fail"

    results.append(_result(
        element_id=None,
        element_type="IfcBuilding",
        element_name="LEED score",
        element_name_long=(
            f"LEED renewable-energy score — "
            f"production {total_kwh:,.0f} kWh/yr vs consumption {consumption:,.0f} kWh/yr"
        ),
        check_status=status,
        actual_value=f"{score:.1f}%",
        required_value=f"≥ {LEED_PASS_THRESHOLD:.0f}%",
        comment=None if status == "pass" else (
            f"Score {score:.1f}% is below the {LEED_PASS_THRESHOLD:.0f}% threshold."
        ),
    ))

    return results
