"""
solar_production_engine.py — NREL PVWatts v8 API client.

Calculates annual solar energy production (kWh) for roof segments.
Each segment has its own tilt & azimuth, yielding per-orientation accuracy
for LEED renewable-energy scoring.

    LEED Score = (Σ Segment_kWh / Consumption_total) × 100

Adapted from the original standalone engine to integrate with:
    - ifc_metadata_extractor.py (auto location from IfcSite)
    - ifc_roof_parser.py        (geometry-based roof segments)
"""

from __future__ import annotations

import logging
import time

import requests

from final_pipeline.config import (
    ARRAY_TYPE,
    MODULE_TYPE,
    NREL_API_KEY,
    PANEL_EFFICIENCY,
    PVWATTS_BASE_URL,
    SYSTEM_LOSSES,
)
from final_pipeline.ifc_metadata_extractor import Location

log = logging.getLogger(__name__)


# ── Core calculation ──────────────────────────────────────────────────────────

def calculate_segment_production(
    area: float,
    tilt: float,
    azimuth: float,
    location: Location,
) -> float:
    """
    Query NREL PVWatts v8 for a single roof segment.

    Parameters
    ----------
    area     : segment area in m²
    tilt     : panel tilt in degrees from horizontal
    azimuth  : compass bearing (180° = due south)
    location : site coordinates

    Returns
    -------
    float — annual AC production in kWh, or 0.0 on error.
    """
    system_capacity = area * PANEL_EFFICIENCY  # kW

    params = {
        "api_key": NREL_API_KEY,
        "lat": location.latitude,
        "lon": location.longitude,
        "system_capacity": round(system_capacity, 3),
        "azimuth": azimuth,
        "tilt": tilt,
        "array_type": ARRAY_TYPE,
        "module_type": MODULE_TYPE,
        "losses": SYSTEM_LOSSES,
    }

    try:
        response = requests.get(PVWATTS_BASE_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if "errors" in data and data["errors"]:
            log.error("  PVWatts API error: %s", data["errors"])
            return 0.0

        annual_kwh = float(data["outputs"]["ac_annual"])
        return annual_kwh

    except requests.RequestException as exc:
        log.error("  PVWatts request failed: %s", exc)
        return 0.0
    except (KeyError, TypeError, ValueError) as exc:
        log.error("  Could not parse PVWatts response: %s", exc)
        return 0.0


# ── Batch analysis ────────────────────────────────────────────────────────────

def run_production_analysis(
    segments: list[dict],
    location: Location,
    *,
    rate_limit_sec: float = 1.0,
    verbose: bool = True,
) -> dict:
    """
    Iterate over roof segments, query PVWatts for each, return results.

    Parameters
    ----------
    segments : list of dicts, each with keys: id, area, tilt, azimuth
    location : site coordinates
    rate_limit_sec : pause between API calls (NREL rate-limit)
    verbose : print per-segment progress to stdout

    Returns
    -------
    dict with keys:
        segments  — list of per-segment result dicts (input + capacity_kw + annual_kwh)
        total_kwh — total annual production (float)
        location  — the Location used
    """
    total_kwh = 0.0
    results: list[dict] = []

    if verbose:
        print(f"\n--- Analysing Roof Segments for {location.name} ---")
        print(f"    Site: ({location.latitude}, {location.longitude})")
        print(f"    Panel efficiency: {PANEL_EFFICIENCY}")
        print()

    for i, seg in enumerate(segments):
        capacity_kw = seg["area"] * PANEL_EFFICIENCY
        annual = calculate_segment_production(
            seg["area"], seg["tilt"], seg["azimuth"], location,
        )
        total_kwh += annual

        result = {
            "id": seg["id"],
            "area": seg["area"],
            "tilt": seg["tilt"],
            "azimuth": seg["azimuth"],
            "capacity_kw": round(capacity_kw, 2),
            "annual_kwh": round(annual, 2),
            "global_id": seg.get("global_id"),
            "ifc_type": seg.get("ifc_type"),
        }
        results.append(result)

        if verbose:
            print(
                f"  {seg['id']:>15s}  |  "
                f"Area: {seg['area']:>7.1f} m²  |  "
                f"Tilt: {seg['tilt']:>5.1f}°  |  "
                f"Azimuth: {seg['azimuth']:>5.1f}°  |  "
                f"Capacity: {capacity_kw:>6.1f} kW  |  "
                f"Yield: {annual:>10,.2f} kWh/yr"
            )

        # Respect NREL rate limits
        if i < len(segments) - 1:
            time.sleep(rate_limit_sec)

    if verbose:
        print()
        print("-" * 70)
        print(f"  TOTAL BUILDING PRODUCTION: {total_kwh:>12,.2f} kWh/yr")
        print("-" * 70)

    return {
        "segments": results,
        "total_kwh": round(total_kwh, 2),
        "location": location,
    }
