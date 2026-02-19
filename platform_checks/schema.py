"""
schema.py — D1 database schema definitions and validation.

Maps to the four platform tables:
    users            — (handled by platform, not by check functions)
    projects         — (handled by platform, not by check functions)
    check_results    — one row per check_* function run
    element_results  — one row per element checked

This module provides:
    - VALID_STATUSES: allowed status values
    - TEAM: team identifier
    - LEED_PASS_THRESHOLD: score % for check_leed_score to return "pass"
    - validate_check_result(): structural validator
    - validate_element_result(): structural validator
"""

from __future__ import annotations

# ── Constants ─────────────────────────────────────────────────────────────────

TEAM = "Lux.ai"

VALID_STATUSES = {"pass", "fail", "unknown", "error"}

# Board-level check_results.status values while job is in progress:
RUNNING_STATUS = "running"

# LEED renewable-energy threshold (board decision: >= 50 % to pass)
LEED_PASS_THRESHOLD = 50.0


# ── check_results row ────────────────────────────────────────────────────────

CHECK_RESULT_REQUIRED_KEYS = {
    "check_name",       # str  — function name, e.g. "check_location"
    "team",             # str  — repo folder name, e.g. "Lux.ai"
    "status",           # str  — "pass" | "fail" | "error" | "unknown"
    "summary",          # str  — human-readable, e.g. "14 doors checked: 12 pass, 2 fail"
    "has_elements",     # int  — 1 if element_results present, 0 otherwise
    "element_results",  # list — list of element_result dicts (may be empty)
}

# ── element_results row ──────────────────────────────────────────────────────

ELEMENT_RESULT_REQUIRED_KEYS = {
    "element_id",       # str | None — IFC GlobalId (22-char base64 when available)
    "element_type",     # str | None — IFC entity type, e.g. "IfcSite"
    "status",           # str  — "pass" | "fail" | "unknown"
    "key",              # str  — what was checked, e.g. "latitude"
    "value",            # any  — the extracted value (float, str, None, …)
    "raw",              # str  — JSON-serialised original data for debugging
}


# ── Validators ────────────────────────────────────────────────────────────────

def validate_check_result(result: dict) -> list[str]:
    """
    Validate a check_result dict against the D1 schema.

    Returns a list of error strings.  Empty list = valid.
    """
    errors: list[str] = []

    # Required keys
    for key in CHECK_RESULT_REQUIRED_KEYS:
        if key not in result:
            errors.append(f"Missing required key: '{key}'")

    if errors:
        return errors  # can't validate further

    # Types
    if not isinstance(result["check_name"], str) or not result["check_name"]:
        errors.append("'check_name' must be a non-empty string")

    if not isinstance(result["team"], str) or not result["team"]:
        errors.append("'team' must be a non-empty string")

    if result["status"] not in VALID_STATUSES:
        errors.append(
            f"'status' must be one of {VALID_STATUSES}, got '{result['status']}'"
        )

    if not isinstance(result["summary"], str):
        errors.append("'summary' must be a string")

    if result["has_elements"] not in (0, 1):
        errors.append(f"'has_elements' must be 0 or 1, got {result['has_elements']}")

    if not isinstance(result["element_results"], list):
        errors.append("'element_results' must be a list")
    else:
        # has_elements consistency
        if result["has_elements"] == 1 and len(result["element_results"]) == 0:
            errors.append("'has_elements' is 1 but 'element_results' is empty")
        if result["has_elements"] == 0 and len(result["element_results"]) > 0:
            errors.append(
                "'has_elements' is 0 but 'element_results' is non-empty"
            )

        # Validate each element_result
        for i, elem in enumerate(result["element_results"]):
            elem_errors = validate_element_result(elem)
            for e in elem_errors:
                errors.append(f"element_results[{i}]: {e}")

    return errors


def validate_element_result(result: dict) -> list[str]:
    """
    Validate one element_result dict.

    Returns a list of error strings.  Empty list = valid.
    """
    errors: list[str] = []

    for key in ELEMENT_RESULT_REQUIRED_KEYS:
        if key not in result:
            errors.append(f"Missing required key: '{key}'")

    if errors:
        return errors

    # element_id can be None (when IFC GlobalId is unavailable) or a string
    eid = result["element_id"]
    if eid is not None and not isinstance(eid, str):
        errors.append(f"'element_id' must be str or None, got {type(eid).__name__}")

    # element_type can be None or a string
    et = result["element_type"]
    if et is not None and not isinstance(et, str):
        errors.append(f"'element_type' must be str or None, got {type(et).__name__}")

    if result["status"] not in VALID_STATUSES:
        errors.append(
            f"'status' must be one of {VALID_STATUSES}, got '{result['status']}'"
        )

    if not isinstance(result["key"], str):
        errors.append("'key' must be a string")

    if not isinstance(result["raw"], str):
        errors.append(f"'raw' must be a string (JSON), got {type(result['raw']).__name__}")

    return errors
