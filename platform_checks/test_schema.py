"""
test_schema.py — Validate that all check_* functions produce
schema-compliant output.

Runs against real IFC files from Sample projects/ and checks:
    1. Structural correctness (required keys, types, valid statuses)
    2. Semantic correctness (GlobalIds are strings, values are sensible)
    3. Edge cases (missing-location, empty model)

Run:
    python platform_checks/test_schema.py
    python platform_checks/test_schema.py --skip-api    (offline, geometry only)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# ── Ensure repo root is importable ────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from platform_checks.schema import (
    TEAM,
    VALID_STATUSES,
    validate_check_result,
)
from platform_checks.checks import (
    check_building_areas,
    check_leed_score,
    check_location,
    check_roof_geometry,
    check_solar_production,
)
from platform_checks.run_all import run_all_checks

logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")


# ── Test infrastructure ───────────────────────────────────────────────────────

class TestReport:
    """Collect pass/fail assertions and print a summary table."""

    def __init__(self):
        self.results: list[tuple[str, str, bool, str]] = []  # (group, test, ok, detail)

    def check(self, group: str, test_name: str, condition: bool, detail: str = ""):
        self.results.append((group, test_name, condition, detail))

    def print_report(self):
        print()
        print("=" * 78)
        print("  SCHEMA VALIDATION TEST REPORT")
        print("=" * 78)

        current_group = None
        pass_count = 0
        fail_count = 0

        for group, test_name, ok, detail in self.results:
            if group != current_group:
                print(f"\n  ── {group} ──")
                current_group = group

            icon = "✅" if ok else "❌"
            print(f"    {icon}  {test_name}", end="")
            if detail:
                print(f"  ({detail})", end="")
            print()

            if ok:
                pass_count += 1
            else:
                fail_count += 1

        print()
        print("-" * 78)
        total = pass_count + fail_count
        print(f"  TOTAL: {pass_count}/{total} passed, {fail_count} failed")
        if fail_count == 0:
            print("  ✅  ALL SCHEMA VALIDATIONS PASSED")
        else:
            print("  ❌  SOME VALIDATIONS FAILED — see above")
        print("-" * 78)
        print()

        return fail_count == 0


# ── Test cases ────────────────────────────────────────────────────────────────

def test_check_schema(
    report: TestReport,
    group: str,
    result: dict,
    expect_status: str | None = None,
    expect_has_elements: int | None = None,
):
    """Run schema validation on one check_result dict."""

    # 1. Structural validation
    errors = validate_check_result(result)
    report.check(
        group, "Schema valid (no structural errors)",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )

    # 2. Team name
    report.check(
        group, f"team == '{TEAM}'",
        result.get("team") == TEAM,
        f"got '{result.get('team')}'",
    )

    # 3. check_name starts with "check_"
    cn = result.get("check_name", "")
    report.check(
        group, "check_name starts with 'check_'",
        cn.startswith("check_"),
        cn,
    )

    # 4. status is valid
    report.check(
        group, f"status in {VALID_STATUSES}",
        result.get("status") in VALID_STATUSES,
        result.get("status"),
    )

    # 5. Expected status (if provided)
    if expect_status is not None:
        report.check(
            group, f"status == '{expect_status}'",
            result.get("status") == expect_status,
            f"got '{result.get('status')}'",
        )

    # 6. has_elements consistency
    if expect_has_elements is not None:
        report.check(
            group, f"has_elements == {expect_has_elements}",
            result.get("has_elements") == expect_has_elements,
            f"got {result.get('has_elements')}",
        )

    # 7. summary is non-empty
    report.check(
        group, "summary is non-empty string",
        isinstance(result.get("summary"), str) and len(result.get("summary", "")) > 0,
    )

    # 8. element_results items have valid GlobalIds (str or None)
    elems = result.get("element_results", [])
    if elems:
        all_ids_ok = all(
            isinstance(e.get("element_id"), (str, type(None)))
            for e in elems
        )
        report.check(
            group, "All element_id values are str or None",
            all_ids_ok,
        )

        # Raw field is valid JSON string
        all_raw_ok = all(isinstance(e.get("raw"), str) for e in elems)
        report.check(
            group, "All 'raw' fields are JSON strings",
            all_raw_ok,
        )

        # Each raw field is parseable JSON
        all_parseable = True
        for e in elems:
            try:
                json.loads(e["raw"])
            except (json.JSONDecodeError, KeyError):
                all_parseable = False
                break
        report.check(
            group, "All 'raw' fields are parseable JSON",
            all_parseable,
        )


def run_tests(skip_api: bool = False):
    """Execute all tests and print the report."""
    report = TestReport()

    # ── Test file: fzk_house (has location, roof, everything) ─────────────
    fzk = _REPO / "Sample projects" / "projects" / "fzk_house" / "arc.ifc"
    if not fzk.is_file():
        print(f"ERROR: Test file not found: {fzk}")
        sys.exit(1)

    print(f"\nTest file: {fzk.relative_to(_REPO)}")
    print("-" * 60)

    # ── Individual check tests ────────────────────────────────────────────

    # 1. check_location
    print("  Running check_location ...")
    r = check_location(fzk)
    test_check_schema(
        report, "check_location (fzk_house)",
        r, expect_status="pass", expect_has_elements=1,
    )

    # 2. check_building_areas
    print("  Running check_building_areas ...")
    r = check_building_areas(fzk)
    test_check_schema(
        report, "check_building_areas (fzk_house)",
        r, expect_has_elements=1,
    )

    # 3. check_roof_geometry
    print("  Running check_roof_geometry ...")
    r = check_roof_geometry(fzk)
    test_check_schema(
        report, "check_roof_geometry (fzk_house)",
        r, expect_status="pass", expect_has_elements=1,
    )
    # Verify segments have GlobalIds
    elems = r.get("element_results", [])
    has_gids = any(e.get("element_id") for e in elems)
    report.check(
        "check_roof_geometry (fzk_house)",
        "At least one segment has a GlobalId",
        has_gids,
    )

    if not skip_api:
        # 4. check_solar_production
        print("  Running check_solar_production ...")
        r = check_solar_production(fzk)
        test_check_schema(
            report, "check_solar_production (fzk_house)",
            r, expect_status="pass", expect_has_elements=1,
        )

        # 5. check_leed_score
        print("  Running check_leed_score ...")
        r = check_leed_score(fzk)
        test_check_schema(
            report, "check_leed_score (fzk_house)",
            r, expect_status="pass", expect_has_elements=0,
        )
    else:
        print("  SKIPPED: check_solar_production (--skip-api)")
        print("  SKIPPED: check_leed_score (--skip-api)")

    # ── run_all_checks integration test ───────────────────────────────────
    print("  Running run_all_checks ...")
    all_results = run_all_checks(fzk, skip_api=skip_api)

    expected_count = 3 if skip_api else 5
    report.check(
        "run_all_checks (integration)",
        f"Returns {expected_count} results",
        len(all_results) == expected_count,
        f"got {len(all_results)}",
    )

    for i, cr in enumerate(all_results):
        errors = validate_check_result(cr)
        report.check(
            "run_all_checks (integration)",
            f"Result [{i}] '{cr.get('check_name', '?')}' is schema-valid",
            len(errors) == 0,
            "; ".join(errors) if errors else "",
        )

    # ── JSON serialisability ──────────────────────────────────────────────
    try:
        json_str = json.dumps(all_results, indent=2, default=str)
        report.check(
            "run_all_checks (integration)",
            "Full output is JSON-serialisable",
            True,
            f"{len(json_str)} bytes",
        )
    except (TypeError, ValueError) as exc:
        report.check(
            "run_all_checks (integration)",
            "Full output is JSON-serialisable",
            False,
            str(exc),
        )

    # ── Edge case: file with potentially missing location ─────────────────
    # ettenheim_gis/city.ifc is a city-level model — may have different behaviour
    edge = _REPO / "Sample projects" / "projects" / "ettenheim_gis" / "city.ifc"
    if edge.is_file():
        print(f"\n  Edge case: {edge.relative_to(_REPO)}")
        r = check_location(edge)
        test_check_schema(
            report, "check_location (ettenheim_gis - edge case)",
            r,
        )
        r2 = check_roof_geometry(edge)
        test_check_schema(
            report, "check_roof_geometry (ettenheim_gis - edge case)",
            r2,
        )

    # ── Print report ──────────────────────────────────────────────────────
    all_passed = report.print_report()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    skip = "--skip-api" in sys.argv
    run_tests(skip_api=skip)
