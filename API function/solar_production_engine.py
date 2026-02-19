"""
Solar Production Engine  [DEPRECATED — use solar_pipeline/ instead]

Superseded by: solar_pipeline/solar_production_engine.py

Original description:
Calculates annual solar energy production for a building by querying the
NREL PVWatts v8 API for each roof segment (slab) individually.

Each segment has its own orientation (tilt & azimuth), so the per-segment
approach yields a far more accurate LEED renewable-energy score than a
single whole-roof average.

    LEED Score = (Σ P_slabs / Consumption_total) × 100

Future integration:
    An IFC parser will populate `roof_segments` automatically from
    IfcSlab / IfcRoof geometry.  For now we use representative dummy data.
"""

import requests
import time
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_KEY = "0zwEIS1adJrx658O3gjQYfI7AprKLjQf4KP420o9"
BASE_URL = "https://developer.nrel.gov/api/pvwatts/v8.json"
PANEL_EFFICIENCY = 0.20  # 1 kW per 5 m²


# ---------------------------------------------------------------------------
# Location dataclass
# ---------------------------------------------------------------------------
@dataclass
class Location:
    """Stores site coordinates and a human-readable project name."""
    latitude: float
    longitude: float
    name: str


# Default site – Barcelona
SITE_LOCATION = Location(
    latitude=41.38,
    longitude=2.17,
    name="Barcelona_Project_Alpha",
)


# ---------------------------------------------------------------------------
# Dummy roof-segment data (will be populated by IFC parser in the future)
# ---------------------------------------------------------------------------
roof_segments: list[dict] = [
    {"id": "Slab_01", "area": 120, "tilt": 30, "azimuth": 180},  # South face
    {"id": "Slab_02", "area": 85,  "tilt": 15, "azimuth": 90},   # East face
    {"id": "Slab_03", "area": 45,  "tilt": 45, "azimuth": 270},  # West face
]


# ---------------------------------------------------------------------------
# Core calculation function
# ---------------------------------------------------------------------------
def calculate_segment_production(
    area: float,
    tilt: float,
    azimuth: float,
    location: Location,
) -> float:
    """
    Query the NREL PVWatts v8 API for a single roof segment and return
    the estimated annual AC energy production in kWh.

    Parameters
    ----------
    area : float
        Segment area in m².
    tilt : float
        Panel tilt angle in degrees from horizontal.
    azimuth : float
        Panel azimuth in degrees (180 = due south).
    location : Location
        Site coordinates and name.

    Returns
    -------
    float
        Annual AC production in kWh, or 0 on error.
    """
    system_capacity = area * PANEL_EFFICIENCY  # kW

    params = {
        "api_key": API_KEY,
        "lat": location.latitude,
        "lon": location.longitude,
        "system_capacity": system_capacity,
        "azimuth": azimuth,
        "tilt": tilt,
        "array_type": 1,   # Fixed – roof mount
        "module_type": 1,   # Premium
        "losses": 14,
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if "errors" in data and data["errors"]:
            print(f"  [API Error] {data['errors']}")
            return 0.0

        annual_kwh = float(data["outputs"]["ac_annual"])
        return annual_kwh

    except requests.RequestException as exc:
        print(f"  [Request Error] {exc}")
        return 0.0
    except (KeyError, TypeError, ValueError) as exc:
        print(f"  [Parse Error] Could not read API response: {exc}")
        return 0.0


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
def run_production_analysis(
    segments: list[dict] | None = None,
    location: Location | None = None,
) -> dict:
    """Iterate over roof segments, query PVWatts, and return results.

    Parameters
    ----------
    segments : list[dict] | None
        Each dict must have keys: id, area, tilt, azimuth.
        Falls back to the module-level ``roof_segments`` dummy data.
    location : Location | None
        Site coordinates.  Falls back to ``SITE_LOCATION``.

    Returns
    -------
    dict with keys:
        "segments"  – list of per-segment result dicts
        "total_kwh" – total annual production (float)
        "location"  – the Location used
    """
    segments = segments or roof_segments
    location = location or SITE_LOCATION
    total_building_production = 0.0
    results: list[dict] = []

    print(f"--- Analyzing Roof Segments for {location.name} ---")
    print(f"    Site: ({location.latitude}, {location.longitude})")
    print(f"    Panel efficiency factor: {PANEL_EFFICIENCY}")
    print()

    for slab in segments:
        capacity_kw = slab["area"] * PANEL_EFFICIENCY
        annual_kwh = calculate_segment_production(
            slab["area"],
            slab["tilt"],
            slab["azimuth"],
            location,
        )
        total_building_production += annual_kwh

        results.append({
            "id": slab["id"],
            "area": slab["area"],
            "tilt": slab["tilt"],
            "azimuth": slab["azimuth"],
            "capacity_kw": capacity_kw,
            "annual_kwh": annual_kwh,
        })

        print(
            f"  {slab['id']:>15s}  |  "
            f"Area: {slab['area']:>7.1f} m²  |  "
            f"Tilt: {slab['tilt']:>5.1f}°  |  "
            f"Azimuth: {slab['azimuth']:>5.1f}°  |  "
            f"Capacity: {capacity_kw:>6.1f} kW  |  "
            f"Yield: {annual_kwh:>10,.2f} kWh/yr"
        )

        # Respect rate limits
        time.sleep(1)

    print()
    print("-" * 70)
    print(f"  TOTAL BUILDING PRODUCTION: {total_building_production:>12,.2f} kWh/yr")
    print("-" * 70)
    print()
    print("  LEED hint:  Score = Total Production / Total Consumption × 100")

    return {
        "segments": results,
        "total_kwh": total_building_production,
        "location": location,
    }


def main() -> None:
    """Run with built-in dummy data (backward-compatible entry point)."""
    run_production_analysis()


if __name__ == "__main__":
    main()
