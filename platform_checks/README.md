# Platform Checks — Stitching Guide

> How to plug Lux.Ai's solar checks into the shared platform.

---

## What This Folder Does

`platform_checks/` wraps the **final_pipeline** solar analysis into **5 check functions** that follow the platform's D1 database schema. Each function takes an IFC file, runs its check, and returns a structured JSON dict the orchestrator can write straight into the database — no string parsing needed.

---

## The 5 Checks

| Function | What it checks | Needs internet? |
|----------|---------------|-----------------|
| `check_location` | IfcSite has latitude + longitude | No |
| `check_building_areas` | Window, floor, and roof area present | No |
| `check_roof_geometry` | 3D roof segments extractable (tilt, azimuth, area) | No |
| `check_solar_production` | PVWatts returns kWh/yr > 0 for each segment | **Yes** |
| `check_leed_score` | LEED renewable-energy score ≥ 50% | **Yes** |

---

## File Structure

```
platform_checks/
│
├── __init__.py          ← Exports all 5 checks + run_all_checks + validator
│
├── schema.py            ← D1 schema definition + validate_check_result()
│                           Team name, valid statuses, LEED threshold
│
├── checks.py            ← The 5 check_* functions (core logic)
│
├── run_all.py           ← Orchestrator: runs all 5 checks on one IFC file
│                           CLI with --json, --skip-api flags
│
├── test_schema.py       ← Test suite: validates every output matches schema
│
└── README.md            ← This file
```

---

## How to Stitch Into the Platform

### Step 1 — Install dependencies

The checks depend on `final_pipeline/` (same repo). Make sure these are installed:

```bash
pip install ifcopenshell numpy requests
```

### Step 2 — Import and call

**Option A: Run all checks at once**

```python
from platform_checks import run_all_checks

results = run_all_checks("path/to/building.ifc")
# results = list of 5 dicts, one per check
```

**Option B: Run a single check**

```python
from platform_checks import check_location

result = check_location("path/to/building.ifc")
```

**Option C: From the command line**

```bash
# Human-readable output
python platform_checks/run_all.py  "building.ifc"

# JSON output (pipe to orchestrator / API)
python platform_checks/run_all.py  "building.ifc"  --json

# Skip API checks (offline — runs only location, areas, geometry)
python platform_checks/run_all.py  "building.ifc"  --skip-api

# Override location when IFC has no coordinates
python platform_checks/run_all.py  "building.ifc"  --lat 48.14 --lon 11.58
```

### Step 3 — Write results to D1

Each dict in the results list maps directly to one **`check_results`** row. The `element_results` key inside it is a list of dicts, each mapping to one **`element_results`** row.

```python
for check in results:
    # ── Insert into check_results table ──
    db.insert("check_results", {
        "check_name":   check["check_name"],       # e.g. "check_location"
        "team":         check["team"],              # "Lux.ai"
        "status":       check["status"],            # "pass" | "fail" | "error" | "unknown"
        "summary":      check["summary"],           # "IfcSite has coordinates: 49.1°N, 8.4°E"
        "has_elements": check["has_elements"],      # 1 or 0
    })

    # ── Insert into element_results table ──
    for elem in check["element_results"]:
        db.insert("element_results", {
            "element_id":   elem["element_id"],     # IFC GlobalId string or None
            "element_type": elem["element_type"],   # "IfcSite", "IfcSlab", etc.
            "status":       elem["status"],         # "pass" | "fail" | "unknown"
            "key":          elem["key"],             # "coordinates", "roof_segment", etc.
            "value":        elem["value"],           # the actual data (varies by check)
            "raw":          elem["raw"],             # JSON string — original output for debugging
        })
```

---

## Output Schema — What Each Check Returns

Every `check_*` function returns a dict with this exact shape:

```json
{
  "check_name": "check_location",
  "team": "Lux.ai",
  "status": "pass",
  "summary": "IfcSite has coordinates: 49.1°N, 8.4°E",
  "has_elements": 1,
  "element_results": [
    {
      "element_id": "0KMpiAlnb52RgQuM1CwVfd",
      "element_type": "IfcSite",
      "status": "pass",
      "key": "coordinates",
      "value": { "latitude": 49.1, "longitude": 8.4 },
      "raw": "{\"global_id\": \"0KMpi...\", ...}"
    }
  ]
}
```

### Field reference

| Field | Type | Maps to table | Description |
|-------|------|---------------|-------------|
| `check_name` | `string` | check_results | Function name, e.g. `"check_location"` |
| `team` | `string` | check_results | Always `"Lux.ai"` |
| `status` | `string` | check_results | Aggregate: `"pass"` if all elements pass, `"fail"` if any fail, `"error"` if function threw, `"unknown"` if data missing |
| `summary` | `string` | check_results | Human-readable, e.g. `"2 roof segment(s) extracted, total area 165.1 m²"` |
| `has_elements` | `int` | check_results | `1` if element_results has rows, `0` if building-level only |
| `element_id` | `string\|null` | element_results | IFC GlobalId (22-char base64). `null` when unavailable |
| `element_type` | `string\|null` | element_results | IFC entity type: `"IfcSite"`, `"IfcSlab"`, `"IfcWindow"`, etc. |
| `status` | `string` | element_results | Per-element: `"pass"`, `"fail"`, or `"unknown"` |
| `key` | `string` | element_results | What was checked: `"coordinates"`, `"roof_segment"`, `"annual_kwh"`, etc. |
| `value` | `any` | element_results | The extracted data (number, dict, null) |
| `raw` | `string` | element_results | JSON string of original output — for debugging / fallback display |

### Status logic per check

| Check | pass | fail | error |
|-------|------|------|-------|
| `check_location` | lat + lon found in IfcSite | coordinates missing or partial | IFC file cannot be opened |
| `check_building_areas` | all 3 areas (window, floor, roof) > 0 | any area = 0 or missing | extraction exception |
| `check_roof_geometry` | ≥ 1 valid segment (area > 0, tilt 0–90°) | no segments found | geometry engine failed |
| `check_solar_production` | annual_kwh > 0 for every segment | any segment returns 0 kWh | PVWatts API error |
| `check_leed_score` | score ≥ 50% | score < 50% | analysis failed |

---

## How to Validate the Schema

Run the built-in test suite:

```bash
# Full test (calls PVWatts API — needs internet)
python platform_checks/test_schema.py

# Offline test (geometry checks only, no API)
python platform_checks/test_schema.py --skip-api
```

Expected output:

```
  TOTAL: 70/70 passed, 0 failed
  ✅  ALL SCHEMA VALIDATIONS PASSED
```

The test validates:
- All required keys present
- Correct types (`string`, `int`, `list`)
- Status values ∈ `{pass, fail, error, unknown}`
- `has_elements` consistent with `element_results` length
- `element_id` is `string` or `null`
- `raw` field is a valid JSON string (parseable)
- Full output is JSON-serialisable

---

## How to Programmatically Validate

```python
from platform_checks import validate_check_result, check_location

result = check_location("building.ifc")
errors = validate_check_result(result)

if errors:
    print("Schema violations:", errors)
else:
    print("✅ Schema valid")
```

---

## Configuration

All thresholds live in two places:

| Setting | File | Default | What it controls |
|---------|------|---------|-----------------|
| `TEAM` | `schema.py` | `"Lux.ai"` | Team name in every check_result |
| `LEED_PASS_THRESHOLD` | `schema.py` | `50.0` | Score % needed for check_leed_score to return "pass" |
| `PANEL_EFFICIENCY` | `final_pipeline/config.py` | `0.20` | kW per m² of roof |
| `DEFAULT_CONSUMPTION_KWH_PER_M2` | `final_pipeline/config.py` | `150` | Energy benchmark (kWh/m²/yr) |
| `NREL_API_KEY` | `final_pipeline/config.py` | provided | PVWatts API key |

---

## Relationship to final_pipeline

```
platform_checks/          final_pipeline/
┌──────────────┐          ┌────────────────────────┐
│ check_*()    │─ calls ─▶│ ifc_metadata_extractor │
│ functions    │          │ ifc_roof_parser        │
│              │─ calls ─▶│ solar_production_engine│
│              │          │ analyze (for LEED)     │
│              │          │ config.py              │
└──────┬───────┘          └────────────────────────┘
       │
       ▼
  D1 database
  (check_results + element_results)
```

`platform_checks` is a **thin wrapper** — it does not duplicate any logic. It calls the existing pipeline functions and reformats their output into the D1 schema.

---

## Board Meeting #1 Notes

The exact return format is **pending Board Meeting #1**. Current implementation follows the "probable format" from the validation schema spec:

- Each `check_*` returns structured JSON (list of dicts)
- Maps directly to `check_results` + `element_results` rows
- No string parsing needed
- Status values: `"pass"`, `"fail"`, `"unknown"`, `"error"`

If the board changes the field names or adds new fields, update `schema.py` — the checks and validator will adapt automatically.
