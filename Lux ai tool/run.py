#!/usr/bin/env python3
"""
run.py — CLI entry-point for the Lux.Ai solar-analysis tool.

Loads an IFC file and runs the five IFCore-compliant checks defined in
tools/checker_solar.py.  Prints a colour-coded results table and
optionally exports to JSON.

Usage
-----
    python run.py path/to/model.ifc
    python run.py path/to/model.ifc --checks location roof_geometry
    python run.py path/to/model.ifc --lat 48.13 --lon 11.58
    python run.py path/to/model.ifc --output results.json
    python run.py --list-checks
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# ── Resolve package imports ──────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent          # Lux ai tool/
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import ifcopenshell                               # noqa: E402
from tools.checker_solar import (                  # noqa: E402
    check_building_areas,
    check_leed_score,
    check_location,
    check_roof_geometry,
    check_solar_production,
)

# ── Check registry ───────────────────────────────────────────────────────────
# Maps short names → (function, needs_extra_args)
CHECK_REGISTRY: dict[str, dict] = {
    "location": {
        "fn": check_location,
        "description": "Verify IfcSite has latitude + longitude",
        "needs_coords": False,
        "needs_consumption": False,
    },
    "building_areas": {
        "fn": check_building_areas,
        "description": "Verify window, floor, and roof areas are present",
        "needs_coords": False,
        "needs_consumption": False,
    },
    "roof_geometry": {
        "fn": check_roof_geometry,
        "description": "Extract 3D roof segments (tilt, azimuth, area)",
        "needs_coords": False,
        "needs_consumption": False,
    },
    "solar_production": {
        "fn": check_solar_production,
        "description": "Run PVWatts per roof segment → kWh/yr  [internet]",
        "needs_coords": True,
        "needs_consumption": False,
    },
    "leed_score": {
        "fn": check_leed_score,
        "description": "LEED renewable-energy score (pass ≥ 50%)  [internet]",
        "needs_coords": True,
        "needs_consumption": True,
    },
}

# Ordered run sequence (offline checks first, then API-dependent ones)
DEFAULT_ORDER = [
    "location",
    "building_areas",
    "roof_geometry",
    "solar_production",
    "leed_score",
]

# ── ANSI colour helpers ──────────────────────────────────────────────────────

_COLOURS = {
    "pass":    "\033[92m",   # green
    "fail":    "\033[91m",   # red
    "warning": "\033[93m",   # yellow
    "blocked": "\033[90m",   # grey
    "log":     "\033[36m",   # cyan
}
_RESET = "\033[0m"
_BOLD  = "\033[1m"


def _coloured(status: str) -> str:
    c = _COLOURS.get(status, "")
    return f"{c}{status.upper():>7}{_RESET}"


# ── Argument parser ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="Lux.Ai — IFCore solar-analysis checks on an IFC file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python run.py model.ifc\n"
            "  python run.py model.ifc --checks location building_areas\n"
            "  python run.py model.ifc --lat 48.13 --lon 11.58 --output out.json\n"
            "  python run.py --list-checks"
        ),
    )
    p.add_argument(
        "ifc_file",
        nargs="?",
        help="Path to the IFC file to analyse.",
    )
    p.add_argument(
        "--checks",
        nargs="+",
        choices=list(CHECK_REGISTRY),
        metavar="NAME",
        help=f"Run only these checks. Choices: {', '.join(CHECK_REGISTRY)}",
    )
    p.add_argument(
        "--lat",
        type=float,
        default=None,
        help="Override latitude (decimal degrees).",
    )
    p.add_argument(
        "--lon",
        type=float,
        default=None,
        help="Override longitude (decimal degrees).",
    )
    p.add_argument(
        "--consumption",
        type=float,
        default=None,
        dest="consumption_kwh_per_m2",
        help="Override building consumption benchmark (kWh/m²/yr).",
    )
    p.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Export results to a JSON file.",
    )
    p.add_argument(
        "--list-checks",
        action="store_true",
        help="Print available checks and exit.",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )
    return p


# ── List checks ──────────────────────────────────────────────────────────────

def list_checks() -> None:
    print(f"\n{_BOLD}Available checks:{_RESET}\n")
    for name in DEFAULT_ORDER:
        info = CHECK_REGISTRY[name]
        tag = ""
        if info["needs_coords"]:
            tag += " [needs lat/lon]"
        if info["needs_consumption"]:
            tag += " [needs consumption]"
        print(f"  {name:<20} {info['description']}{tag}")
    print()


# ── Run checks ───────────────────────────────────────────────────────────────

def run_checks(
    model: ifcopenshell.file,
    selected: list[str],
    *,
    lat: float | None = None,
    lon: float | None = None,
    consumption_kwh_per_m2: float | None = None,
) -> dict[str, list[dict]]:
    """
    Execute the selected check functions on *model*.

    Returns {check_name: [element_results, ...]}.
    """
    all_results: dict[str, list[dict]] = {}

    for name in selected:
        info = CHECK_REGISTRY[name]
        fn = info["fn"]

        # Build kwargs for checks that accept optional coordinates
        kwargs: dict = {}
        if info["needs_coords"]:
            if lat is not None:
                kwargs["lat"] = lat
            if lon is not None:
                kwargs["lon"] = lon
        if info["needs_consumption"] and consumption_kwh_per_m2 is not None:
            kwargs["consumption_kwh_per_m2"] = consumption_kwh_per_m2

        t0 = time.perf_counter()
        try:
            results = fn(model, **kwargs)
        except Exception as exc:
            results = [{
                "element_id": None,
                "element_type": None,
                "element_name": name,
                "element_name_long": f"Unhandled exception in {name}",
                "check_status": "blocked",
                "actual_value": None,
                "required_value": None,
                "comment": str(exc),
                "log": None,
            }]
        elapsed = time.perf_counter() - t0

        all_results[name] = results
        _print_check_results(name, results, elapsed)

    return all_results


# ── Pretty printing ──────────────────────────────────────────────────────────

def _print_check_results(
    check_name: str, results: list[dict], elapsed: float
) -> None:
    """Print one check's results as a compact table."""
    # Header
    summary_counts = {}
    for r in results:
        s = r.get("check_status", "?")
        summary_counts[s] = summary_counts.get(s, 0) + 1

    tags = "  ".join(f"{_coloured(s)}×{n}" for s, n in summary_counts.items())
    print(f"\n{'─' * 60}")
    print(f"{_BOLD}▸ {check_name}{_RESET}  ({elapsed:.2f}s)  {tags}")
    print(f"{'─' * 60}")

    # Rows
    for r in results:
        status = _coloured(r.get("check_status", "?"))
        name = r.get("element_name", "?")
        actual = r.get("actual_value") or "—"
        required = r.get("required_value") or ""
        comment = r.get("comment") or ""

        print(f"  {status}  {name}")
        print(f"           actual : {actual}")
        if required:
            print(f"           require: {required}")
        if comment:
            print(f"           note   : {comment}")


def _print_summary(all_results: dict[str, list[dict]]) -> None:
    """Print an aggregate pass/fail summary."""
    total = 0
    counts: dict[str, int] = {}
    for rows in all_results.values():
        for r in rows:
            total += 1
            s = r.get("check_status", "?")
            counts[s] = counts.get(s, 0) + 1

    print(f"\n{'═' * 60}")
    print(f"{_BOLD}  SUMMARY{_RESET}")
    print(f"{'═' * 60}")
    print(f"  Checks run : {len(all_results)}")
    print(f"  Total rows : {total}")
    for s in ("pass", "fail", "warning", "blocked", "log"):
        if s in counts:
            print(f"    {_coloured(s)} : {counts[s]}")

    # Overall verdict
    has_fail = counts.get("fail", 0) > 0
    has_blocked = counts.get("blocked", 0) > 0
    if has_fail:
        verdict = f"\033[91m{_BOLD}  RESULT: FAIL{_RESET}"
    elif has_blocked:
        verdict = f"\033[93m{_BOLD}  RESULT: INCOMPLETE (blocked checks){_RESET}"
    else:
        verdict = f"\033[92m{_BOLD}  RESULT: ALL PASS{_RESET}"
    print(f"\n{verdict}")
    print(f"{'═' * 60}\n")


def _export_json(all_results: dict[str, list[dict]], path: str) -> None:
    """Write all results to a JSON file."""
    output = {
        "tool": "Lux.Ai Solar Checker",
        "version": "1.0.0",
        "checks": {},
    }
    for name, rows in all_results.items():
        output["checks"][name] = rows

    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  Results exported to: {path}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # ── List mode ─────────────────────────────────────────────────────────
    if args.list_checks:
        list_checks()
        return 0

    # ── Validate file arg ─────────────────────────────────────────────────
    if not args.ifc_file:
        parser.error("the following argument is required: ifc_file")
        return 1

    ifc_path = Path(args.ifc_file).resolve()
    if not ifc_path.is_file():
        print(f"\033[91mError: File not found: {ifc_path}\033[0m", file=sys.stderr)
        return 1
    if ifc_path.suffix.lower() != ".ifc":
        print(f"\033[93mWarning: File does not have .ifc extension: {ifc_path.name}\033[0m")

    # ── Logging ───────────────────────────────────────────────────────────
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    # ── Open IFC model ────────────────────────────────────────────────────
    print(f"\n{_BOLD}Lux.Ai Solar Checker{_RESET}")
    print(f"{'═' * 60}")
    print(f"  File : {ifc_path.name}")
    print(f"  Path : {ifc_path}")

    try:
        t0 = time.perf_counter()
        model = ifcopenshell.open(str(ifc_path))
        load_time = time.perf_counter() - t0
        print(f"  Schema : {model.schema}")
        print(f"  Loaded in {load_time:.2f}s")
    except Exception as exc:
        print(f"\033[91mError: Cannot open IFC file: {exc}\033[0m", file=sys.stderr)
        return 1

    # ── Determine which checks to run ─────────────────────────────────────
    selected = args.checks or DEFAULT_ORDER
    print(f"  Checks : {', '.join(selected)}")

    # ── Run ───────────────────────────────────────────────────────────────
    all_results = run_checks(
        model,
        selected,
        lat=args.lat,
        lon=args.lon,
        consumption_kwh_per_m2=args.consumption_kwh_per_m2,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    _print_summary(all_results)

    # ── Export ────────────────────────────────────────────────────────────
    if args.output:
        _export_json(all_results, args.output)

    # Return 0 if no failures, 1 otherwise
    has_fail = any(
        r["check_status"] == "fail"
        for rows in all_results.values()
        for r in rows
    )
    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
