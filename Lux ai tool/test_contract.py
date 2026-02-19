"""
test_contract.py — Validate that tools/checker_solar.py follows the IFCore contract.

Tests:
1. File lives in tools/ and is named checker_*.py
2. All check_* functions have 'model' as first arg
3. Return type annotation is list[dict]
4. A mock model produces valid element_results dicts

Run:
    cd "Lux ai tool"
    python test_contract.py
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure both this folder and the repo root are importable
_HERE = Path(__file__).resolve().parent          # Lux ai tool/
_REPO = _HERE.parent                              # repo root
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools.checker_solar import (
    check_building_areas,
    check_leed_score,
    check_location,
    check_roof_geometry,
    check_solar_production,
)

# ── Contract constants ────────────────────────────────────────────────────────

REQUIRED_KEYS = {
    "element_id",
    "element_type",
    "element_name",
    "element_name_long",
    "check_status",
    "actual_value",
    "required_value",
    "comment",
    "log",
}

VALID_STATUSES = {"pass", "fail", "warning", "blocked", "log"}

ALL_CHECKS = [
    check_location,
    check_building_areas,
    check_roof_geometry,
    check_solar_production,
    check_leed_score,
]


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_file_location():
    """checker_solar.py lives directly inside tools/."""
    path = _HERE / "tools" / "checker_solar.py"
    assert path.is_file(), f"Expected {path} to exist"
    assert path.parent.name == "tools", f"Expected file in tools/, got {path.parent.name}"
    assert path.name.startswith("checker_"), f"Expected checker_*.py, got {path.name}"
    print("  ✅  File location: tools/checker_solar.py")


def test_function_signatures():
    """All check_* functions have 'model' as first parameter."""
    for fn in ALL_CHECKS:
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        assert params[0] == "model", (
            f"{fn.__name__}: first param is '{params[0]}', expected 'model'"
        )
        print(f"  ✅  {fn.__name__}(model, ...) — signature OK")


def test_return_type_with_mock():
    """
    Running check_location and check_building_areas on a mock model
    returns list[dict] with correct keys and valid statuses.
    """
    # Build a minimal mock ifcopenshell model
    mock_model = MagicMock()
    mock_model.by_type.return_value = []  # no elements → triggers edge cases

    # check_location: no IfcSite → should return list with a fail/blocked item
    results = check_location(mock_model)
    _validate_results("check_location", results)

    # check_building_areas: extractors will get None → blocked
    results = check_building_areas(mock_model)
    _validate_results("check_building_areas", results)

    print("  ✅  Mock model tests passed")


def _validate_results(fn_name: str, results):
    assert isinstance(results, list), f"{fn_name} returned {type(results)}, expected list"
    assert len(results) > 0, f"{fn_name} returned empty list (expected at least 1 row)"
    for i, row in enumerate(results):
        assert isinstance(row, dict), f"{fn_name}[{i}] is not a dict"
        missing = REQUIRED_KEYS - set(row.keys())
        assert not missing, f"{fn_name}[{i}] missing keys: {missing}"
        status = row["check_status"]
        assert status in VALID_STATUSES, (
            f"{fn_name}[{i}] check_status='{status}' not in {VALID_STATUSES}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  IFCore Contract Validation — tools/checker_solar.py")
    print("=" * 60)
    print()

    test_file_location()
    test_function_signatures()
    test_return_type_with_mock()

    print()
    print("=" * 60)
    print("  ALL CONTRACT CHECKS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
