# Lux.Ai — IFC Metadata Extraction: Agent Handoff Report

**Date:** 2026-02-18
**Project:** Lux.Ai — AI for Architecture & Urbanism
**Repo root:** `c:\Users\LEGION\Documents\GitHub\Lux.Ai\`
**Python:** 3.12.10 via `.venv\` virtual environment

---

## What Was Built (Summary)

Three things were created in this session:

| # | What | File | Status |
|---|------|------|--------|
| 1 | IFC model scanner — extracts 4 building metrics from all IFC files | `scan_ifc_models.py` | Done, working |
| 2 | IFC metadata key discovery — inventories every property/quantity name across all models | `discover_ifc_keys.py` | Done, working |
| 3 | Canonical alias map — standardised lookup table for future extractors | `key_aliases.json` | Generated |

---

## Context & Problem Being Solved

The repo contains **36 IFC (Industry Foundation Classes) BIM model files** across 20 sample building projects in `Sample projects/projects/`. IFC is the open standard for Building Information Modeling used by Archicad, Revit, Vectorworks, and other tools.

**The challenge:** Different IFC exporters use different internal names for the same concept. For example, "floor area" might be stored as:
- `Qto_SpaceBaseQuantities / NetFloorArea` (IFC4 / buildingSMART standard)
- `BaseQuantities / NetFloorArea` (IFC2x3 legacy)
- `GSA Space Areas / GSA BIM Area` (Revit US government exports)
- `ArchiCADQuantities / Netto-Grundfläche` (Archicad German)

The goal was to scan all files, discover every key name actually in use, and produce a normalised mapping so any future function can reliably extract data regardless of which software created the file.

---

## Repository Structure

```
Lux.Ai/
├── scan_ifc_models.py          ← NEW: extracts 4 metrics from all IFC files
├── discover_ifc_keys.py        ← NEW: inventories all property/quantity keys
├── key_aliases.json            ← NEW: canonical key → alias mapping (config)
├── ifc_key_inventory.json      ← NEW: raw inventory of all keys found
├── ifc_scan_results.csv        ← NEW: output data table (36 rows)
├── ifc_scan.log                ← NEW: per-file processing log
├── .venv/                      ← Python 3.12 virtual environment
│   └── Scripts/python.exe
└── Sample projects/
    └── projects/               ← 36 IFC files across 20 projects
        ├── 4351/arc.ifc
        ├── ac20/arc.ifc
        ├── city_house_munich/arc.ifc
        ├── dental_clinic/arc.ifc + mep.ifc + str.ifc
        ├── digital_hub/arc.ifc + heating.ifc + plumbing.ifc + ventilation.ifc
        ├── duplex/arc.ifc + mep.ifc
        ├── ettenheim_gis/city.ifc
        ├── fantasy_hotel_1/arc.ifc
        ├── fantasy_hotel_2/arc.ifc
        ├── fantasy_office_building_1/arc.ifc
        ├── fantasy_office_building_2/arc.ifc
        ├── fantasy_office_building_3/arc.ifc
        ├── fantasy_residential_building_1/arc.ifc
        ├── fzk_house/arc.ifc
        ├── hitos/arc.ifc
        ├── molio/arc.ifc
        ├── samuel_macalister_sample_house/arc.ifc + mep.ifc
        ├── schependomlaan/arc.ifc
        ├── sixty5/arc.ifc + str.ifc + facade.ifc + electrical.ifc + kitchen.ifc + plumbing.ifc + ventilation.ifc
        ├── smiley_west/arc.ifc
        └── wbdg_office/arc.ifc + str.ifc + mep.ifc
```

Each project also contains:
- `model_card.md` — YAML frontmatter with project metadata (source, license, discipline list)
- `snapshot.png` — thumbnail
- `license.txt`

---

## Dependencies Installed

```bash
.venv/Scripts/python.exe -m pip install ifcopenshell tabulate
```

Packages installed:
- `ifcopenshell==0.8.4.post1` — IFC file parser (binary wheel, no compiler needed)
- `tabulate==0.9.0` — console table formatting
- `numpy`, `shapely`, `lark`, `isodate` — pulled in as ifcopenshell dependencies

---

## File 1: `scan_ifc_models.py`

### Purpose
Scans all IFC files and extracts 4 building metrics per file into a CSV.

### Run
```bash
.venv/Scripts/python.exe scan_ifc_models.py
# Optional args:
# --root "path/to/ifc/directory"   (default: Sample projects/projects/)
# --output results.csv             (default: ifc_scan_results.csv)
```

### What it extracts

| Canonical Key | IFC Source | Fallback Strategy |
|---|---|---|
| `window_area_m2` | `IfcWindow → Qto_WindowBaseQuantities / Area` | `BaseQuantities/Area` → `OverallHeight × OverallWidth` |
| `floor_area_m2` | `IfcSpace → Qto_SpaceBaseQuantities / NetFloorArea` | `GrossFloorArea` → `BaseQuantities/NetFloorArea` → `IfcSlab[FLOOR]/NetArea` |
| `roof_area_m2` | `IfcRoof → Qto_RoofBaseQuantities / NetArea` | `GrossArea` → `IfcSlab[ROOF]/NetArea` → `GrossArea` |
| `true_north_angle_deg` | `IfcGeometricRepresentationContext.TrueNorth` | DirectionRatios (X,Y) → `atan2(x,y)` → compass bearing (clockwise from north) |
| `latitude` | `IfcSite.RefLatitude` | IfcCompoundPlaneAngleMeasure [deg, min, sec, microsec] → decimal degrees |
| `longitude` | `IfcSite.RefLongitude` | Same decoding |

### Key functions

```python
find_ifc_files(root_dir: Path) -> list[Path]
    # Recursively globs *.ifc under root_dir

process_ifc_file(ifc_path: Path) -> dict
    # Opens file, calls 4 extractors, returns result dict with all 8 columns

extract_window_area(model) -> float | None
extract_floor_area(model)  -> float | None
extract_roof_area(model)   -> float | None
extract_orientation(model) -> dict  # {true_north_angle_deg, latitude, longitude}

get_quantity(element, qset_name, qty_name) -> float | None
    # Walks IsDefinedBy → IfcRelDefinesByProperties → IfcElementQuantity
    # Handles both IfcQuantityArea (.AreaValue) and IfcQuantityLength (.LengthValue)

get_quantity_multi(element, qset_names: list[str], qty_name) -> float | None
    # Tries each qset name in order — handles IFC2x3 vs IFC4 naming differences

get_area_scale(model) -> float
get_length_scale(model) -> float
    # Uses ifcopenshell.util.unit.calculate_unit_scale to handle feet vs metres

decode_compound_angle(compound) -> float | None
    # Converts IfcCompoundPlaneAngleMeasure [deg,min,sec,microsec] to decimal degrees
```

### Output — `ifc_scan_results.csv`

36 rows, 9 columns:
```
project_name, ifc_file, window_area_m2, floor_area_m2, roof_area_m2,
true_north_angle_deg, latitude, longitude, error
```

### Actual scan results (key data)

| Project | Window m² | Floor m² | Roof m² | TrueNorth° | Lat | Lon |
|---|---|---|---|---|---|---|
| 4351 | 10.87 | N/A | N/A | N/A | N/A | N/A |
| ac20 | 309.50 | 1939.38 | 688.77 | 0.00 | 49.15 | 8.72 |
| city_house_munich | 29.51 | N/A | N/A | 32.56 | 48.19 | 11.47 |
| dental_clinic | 100.63 | N/A | N/A | N/A | 42.36 | -71.06 |
| digital_hub | 375.00 | 2842.12 | N/A | 360.00 | 48.14 | 11.58 |
| duplex | 65.94 | N/A | N/A | N/A | 41.87 | -87.64 |
| ettenheim_gis | 725.97 | N/A | N/A | N/A | N/A | N/A |
| fantasy_hotel_1 | 58.78 | N/A | N/A | 360.00 | 42.36 | -71.06 |
| fantasy_hotel_2 | 400.31 | N/A | N/A | 360.00 | 42.36 | -71.06 |
| fantasy_office_building_1 | 156.82 | 1257.07 | 339.92 | 360.00 | 48.14 | 11.58 |
| fantasy_office_building_2 | 142.91 | 716.06 | 419.57 | 360.00 | 48.14 | 11.58 |
| fantasy_office_building_3 | 984.96 | N/A | N/A | 360.00 | 48.15 | 11.57 |
| fantasy_residential_building_1 | 19.56 | N/A | N/A | 360.00 | 48.14 | 11.58 |
| fzk_house | 23.17 | 173.34 | 165.12 | 310.00 | 49.10 | 8.44 |
| hitos | 1531.32 | N/A | N/A | 360.00 | 69.67 | 18.83 |
| molio | N/A | 140.78 | N/A | 0.00 | 55.67 | 12.62 |
| samuel_macalister_sample_house | 61.64 | N/A | N/A | 323.00 | 42.21 | -71.03 |
| schependomlaan | N/A | N/A | N/A | N/A | N/A | N/A |
| sixty5/arc | 4901.15 | 0.00 | N/A | 0.00 | 51.45 | 5.48 |
| smiley_west | 95.92 | 1821.17 | 614.41 | 147.50 | 49.03 | 8.39 |
| wbdg_office | 124.50 | N/A | N/A | N/A | 42.36 | -71.06 |

> **Note:** `360.00°` true north = same as `0.00°` (floating-point rounding artefact near exact north alignment — cosmetically different, data is correct). MEP/structural discipline files return N/A for most metrics — expected.

### Summary statistics
- Files scanned: 36, Errors: 0
- Window data: 19/36 files
- Floor data: 9/36 files
- Roof data: 5/36 files
- Orientation: 25/36 files

---

## File 2: `discover_ifc_keys.py`

### Purpose
Full metadata key discovery — enumerates every property set and quantity set used across all 36 IFC files, grouped by IFC element type. Produces the raw inventory and the canonical alias map.

### Run
```bash
.venv/Scripts/python.exe discover_ifc_keys.py
```

### Element types targeted
`IfcWindow`, `IfcDoor`, `IfcSpace`, `IfcSlab`, `IfcRoof`, `IfcWall`, `IfcWallStandardCase`, `IfcCovering`, `IfcSite`, `IfcBuilding`, `IfcBuildingStorey`

### Key functions

```python
collect_qsets(model, element_type: str) -> dict
    # Returns {qset_name: {qty_name: count}} for all elements of that type in the model

collect_psets(model, element_type: str) -> dict
    # Returns {pset_name: {prop_name: count}} for all elements of that type in the model

merge_into(global_inv, element_type, section, file_data, project_name)
    # Accumulates per-file results: increments file_count, appends project_name

inventory_to_plain(global_inv) -> dict
    # Converts defaultdict structure to plain dict for JSON serialisation

build_aliases(inventory) -> dict
    # Creates key_aliases.json: pre-seeds known patterns + auto-discovers
    # any area-related quantity keys not already covered
```

### Discovery results summary

| Element Type | Qty Sets | Qty Keys | Prop Sets | Prop Keys |
|---|---|---|---|---|
| IfcWindow | 59 | 8,834 | 102 | 20,413 |
| IfcDoor | 21 | 2,887 | 64 | 10,029 |
| IfcSpace | 8 | 201 | 38 | 1,315 |
| IfcSlab | 9 | 144 | 35 | 302 |
| IfcRoof | 1 | 3 | 25 | 117 |
| IfcWall | 22 | 234 | 41 | 392 |
| IfcWallStandardCase | 20 | 146 | 18 | 238 |
| IfcCovering | 3 | 114 | 33 | 325 |
| IfcSite | 1 | 2 | 10 | 58 |
| IfcBuilding | 1 | 1 | 13 | 77 |
| IfcBuildingStorey | 1 | 4 | 28 | 66 |

> **Why IfcWindow has 59 qsets and 8,834 keys:** Archicad creates one unique quantity set per window *type* family (named `AC_Equantity_IFC_Fenster_-_ein_Panel`, `AC_Equantity_R1_21`, etc.), each with its own set of dimension keys. The actual area extraction only needs the 2 standard sets below.

---

## File 3: `key_aliases.json`

### Purpose
A **human-editable JSON config** that maps each canonical building metric to an ordered list of IFC lookup strategies. Any extractor function should iterate this list and return the first strategy that yields a value.

### Structure
```json
{
  "canonical_key": [
    {
      "entity":    "IfcWindow",               // IFC element type to query
      "source":    "qset",                    // "qset" (quantity set) or "attr" (direct attribute)
      "set_name":  "Qto_WindowBaseQuantities", // quantity set name
      "key":       "Area"                     // quantity name within the set
    },
    ...
    {
      "entity":    "IfcWindow",
      "source":    "attr",                    // direct IFC attribute (no set lookup)
      "keys":      ["OverallHeight", "OverallWidth"],
      "op":        "multiply"                 // operation to apply
    }
  ]
}
```

### Canonical keys defined

#### `window_area`
| Priority | Entity | Source | Set Name | Key | Notes |
|---|---|---|---|---|---|
| 1 | IfcWindow | qset | `Qto_WindowBaseQuantities` | `Area` | IFC4 standard |
| 2 | IfcWindow | qset | `BaseQuantities` | `Area` | IFC2x3 Archicad/Revit |
| 3 | IfcWindow | qset | `BaseQuantities` | `GrossArea` | IFC2x3 fallback |
| 4 | IfcWindow | attr | — | `OverallHeight × OverallWidth` | Last resort, multiply length attrs |

#### `floor_area`
| Priority | Entity | Source | Set Name | Key | Filter |
|---|---|---|---|---|---|
| 1 | IfcSpace | qset | `Qto_SpaceBaseQuantities` | `NetFloorArea` | — |
| 2 | IfcSpace | qset | `Qto_SpaceBaseQuantities` | `GrossFloorArea` | — |
| 3 | IfcSpace | qset | `BaseQuantities` | `NetFloorArea` | IFC2x3 |
| 4 | IfcSpace | qset | `BaseQuantities` | `GrossFloorArea` | IFC2x3 |
| 5 | IfcSpace | qset | `GSA Space Areas` | `GSA BIM Area` | US Revit exports |
| 6 | IfcSlab | qset | `Qto_SlabBaseQuantities` | `NetArea` | PredefinedType=FLOOR |
| 7 | IfcSlab | qset | `BaseQuantities` | `NetArea` | PredefinedType=FLOOR |
| 8 | IfcSlab | qset | `BaseQuantities` | `GrossArea` | PredefinedType=FLOOR |

#### `roof_area`
| Priority | Entity | Source | Set Name | Key | Filter |
|---|---|---|---|---|---|
| 1 | IfcRoof | qset | `Qto_RoofBaseQuantities` | `NetArea` | — |
| 2 | IfcRoof | qset | `Qto_RoofBaseQuantities` | `GrossArea` | — |
| 3 | IfcRoof | qset | `BaseQuantities` | `NetArea` | IFC2x3 |
| 4 | IfcSlab | qset | `Qto_SlabBaseQuantities` | `NetArea` | PredefinedType=ROOF |
| 5 | IfcSlab | qset | `Qto_SlabBaseQuantities` | `GrossArea` | PredefinedType=ROOF |
| 6 | IfcSlab | qset | `BaseQuantities` | `NetArea` | PredefinedType=ROOF |
| 7 | IfcSlab | qset | `BaseQuantities` | `GrossArea` | PredefinedType=ROOF |

#### `true_north_angle`
- Entity: `IfcGeometricRepresentationContext`
- Source: `attr` → `TrueNorth.DirectionRatios (X, Y)`
- Conversion: `compass_bearing = (-atan2(X, Y) in degrees) % 360`
- Meaning: degrees clockwise from north (0° = north aligned with +Y axis)

#### `latitude` / `longitude`
- Entity: `IfcSite`
- Source: `attr` → `RefLatitude` / `RefLongitude`
- Format: `IfcCompoundPlaneAngleMeasure` = tuple of `[degrees, minutes, seconds, microseconds]`
- Conversion: `decimal = deg + min/60 + sec/3600 + microsec/3_600_000_000`

#### `_auto_discovered_area_keys`
Any quantity keys containing the word "area" found in the inventory that aren't already in the 6 canonical keys above are appended here automatically by `build_aliases()`. Review these manually to see if any should be promoted to a canonical key.

---

## File 4: `ifc_key_inventory.json`

Full raw inventory of every property/quantity key found across all 36 IFC files. Structure:

```json
{
  "IfcWindow": {
    "quantity_sets": {
      "Qto_WindowBaseQuantities": {
        "Area":   { "file_count": 12, "projects": ["ac20", "digital_hub", ...] },
        "Height": { "file_count": 12, "projects": [...] },
        "Width":  { "file_count": 12, "projects": [...] }
      },
      "BaseQuantities": {
        "Area":      { "file_count": 7, "projects": [...] },
        "GrossArea": { "file_count": 4, "projects": ["duplex", ...] }
      }
    },
    "property_sets": {
      "Pset_WindowCommon": {
        "IsExternal":           { "file_count": 18, "projects": [...] },
        "ThermalTransmittance": { "file_count": 8,  "projects": [...] }
      }
    }
  },
  "IfcSpace": { ... },
  ...
}
```

Use this file as a reference when adding new canonical keys to `key_aliases.json`.

---

## IFC Exporter Ecosystem Found in the Dataset

| Exporter | Quantity Set Style | Property Set Style | Files |
|---|---|---|---|
| **Archicad (German IFC4)** | `BaseQuantities` + per-type `AC_Equantity_*` | `AC_Pset_*`, `ArchiCADProperties` | ac20, fzk_house |
| **Archicad (Dutch IFC2x3)** | `ArchiCADQuantities`, `BaseQuantities` | Same | sixty5 |
| **Revit (IFC2x3)** | `BaseQuantities`, `GSA Space Areas` | `PSet_Revit_*` | duplex, dental_clinic, wbdg_office |
| **IFC4 modern tools** | `Qto_*BaseQuantities` | `Pset_*Common` | digital_hub, schependomlaan |
| **IFC4X3 (latest)** | `Qto_*BaseQuantities` | `Pset_*Common` | city_house_munich |
| **Norwegian tool (hitos)** | Material-named sets (`Bindingsverk`, `Isolasjon`) | — | hitos |
| **Synchro 4D** | — | `SynchroResourceProperty` | some |

---

## IFC Schema Versions in Dataset

| Schema | Files | Example Projects |
|---|---|---|
| IFC2X3 | ~20 | 4351, duplex, schependomlaan, sixty5, hitos, molio, wbdg_office |
| IFC4 | ~14 | ac20, digital_hub, fzk_house, smiley_west, fantasy_* |
| IFC4X3 | 1 | city_house_munich |

---

## Key IFC Concepts for the Next Agent

### How properties are stored in IFC

```
IfcWindow
  └── IsDefinedBy (list of IfcRelDefinesByProperties)
        └── RelatingPropertyDefinition
              ├── IfcPropertySet (name="Pset_WindowCommon")
              │     └── HasProperties → [IfcPropertySingleValue, ...]
              │           └── .Name = "IsExternal", .NominalValue = True
              └── IfcElementQuantity (name="Qto_WindowBaseQuantities")
                    └── Quantities → [IfcQuantityArea, IfcQuantityLength, ...]
                          └── .Name = "Area", .AreaValue = 2.5
```

### How orientation is stored

```
IfcGeometricRepresentationContext
  └── TrueNorth → IfcDirection
        └── DirectionRatios = (X, Y)    # 2D vector pointing toward geographic north
              # (0, 1) = north is +Y (no rotation)
              # (1, 0) = north is +X (building rotated 90° CCW)
              # compass_bearing = (-atan2(X,Y) in degrees) % 360
```

### How location is stored

```
IfcSite
  ├── RefLatitude  → IfcCompoundPlaneAngleMeasure = (51, 27, 0, 0)  # 51°27'N
  └── RefLongitude → IfcCompoundPlaneAngleMeasure = (5,  29, 0, 0)  # 5°29'E
```

### Unit handling

Always check the model's units before using extracted values:
```python
import ifcopenshell.util.unit as ifc_unit_util
area_scale   = ifc_unit_util.calculate_unit_scale(model, "AREAMEASURE")
length_scale = ifc_unit_util.calculate_unit_scale(model, "LENGTHUNIT")
# US Revit models may use square feet → area_scale ≈ 0.0929
```

---

## How to Use `key_aliases.json` in a New Extractor

```python
import json
import ifcopenshell
import ifcopenshell.util.unit as ifc_unit_util
import math

# Load the alias map once
with open("key_aliases.json") as f:
    ALIASES = json.load(f)

def extract_metric(model, canonical_key: str) -> float | None:
    """
    Generic extractor that uses key_aliases.json to find a metric
    regardless of which IFC exporter created the file.
    """
    aliases = ALIASES.get(canonical_key, [])
    area_scale   = ifc_unit_util.calculate_unit_scale(model, "AREAMEASURE")
    length_scale = ifc_unit_util.calculate_unit_scale(model, "LENGTHUNIT")
    total = 0.0
    found = False

    for alias in aliases:
        entity   = alias["entity"]
        source   = alias["source"]
        pred_req = alias.get("predefined_type")

        for elem in model.by_type(entity):
            # Filter by PredefinedType if required
            if pred_req and getattr(elem, "PredefinedType", None) != pred_req:
                continue

            if source == "qset":
                val = get_quantity(elem, alias["set_name"], alias["key"])
                if val is not None:
                    total += val * area_scale
                    found = True

            elif source == "attr" and alias.get("op") == "multiply":
                vals = [getattr(elem, k, None) for k in alias["keys"]]
                if all(v is not None for v in vals):
                    product = 1.0
                    for v in vals:
                        product *= float(v)
                    total += product * (length_scale ** 2)
                    found = True

        if found:
            return round(total, 4)  # Return on first successful strategy

    return None
```

---

## Known Data Quality Notes

| Project | Issue | Explanation |
|---|---|---|
| `sixty5/arc.ifc` | `floor_area = 0.00` (not N/A) | Qto_SpaceBaseQuantities exists but values are 0 — model may lack space boundaries |
| `sixty5/str.ifc` | `floor_area = 34,199 m²` | Structural slabs classified as FLOOR — over-counting, architectural file preferred |
| `schependomlaan/arc.ifc` | All N/A | IFC2x3 model has no quantity sets — geometry only |
| TrueNorth `360.00°` | Same as `0.00°` | Floating-point artefact when vector X is tiny negative; `(-ε) % 360 ≈ 360` |
| `ettenheim_gis/city.ifc` | Windows = 725.97 m² | GIS city model — likely summing openings across many buildings, not a single building |
| MEP/structural discipline files | All N/A | Correct — these files don't contain architectural elements |

---

## Running Everything

```bash
# Activate venv (Windows)
.venv\Scripts\activate

# Run the 4-metric scanner
python scan_ifc_models.py
# → prints table to console
# → writes ifc_scan_results.csv
# → writes ifc_scan.log

# Re-run the key discovery (if new IFC files are added)
python discover_ifc_keys.py
# → overwrites ifc_key_inventory.json
# → overwrites key_aliases.json
# → prints full inventory to console
```

---

## Suggested Next Steps

1. **Extend `key_aliases.json`** — add more canonical keys (e.g., `wall_area`, `door_count`, `storey_height`, `building_height`) using the inventory as a reference
2. **Handle `schependomlaan`** — this IFC2x3 model has no quantity sets; area must be computed from geometry using `ifcopenshell.geom`
3. **Window-to-floor ratio** — divide `window_area` by `floor_area` per project for glazing ratio analysis
4. **Solar orientation** — combine `true_north_angle` with `latitude` for solar exposure estimation
5. **Energy analysis** — roof/floor ratio and glazing ratio are key inputs for energy simulation
6. **Database ingestion** — load `ifc_scan_results.csv` into a dataframe or database for ML feature extraction

---

*Report generated by Claude Sonnet 4.6 (claude-sonnet-4-6) on 2026-02-18*
