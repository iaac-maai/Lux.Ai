"""
analyze.py — One function. One IFC file in. Full solar score out.

    from final_pipeline.analyze import analyze_ifc
    result = analyze_ifc("path/to/building.ifc")
    print(result["leed_score"])          # e.g. 100.1
    print(result["total_production"])    # e.g. 26019.96  (kWh/yr)

Also runnable from the command line:
    python -m final_pipeline.analyze  "path/to/building.ifc"
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def analyze_ifc(
    ifc_path: str | Path,
    *,
    lat: float | None = None,
    lon: float | None = None,
    name: str | None = None,
    consumption_kwh_per_m2: float | None = None,
    call_api: bool = True,
) -> dict:
    """
    Analyse one IFC building file and return a full solar production report.

    ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
    │  IFC file    │────▶│  This function   │────▶│  Result dict │
    └──────────────┘     └──────────────────┘     └──────────────┘

    What happens inside
    -------------------
    1. Opens the IFC file
    2. Reads building metadata  (areas, location, orientation)
    3. Extracts 3-D roof geometry and splits it into segments
    4. Queries the NREL PVWatts v8 API for each segment's solar yield
    5. Calculates a LEED-style renewable-energy score

    Parameters
    ----------
    ifc_path : str or Path
        Path to an .ifc file (IFC2x3, IFC4, or IFC4x3).

    lat, lon : float, optional
        Override latitude / longitude if the IFC file has no IfcSite
        coordinates.  When omitted the tool reads them automatically.

    name : str, optional
        Project name shown in the report.  Defaults to the parent folder
        name of the IFC file.

    consumption_kwh_per_m2 : float, optional
        Building energy consumption benchmark in kWh/m²/yr.
        Default = 150 (ASHRAE typical office).
        The tool multiplies this by the floor area to estimate annual
        consumption, which is the denominator in the LEED score.

    call_api : bool, default True
        Set to False to skip the PVWatts API call (useful for offline
        testing).  When False the per-segment kWh values are all 0.

    Returns
    -------
    dict with these keys:

        ok                  bool     – True if the analysis succeeded
        error               str      – error message (only if ok=False)

        # ── Building metadata ──────────────────────────
        project_name        str      – project / folder name
        ifc_file            str      – file name
        window_area_m2      float|None
        floor_area_m2       float|None
        roof_area_m2        float|None  (from property sets)
        true_north_deg      float|None  (compass bearing)
        latitude            float|None
        longitude           float|None

        # ── Roof geometry ──────────────────────────────
        segments            list[dict] – per roof-face data
            Each dict:  id, area, tilt, azimuth, capacity_kw, annual_kwh

        # ── Solar production ───────────────────────────
        total_roof_area_m2  float  – from geometry (sum of segments)
        total_capacity_kw   float
        total_production    float  – kWh/yr
        consumption         float  – estimated kWh/yr
        leed_score          float  – production / consumption × 100

    Example
    -------
    >>> result = analyze_ifc("Sample projects/projects/fzk_house/arc.ifc")
    >>> print(f"Score: {result['leed_score']:.1f}%")
    Score: 100.1%
    """

    # ── Lazy imports (so the module is light to load) ─────────────────────
    from final_pipeline.config import (
        DEFAULT_CONSUMPTION_KWH_PER_M2,
        FALLBACK_CONSUMPTION_KWH,
        PANEL_EFFICIENCY,
    )
    from final_pipeline.ifc_metadata_extractor import (
        Location,
        extract_all,
        extract_location,
    )
    from final_pipeline.ifc_roof_parser import parse_roof_segments
    from final_pipeline.solar_production_engine import run_production_analysis

    ifc_path = Path(ifc_path)
    project = name or ifc_path.parent.name

    # ── 1. Validate input ─────────────────────────────────────────────────
    if not ifc_path.is_file():
        return _error(f"File not found: {ifc_path}")

    # ── 2. Metadata extraction ────────────────────────────────────────────
    try:
        metadata = extract_all(ifc_path)
    except Exception as exc:
        return _error(f"Metadata extraction failed: {exc}")

    # ── 3. Resolve location ───────────────────────────────────────────────
    if lat is not None and lon is not None:
        location = Location(latitude=lat, longitude=lon, name=project)
    elif metadata.get("latitude") is not None and metadata.get("longitude") is not None:
        location = Location(
            latitude=metadata["latitude"],
            longitude=metadata["longitude"],
            name=project,
        )
    else:
        return _error(
            "No location found. The IFC file has no IfcSite coordinates. "
            "Pass lat= and lon= manually."
        )

    # ── 4. Roof geometry ─────────────────────────────────────────────────
    try:
        segments = parse_roof_segments(ifc_path)
    except Exception as exc:
        return _error(f"Roof geometry parsing failed: {exc}")

    if not segments:
        return _error("No roof segments found in this IFC file.")

    # ── 5. Solar production (PVWatts API) ─────────────────────────────────
    if call_api:
        try:
            prod = run_production_analysis(
                segments, location, verbose=False,
            )
        except Exception as exc:
            return _error(f"PVWatts API failed: {exc}")
        segment_results = prod["segments"]
        total_kwh = prod["total_kwh"]
    else:
        # Offline mode — geometry only, no kWh
        segment_results = []
        for seg in segments:
            segment_results.append({
                **seg,
                "capacity_kw": round(seg["area"] * PANEL_EFFICIENCY, 2),
                "annual_kwh": 0.0,
            })
        total_kwh = 0.0

    # ── 6. LEED score ─────────────────────────────────────────────────────
    bench = consumption_kwh_per_m2 or DEFAULT_CONSUMPTION_KWH_PER_M2
    floor = metadata.get("floor_area_m2")
    if floor and floor > 0:
        consumption = floor * bench
    else:
        consumption = FALLBACK_CONSUMPTION_KWH

    leed_score = (total_kwh / consumption * 100) if consumption > 0 else 0.0

    # ── Build result ──────────────────────────────────────────────────────
    total_roof = sum(s["area"] for s in segment_results)
    total_cap = sum(s["capacity_kw"] for s in segment_results)

    return {
        "ok": True,
        "error": None,

        # Metadata
        "project_name": project,
        "ifc_file": ifc_path.name,
        "window_area_m2": metadata.get("window_area_m2"),
        "floor_area_m2": metadata.get("floor_area_m2"),
        "roof_area_m2": metadata.get("roof_area_m2"),
        "true_north_deg": metadata.get("true_north_angle_deg"),
        "latitude": location.latitude,
        "longitude": location.longitude,

        # Segments
        "segments": segment_results,

        # Totals
        "total_roof_area_m2": round(total_roof, 2),
        "total_capacity_kw": round(total_cap, 2),
        "total_production": round(total_kwh, 2),
        "consumption": round(consumption, 2),
        "leed_score": round(leed_score, 1),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _error(msg: str) -> dict:
    """Return a minimal error result."""
    log.error(msg)
    return {"ok": False, "error": msg}


def print_report(result: dict) -> None:
    """Pretty-print an analysis result to the terminal."""
    if not result.get("ok"):
        print(f"\n  ERROR: {result.get('error')}\n")
        return

    print()
    print("=" * 60)
    print(f"  SOLAR ANALYSIS — {result['project_name']}")
    print("=" * 60)

    print(f"\n  FILE        {result['ifc_file']}")
    print(f"  LOCATION    {result['latitude']}, {result['longitude']}")

    print(f"\n  ── Building Metadata ────────────────────────")
    for key, label in [
        ("window_area_m2", "Window area"),
        ("floor_area_m2", "Floor area"),
        ("roof_area_m2", "Roof area (property-set)"),
        ("true_north_deg", "True north"),
    ]:
        val = result.get(key)
        unit = "°" if "north" in key else " m²"
        print(f"     {label:.<30s} {f'{val:,.1f}{unit}' if val is not None else 'N/A'}")

    print(f"\n  ── Roof Segments (geometry) ─────────────────")
    for s in result["segments"]:
        print(f"     {s['id']:15s}  "
              f"Area: {s['area']:>7.1f} m²   "
              f"Tilt: {s['tilt']:>5.1f}°   "
              f"Azimuth: {s['azimuth']:>5.1f}°   "
              f"→ {s['annual_kwh']:>10,.1f} kWh/yr")

    print(f"\n  ── Solar Production ────────────────────────")
    print(f"     Total roof area ......... {result['total_roof_area_m2']:>10,.1f} m²")
    print(f"     System capacity ......... {result['total_capacity_kw']:>10,.1f} kW")
    print(f"     Annual production ....... {result['total_production']:>10,.1f} kWh/yr")

    print(f"\n  ── LEED Score ──────────────────────────────")
    print(f"     Consumption estimate .... {result['consumption']:>10,.0f} kWh/yr")
    print(f"     Renewable production .... {result['total_production']:>10,.0f} kWh/yr")
    print(f"     Score ................... {result['leed_score']:>10.1f} %")

    if result["leed_score"] >= 100:
        print(f"\n     ★  Net-zero energy achieved!")
    elif result["leed_score"] >= 50:
        print(f"\n     ●  Over 50% renewable coverage — strong LEED contribution")
    elif result["leed_score"] >= 10:
        print(f"\n     ○  Moderate renewable contribution")
    else:
        print(f"\n     ·  Low renewable contribution — consider design changes")
    print()


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    """Minimal CLI: python -m final_pipeline.analyze  building.ifc"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    if len(sys.argv) < 2:
        print("Usage:  python -m final_pipeline.analyze  <file.ifc>  [--lat N] [--lon N]")
        sys.exit(1)

    # Simple arg parsing (no argparse needed for this one-shot entry)
    ifc_path = sys.argv[1]
    lat = lon = None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--lat" and i + 1 < len(args):
            lat = float(args[i + 1]); i += 2
        elif args[i] == "--lon" and i + 1 < len(args):
            lon = float(args[i + 1]); i += 2
        else:
            i += 1

    result = analyze_ifc(ifc_path, lat=lat, lon=lon)
    print_report(result)


if __name__ == "__main__":
    main()
