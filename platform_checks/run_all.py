"""
run_all.py — Run all check_* functions on an IFC file.

Returns a list of check_result dicts matching the D1 database schema.
Also runnable from the command line:

    python -m platform_checks.run_all  "path/to/building.ifc"
    python -m platform_checks.run_all  "building.ifc"  --lat 48.14 --lon 11.58
    python -m platform_checks.run_all  "building.ifc"  --skip-api
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Ensure repo root is importable
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from platform_checks.checks import (
    check_building_areas,
    check_leed_score,
    check_location,
    check_roof_geometry,
    check_solar_production,
)

log = logging.getLogger(__name__)


def run_all_checks(
    ifc_path: str | Path,
    *,
    lat: float | None = None,
    lon: float | None = None,
    consumption_kwh_per_m2: float | None = None,
    skip_api: bool = False,
) -> list[dict]:
    """
    Run every check_* function on *ifc_path* and return a list of
    check_result dicts ready for the D1 database.

    Parameters
    ----------
    ifc_path : path to an .ifc file
    lat, lon : optional coordinate overrides
    consumption_kwh_per_m2 : LEED energy benchmark override
    skip_api : if True, skip check_solar_production and check_leed_score
               (both require internet)

    Returns
    -------
    list[dict] — one dict per check, ordered by execution
    """
    ifc_path = Path(ifc_path)
    results: list[dict] = []

    # 1. Location (no API)
    log.info("[1/5] check_location")
    results.append(check_location(ifc_path))

    # 2. Building areas (no API)
    log.info("[2/5] check_building_areas")
    results.append(check_building_areas(ifc_path))

    # 3. Roof geometry (no API)
    log.info("[3/5] check_roof_geometry")
    results.append(check_roof_geometry(ifc_path))

    if skip_api:
        log.info("[4/5] check_solar_production — SKIPPED (--skip-api)")
        log.info("[5/5] check_leed_score — SKIPPED (--skip-api)")
    else:
        # 4. Solar production (API)
        log.info("[4/5] check_solar_production")
        results.append(check_solar_production(ifc_path, lat=lat, lon=lon))

        # 5. LEED score (API)
        log.info("[5/5] check_leed_score")
        results.append(check_leed_score(
            ifc_path, lat=lat, lon=lon,
            consumption_kwh_per_m2=consumption_kwh_per_m2,
        ))

    return results


# ── Pretty printer ────────────────────────────────────────────────────────────

def print_results(results: list[dict]) -> None:
    """Print a human-readable summary of all check results."""
    print()
    print("=" * 70)
    print("  PLATFORM CHECK RESULTS")
    print("=" * 70)

    for r in results:
        status_icon = {
            "pass": "✅", "fail": "❌", "error": "⚠️", "unknown": "❓",
        }.get(r["status"], "?")

        print(f"\n  {status_icon}  {r['check_name']}")
        print(f"      Status  : {r['status']}")
        print(f"      Team    : {r['team']}")
        print(f"      Summary : {r['summary']}")
        print(f"      Elements: {len(r['element_results'])} row(s)")

        for e in r["element_results"]:
            e_icon = {"pass": "✅", "fail": "❌", "unknown": "❓"}.get(
                e["status"], "?"
            )
            eid = e["element_id"] or "—"
            print(f"        {e_icon}  [{e['key']}] {eid} = {e['value']}")

    print()
    print("=" * 70)
    passed = sum(1 for r in results if r["status"] == "pass")
    total = len(results)
    print(f"  {passed}/{total} checks passed")
    print("=" * 70)
    print()


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    if len(sys.argv) < 2:
        print("Usage:  python -m platform_checks.run_all  <file.ifc>"
              "  [--lat N] [--lon N] [--skip-api] [--json]")
        sys.exit(1)

    ifc_path = sys.argv[1]
    lat = lon = None
    skip_api = False
    as_json = False
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--lat" and i + 1 < len(args):
            lat = float(args[i + 1]); i += 2
        elif args[i] == "--lon" and i + 1 < len(args):
            lon = float(args[i + 1]); i += 2
        elif args[i] == "--skip-api":
            skip_api = True; i += 1
        elif args[i] == "--json":
            as_json = True; i += 1
        else:
            i += 1

    results = run_all_checks(
        ifc_path, lat=lat, lon=lon, skip_api=skip_api,
    )

    if as_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print_results(results)


if __name__ == "__main__":
    main()
