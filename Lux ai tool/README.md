# Lux.Ai — IFCore Platform Tool

> Solar energy compliance checks for the IFCore platform.

---

## Repo Structure (IFCore Contract)

```
Lux ai tool/
├── tools/
│   └── checker_solar.py      ← 5 check_* functions (platform-scanned)
├── final_pipeline/            ← bundled IFC parsing + solar engine
│   ├── config.py
│   ├── ifc_metadata_extractor.py
│   ├── ifc_roof_parser.py
│   ├── solar_production_engine.py
│   └── key_aliases.json
├── run.py                     ← CLI entry-point
├── requirements.txt           ← team dependencies
├── test_contract.py           ← contract validation tests
└── README.md                  ← this file
```

The platform auto-discovers all `check_*` functions inside `tools/checker_*.py`.

---

## Quick Start

```bash
cd "Lux ai tool"
pip install -r requirements.txt

# Run all 5 checks on an IFC file
python run.py path/to/model.ifc

# Run specific checks only
python run.py path/to/model.ifc --checks location building_areas roof_geometry

# Override coordinates for solar / LEED checks
python run.py path/to/model.ifc --lat 48.13 --lon 11.58

# Export results to JSON
python run.py path/to/model.ifc --output results.json

# List available checks
python run.py --list-checks
```

| Function | What it checks | Needs internet? |
|----------|---------------|-----------------|
| `check_location` | IfcSite has latitude + longitude | No |
| `check_building_areas` | Window, floor, and roof area present | No |
| `check_roof_geometry` | 3D roof segments extractable (tilt, azimuth, area) | No |
| `check_solar_production` | PVWatts returns kWh/yr > 0 for each segment | **Yes** |
| `check_leed_score` | LEED renewable-energy score ≥ 50% | **Yes** |

---

## Usage

### Local testing

```python
import ifcopenshell
from tools.checker_solar import check_location

model = ifcopenshell.open("path/to/model.ifc")
results = check_location(model)
for r in results:
    print(f"[{r['check_status'].upper()}] {r['element_name']}: {r['actual_value']} (req: {r['required_value']})")
```

### Contract validation

```bash
cd "Lux ai tool"
python test_contract.py
```

---

## Contract Compliance

Every `check_*` function:

- **First arg:** `model` (`ifcopenshell.file` object)
- **Returns:** `list[dict]` — one dict per element
- **Dict keys:** `element_id`, `element_type`, `element_name`, `element_name_long`, `check_status`, `actual_value`, `required_value`, `comment`, `log`
- **Status values:** `pass`, `fail`, `warning`, `blocked`, `log`

---

## Dependencies

This tool is fully self-contained. All `final_pipeline/` modules are bundled. Install with:

```bash
pip install ifcopenshell numpy requests
```
