"""
Run Solar Analysis — Orchestrator  [DEPRECATED — use solar_pipeline/ instead]

This file is superseded by the unified solar_pipeline package:
    python -m solar_pipeline.run_solar_analysis  path/to/model.ifc

Original usage:
    python run_solar_analysis.py                          # uses default IFC
    python run_solar_analysis.py  path/to/model.ifc       # custom IFC file
"""

import os
import sys

# ---------------------------------------------------------------------------
# Resolve imports when running from *this* directory or from the repo root.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from ifc_roof_parser import parse_roof_segments                # noqa: E402
from solar_production_engine import (                           # noqa: E402
    Location,
    run_production_analysis,
)


# ---------------------------------------------------------------------------
# Site configuration  (hardcoded for now — future: read from IfcSite)
# ---------------------------------------------------------------------------
SITE = Location(
    latitude=41.38,
    longitude=2.17,
    name="Barcelona_Project_Alpha",
)

# Default IFC file (relative to this script)
DEFAULT_IFC = os.path.normpath(
    os.path.join(_SCRIPT_DIR, "..", "..", "00_data", "ifc_models",
                 "Ifc4_SampleHouse_1_Roof.ifc")
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ifc_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IFC

    if not os.path.isfile(ifc_path):
        print(f"[Error] IFC file not found: {ifc_path}")
        sys.exit(1)

    # ---- Step 1: Parse the IFC file ----
    print("=" * 70)
    print("  STEP 1 — Parsing IFC roof geometry")
    print("=" * 70)
    segments = parse_roof_segments(ifc_path)

    if not segments:
        print("\n  No roof segments found.  Nothing to analyse.")
        sys.exit(0)

    # ---- Step 2: Run production analysis ----
    print()
    print("=" * 70)
    print("  STEP 2 — Querying NREL PVWatts v8 for each segment")
    print("=" * 70)
    result = run_production_analysis(segments=segments, location=SITE)

    # ---- Step 3: Summary ----
    print()
    print("=" * 70)
    print("  STEP 3 — Summary")
    print("=" * 70)
    total = result["total_kwh"]
    loc = result["location"]
    print(f"  IFC file  : {os.path.basename(ifc_path)}")
    print(f"  Location  : {loc.name} ({loc.latitude}, {loc.longitude})")
    print(f"  Segments  : {len(result['segments'])}")
    print(f"  Total area: "
          f"{sum(s['area'] for s in result['segments']):.2f} m²")
    print(f"  Total prod: {total:,.2f} kWh/yr")
    print()

    # LEED example (assuming a 50,000 kWh/yr consumption baseline)
    example_consumption = 50_000
    leed_pct = (total / example_consumption) * 100 if total > 0 else 0
    print(f"  LEED example (consumption = {example_consumption:,} kWh/yr):")
    print(f"    Score = {total:,.0f} / {example_consumption:,} × 100 "
          f"= {leed_pct:.1f}%")
    print()


if __name__ == "__main__":
    main()
