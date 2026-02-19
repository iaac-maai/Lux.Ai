"""
platform_checks — Schema-compliant check_* functions for the Lux.Ai platform.

Each check_* function receives an IFC file path and returns a dict matching
the D1 database schema (check_results + element_results tables).

Usage:
    from platform_checks import run_all_checks
    results = run_all_checks("building.ifc")
"""

from platform_checks.checks import (
    check_building_areas,
    check_leed_score,
    check_location,
    check_roof_geometry,
    check_solar_production,
)
from platform_checks.run_all import run_all_checks
from platform_checks.schema import validate_check_result

__all__ = [
    "check_location",
    "check_building_areas",
    "check_roof_geometry",
    "check_solar_production",
    "check_leed_score",
    "run_all_checks",
    "validate_check_result",
]
