"""
checks.py — Five check_* functions for the Lux.Ai platform.

Each function takes an IFC file path and returns a dict matching
the D1 database schema (check_results + element_results).

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
import sys
from pathlib import Path

# ── Ensure repo root importable ───────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from platform_checks.schema import LEED_PASS_THRESHOLD, TEAM

from final_pipeline.config import (
    DEFAULT_CONSUMPTION_KWH_PER_M2,
    FALLBACK_CONSUMPTION_KWH,
    PANEL_EFFICIENCY,
)
from final_pipeline.ifc_metadata_extractor import (
    Location,
    extract_all_with_elements,
    extract_location,
    extract_true_north,
)
from final_pipeline.ifc_roof_parser import parse_roof_segments
from final_pipeline.solar_production_engine import run_production_analysis

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _elem(
    element_id: str | None,
    element_type: str | None,
    status: str,
    key: str,
    value,
    raw_data=None,
) -> dict:
    """Build one element_result row."""
    return {
        "element_id": element_id,
        "element_type": element_type,
        "status": status,
        "key": key,
        "value": value,
        "raw": json.dumps(raw_data if raw_data is not None else value, default=str),
    }


def _check(
    check_name: str,
    status: str,
    summary: str,
    element_results: list[dict],
) -> dict:
    """Build one check_result row."""
    return {
        "check_name": check_name,
        "team": TEAM,
        "status": status,
        "summary": summary,
        "has_elements": 1 if element_results else 0,
        "element_results": element_results,
    }


def _aggregate_status(elements: list[dict]) -> str:
    """
    Derive aggregate status from element-level statuses.
    pass   — all elements pass
    fail   — any element fails
    unknown — any element unknown AND none fail
    """
    statuses = {e["status"] for e in elements}
    if "fail" in statuses:
        return "fail"
    if "unknown" in statuses:
        return "unknown"
    if "pass" in statuses:
        return "pass"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. check_location
# ═══════════════════════════════════════════════════════════════════════════════

def check_location(ifc_path: str | Path) -> dict:
    """
    Verify the IFC file has geographic coordinates (lat/lon) in IfcSite.

    Element-level results:
        One row per IfcSite, status = pass if both lat & lon present.
    """
    name = "check_location"
    try:
        data = extract_all_with_elements(ifc_path)
    except Exception as exc:
        return _check(name, "error", f"Extraction failed: {exc}", [])

    if data.get("error"):
        return _check(name, "error", f"Cannot open IFC: {data['error']}", [])

    sites = data["elements"].get("site", [])
    if not sites:
        return _check(name, "fail", "No IfcSite found in model.", [])

    elems: list[dict] = []
    for s in sites:
        has_lat = s.get("latitude") is not None
        has_lon = s.get("longitude") is not None
        if has_lat and has_lon:
            st = "pass"
            summary_fragment = f"{s['latitude']}°N, {s['longitude']}°E"
        elif has_lat or has_lon:
            st = "fail"
            summary_fragment = "partial coordinates"
        else:
            st = "fail"
            summary_fragment = "no coordinates"

        elems.append(_elem(
            element_id=s.get("global_id"),
            element_type="IfcSite",
            status=st,
            key="coordinates",
            value={"latitude": s.get("latitude"), "longitude": s.get("longitude")},
            raw_data=s,
        ))

    agg = _aggregate_status(elems)
    if agg == "pass":
        first = sites[0]
        summary = f"IfcSite has coordinates: {first['latitude']}°N, {first['longitude']}°E"
    else:
        summary = f"{len(sites)} IfcSite(s) checked: coordinates missing or incomplete"

    return _check(name, agg, summary, elems)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. check_building_areas
# ═══════════════════════════════════════════════════════════════════════════════

def check_building_areas(ifc_path: str | Path) -> dict:
    """
    Verify window, floor, and roof areas are present in the IFC model.

    Three area metrics checked at the building level.
    Element-level results: one row per metric (window/floor/roof).
    """
    name = "check_building_areas"
    try:
        data = extract_all_with_elements(ifc_path)
    except Exception as exc:
        return _check(name, "error", f"Extraction failed: {exc}", [])

    if data.get("error"):
        return _check(name, "error", f"Cannot open IFC: {data['error']}", [])

    elems: list[dict] = []
    pass_count = 0
    fail_count = 0
    unknown_count = 0

    area_keys = [
        ("window_area_m2", "window area"),
        ("floor_area_m2", "floor area"),
        ("roof_area_m2", "roof area"),
    ]

    for key, label in area_keys:
        val = data.get(key)
        source_elements = data["elements"].get(key, [])

        if val is not None and val > 0:
            st = "pass"
            pass_count += 1
        elif val is None:
            st = "unknown"
            unknown_count += 1
        else:
            st = "fail"
            fail_count += 1

        # Pick the first source element's GlobalId (if any)
        first_gid = None
        first_type = None
        if source_elements:
            first_gid = source_elements[0].get("global_id")
            first_type = source_elements[0].get("ifc_type")

        elems.append(_elem(
            element_id=first_gid,
            element_type=first_type,
            status=st,
            key=key,
            value=val,
            raw_data={
                "value": val,
                "source_element_count": len(source_elements),
                "source_elements": source_elements[:5],  # cap for readability
            },
        ))

    total = len(area_keys)
    summary = f"{total} area metrics checked: {pass_count} pass, {fail_count} fail, {unknown_count} unknown"
    agg = _aggregate_status(elems)

    return _check(name, agg, summary, elems)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. check_roof_geometry
# ═══════════════════════════════════════════════════════════════════════════════

def check_roof_geometry(ifc_path: str | Path) -> dict:
    """
    Verify that 3D roof segments can be extracted from the IFC geometry.

    Element-level results: one row per roof segment with tilt/azimuth/area.
    Each segment carries the IFC GlobalId of its source roof element.
    """
    name = "check_roof_geometry"
    try:
        segments = parse_roof_segments(ifc_path)
    except Exception as exc:
        return _check(name, "error", f"Roof parsing failed: {exc}", [])

    if not segments:
        return _check(
            name, "fail",
            "No roof segments found — model may lack IfcRoof or IfcSlab(ROOF) geometry.",
            [],
        )

    elems: list[dict] = []
    for seg in segments:
        area_ok = seg["area"] > 0
        tilt_ok = 0 <= seg["tilt"] <= 90
        st = "pass" if (area_ok and tilt_ok) else "fail"
        elems.append(_elem(
            element_id=seg.get("global_id"),
            element_type=seg.get("ifc_type"),
            status=st,
            key="roof_segment",
            value={
                "id": seg["id"],
                "area_m2": seg["area"],
                "tilt_deg": seg["tilt"],
                "azimuth_deg": seg["azimuth"],
            },
            raw_data=seg,
        ))

    agg = _aggregate_status(elems)
    total_area = sum(s["area"] for s in segments)
    summary = (
        f"{len(segments)} roof segment(s) extracted, "
        f"total area {total_area:,.1f} m²"
    )
    return _check(name, agg, summary, elems)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. check_solar_production
# ═══════════════════════════════════════════════════════════════════════════════

def check_solar_production(
    ifc_path: str | Path,
    *,
    lat: float | None = None,
    lon: float | None = None,
) -> dict:
    """
    Run PVWatts API for each roof segment and check production > 0 kWh.

    Requires location (auto-detected from IfcSite, or pass lat/lon).
    Element-level results: one row per segment with annual_kwh.
    """
    name = "check_solar_production"

    # 1. Segments
    try:
        segments = parse_roof_segments(ifc_path)
    except Exception as exc:
        return _check(name, "error", f"Roof parsing failed: {exc}", [])

    if not segments:
        return _check(name, "fail", "No roof segments to analyse.", [])

    # 2. Location
    try:
        import ifcopenshell
        model = ifcopenshell.open(str(ifc_path))
    except Exception as exc:
        return _check(name, "error", f"Cannot open IFC: {exc}", [])

    if lat is not None and lon is not None:
        location = Location(latitude=lat, longitude=lon, name="override")
    else:
        location = extract_location(model)
        if location is None:
            return _check(
                name, "fail",
                "No coordinates in IfcSite — cannot query PVWatts. "
                "Pass lat/lon manually.",
                [],
            )

    # 3. API call
    try:
        prod = run_production_analysis(segments, location, verbose=False)
    except Exception as exc:
        return _check(name, "error", f"PVWatts API error: {exc}", [])

    # 4. Per-segment results
    elems: list[dict] = []
    total_kwh = prod.get("total_kwh", 0.0)
    for seg_result in prod.get("segments", []):
        kwh = seg_result.get("annual_kwh", 0.0)
        st = "pass" if kwh > 0 else "fail"
        elems.append(_elem(
            element_id=seg_result.get("global_id"),
            element_type=seg_result.get("ifc_type"),
            status=st,
            key="annual_kwh",
            value=round(kwh, 2),
            raw_data=seg_result,
        ))

    agg = _aggregate_status(elems)
    summary = (
        f"{len(segments)} segment(s) analysed: "
        f"total production {total_kwh:,.0f} kWh/yr"
    )
    return _check(name, agg, summary, elems)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. check_leed_score
# ═══════════════════════════════════════════════════════════════════════════════

def check_leed_score(
    ifc_path: str | Path,
    *,
    lat: float | None = None,
    lon: float | None = None,
    consumption_kwh_per_m2: float | None = None,
) -> dict:
    """
    Calculate LEED renewable-energy score.
    pass if score >= 50 %, fail otherwise.

    Building-level check — no per-element results (has_elements = 0).
    """
    name = "check_leed_score"

    # Reuse the full pipeline
    try:
        from final_pipeline.analyze import analyze_ifc
    except ImportError as exc:
        return _check(name, "error", f"Import error: {exc}", [])

    try:
        result = analyze_ifc(
            ifc_path, lat=lat, lon=lon,
            consumption_kwh_per_m2=consumption_kwh_per_m2,
            call_api=True,
        )
    except Exception as exc:
        return _check(name, "error", f"Analysis failed: {exc}", [])

    if not result.get("ok"):
        return _check(name, "error", result.get("error", "Unknown error"), [])

    score = result.get("leed_score", 0.0)
    prod = result.get("total_production", 0.0)
    cons = result.get("consumption", 0.0)

    if score >= LEED_PASS_THRESHOLD:
        st = "pass"
    else:
        st = "fail"

    summary = (
        f"LEED score {score:.1f}% — "
        f"production {prod:,.0f} kWh/yr vs consumption {cons:,.0f} kWh/yr "
        f"(threshold {LEED_PASS_THRESHOLD:.0f}%)"
    )

    return _check(name, st, summary, [])
