# Solar Pipeline — README

## What is this?

This tool answers **one question**:

> **"If I put solar panels on this building's roof, how much energy will they produce?"**

You give it a **building file** (`.ifc` format — the standard file that architects export from tools like Revit, ArchiCAD, or Vectorworks).

It gives you back a **solar score** — how much of the building's energy the roof solar panels could cover.

```
     ┌─────────────┐          ┌────────────────┐          ┌──────────────┐
     │             │          │                │          │              │
     │  .ifc file  │  ──────▶ │  analyze_ifc() │  ──────▶ │  Solar Score │
     │  (building) │          │  (this tool)   │          │  (% energy)  │
     │             │          │                │          │              │
     └─────────────┘          └────────────────┘          └──────────────┘
```

**Score = 100%** means the roof produces as much energy as the building consumes.
That's **net-zero energy** — a key goal for green building certifications like **LEED**.

---

## Quick Start (for everyone)

### Step 1 — Install

Open a terminal (Command Prompt, PowerShell, or Mac Terminal) and type:

```bash
pip install ifcopenshell numpy requests tabulate
```

### Step 2 — Run

```bash
python -m final_pipeline.analyze  "path/to/your/building.ifc"
```

That's it. You'll see a report like this:

```
  ============================================================
    SOLAR ANALYSIS — fzk_house
  ============================================================

    FILE        arc.ifc
    LOCATION    49.100435, 8.436539

    ── Building Metadata ────────────────────────
       Window area.................. 23.2 m²
       Floor area................... 173.3 m²
       Roof area (property-set)..... 165.1 m²
       True north................... 310.0°

    ── Roof Segments (geometry) ─────────────────
       Roof_Seg_01    Area:    82.6 m²   Tilt:  30.0°   Azimuth: 310.0°   →  11,105.3 kWh/yr
       Roof_Seg_02    Area:    82.6 m²   Tilt:  30.0°   Azimuth: 130.0°   →  14,914.6 kWh/yr

    ── Solar Production ────────────────────────
       Total roof area .........    165.1 m²
       System capacity .........     33.0 kW
       Annual production .......  26,020.0 kWh/yr

    ── LEED Score ──────────────────────────────
       Consumption estimate ....  26,001 kWh/yr
       Renewable production ....  26,020 kWh/yr
       Score ...................    100.1 %

       ★  Net-zero energy achieved!
```

### If the building file has no location

Some IFC files don't include coordinates. Just add them manually:

```bash
python -m final_pipeline.analyze  "building.ifc"  --lat 48.14  --lon 11.58
```

---

## Quick Start (for developers / AI agents)

One import, one function, one result:

```python
from final_pipeline.analyze import analyze_ifc

result = analyze_ifc("building.ifc")

if result["ok"]:
    print(result["leed_score"])        # 100.1
    print(result["total_production"])  # 26019.96  (kWh/yr)
    print(result["segments"])          # [{id, area, tilt, azimuth, annual_kwh}, ...]
else:
    print(result["error"])             # what went wrong
```

### Function signature

```python
analyze_ifc(
    ifc_path,                        # path to .ifc file (required)
    *,
    lat=None,                        # override latitude
    lon=None,                        # override longitude
    name=None,                       # project name for the report
    consumption_kwh_per_m2=None,     # energy benchmark (default: 150)
    call_api=True,                   # set False for offline mode
)
```

### What the result dict contains

| Key | Type | What it means |
|-----|------|---------------|
| `ok` | bool | `True` if everything worked |
| `error` | str or None | Error message if `ok` is False |
| `project_name` | str | Name of the project |
| `ifc_file` | str | File name |
| `window_area_m2` | float or None | Total window area in the building |
| `floor_area_m2` | float or None | Total floor area |
| `roof_area_m2` | float or None | Roof area from property sets (metadata) |
| `true_north_deg` | float or None | Building orientation (0° = facing north) |
| `latitude` | float | Site latitude |
| `longitude` | float | Site longitude |
| `segments` | list[dict] | Each roof face with its area, tilt, azimuth, and annual kWh |
| `total_roof_area_m2` | float | Total roof area from 3D geometry |
| `total_capacity_kw` | float | Solar panel capacity in kilowatts |
| `total_production` | float | Annual energy production in kWh/yr |
| `consumption` | float | Estimated annual consumption in kWh/yr |
| `leed_score` | float | Production ÷ Consumption × 100 (%) |

---

## How It Works — The DNA of the Tool

The pipeline has **5 steps**. Each step is handled by a separate module, but `analyze_ifc()` runs them all for you automatically.

```
  STEP 1          STEP 2            STEP 3          STEP 4           STEP 5
 ┌────────┐    ┌───────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐
 │ Open   │    │ Read      │    │ Analyse   │    │ Query    │    │ Calculate│
 │ IFC    │───▶│ metadata  │───▶│ roof 3D   │───▶│ PVWatts  │───▶│ LEED     │
 │ file   │    │ from file │    │ geometry  │    │ solar API│    │ score    │
 └────────┘    └───────────┘    └───────────┘    └──────────┘    └──────────┘
                    │                │                │                │
              window area      roof segments     kWh per          score =
              floor area       (tilt, azimuth,   segment          production
              roof area         area each)                        ÷ consumption
              lat, lon                                            × 100
              true north
```

### Step 1 — Open the IFC file

**IFC** (Industry Foundation Classes) is the universal file format for buildings. It stores the entire building as a database of objects — walls, windows, roofs, floors, etc. — with their geometry and properties.

We use the open-source library `ifcopenshell` to read it.

### Step 2 — Read building metadata

The tool reads **property sets** embedded in the IFC file:

| What | Where in the IFC file | Why we need it |
|------|----------------------|----------------|
| Window area | `IfcWindow` → quantity sets | Glazing ratio analysis |
| Floor area | `IfcSpace` or `IfcSlab[FLOOR]` → quantity sets | Estimate building energy consumption |
| Roof area | `IfcRoof` or `IfcSlab[ROOF]` → quantity sets | Cross-validate with 3D geometry |
| Latitude & Longitude | `IfcSite.RefLatitude / RefLongitude` | Tell PVWatts where the building is |
| True North | `IfcGeometricRepresentationContext.TrueNorth` | Correct roof azimuths to real compass |

**The alias system**: Different software (Revit, ArchiCAD, Vectorworks) stores these values under different names. For example, floor area might be called `NetFloorArea`, `GrossFloorArea`, `GSA BIM Area`, or `Netto-Grundfläche` (German). The file `key_aliases.json` contains a priority-ordered list of all known names for each property, so the tool tries each one until it finds a match.

### Step 3 — Analyse roof 3D geometry

This is the key step that makes per-segment solar analysis possible:

1. **Find roof elements** — `IfcRoof` objects and `IfcSlab` objects marked as roof type
2. **Triangulate** — Convert each roof element into a mesh of triangles using world coordinates
3. **Compute normals** — Each triangle has a direction it faces (its "normal vector")
4. **Cluster** — Group triangles that face the same direction (within 15° tolerance)
5. **Compute properties** — For each cluster, calculate:
   - **Area** — total m² of that roof face
   - **Tilt** — angle from horizontal (0° = flat, 90° = vertical wall)
   - **Azimuth** — compass direction the face points (0° = north, 180° = south)
6. **True north correction** — Rotate all azimuths by the building's TrueNorth angle so they match real-world compass directions

*Why per-segment?* A south-facing roof face in Europe may produce **2.5× more energy** than a north-facing one. A single whole-roof average would be misleading.

### Step 4 — Query the PVWatts solar API

For each roof segment, we call the **NREL PVWatts v8** API (US National Renewable Energy Laboratory):

```
  Inputs:  lat, lon, roof area, tilt, azimuth
  Output:  estimated annual solar energy in kWh
```

The API uses real weather data, sun angles, and atmospheric conditions for the specific location. It accounts for panel losses (wiring, soiling, shading) at 14%.

### Step 5 — Calculate LEED score

```
  Score = Total Production (kWh/yr)  ÷  Total Consumption (kWh/yr)  ×  100
```

- **Production** = sum of all segment yields from PVWatts
- **Consumption** = floor area × 150 kWh/m²/yr (ASHRAE office benchmark)
  - If the IFC file has no floor area data, defaults to 50,000 kWh/yr

| Score | Meaning |
|-------|---------|
| ≥ 100% | Net-zero energy — roof produces everything the building needs |
| 50–99% | Strong renewable contribution |
| 10–49% | Moderate contribution |
| < 10% | Low contribution — different design or additional renewables needed |

---

## Configuration — `config.py`

All defaults can be changed in [`final_pipeline/config.py`](config.py):

```python
# ── NREL PVWatts v8 API ──────────────────────────────────────────────
NREL_API_KEY = "0zwEIS1a..."         # Free key from api.nrel.gov
PVWATTS_BASE_URL = "https://developer.nrel.gov/api/pvwatts/v8.json"

# ── Solar panel assumptions ──────────────────────────────────────────
PANEL_EFFICIENCY = 0.20              # 20% efficiency = 1 kW per 5 m²
ARRAY_TYPE = 1                       # 1 = fixed roof mount
MODULE_TYPE = 1                      # 1 = premium (monocrystalline)
SYSTEM_LOSSES = 14                   # 14% total system losses

# ── Roof geometry ────────────────────────────────────────────────────
DEFAULT_ANGLE_TOLERANCE_DEG = 15.0   # Clustering tolerance (degrees)
MIN_SEGMENT_AREA_M2 = 1.0           # Ignore tiny fragments

# ── Energy benchmarks ────────────────────────────────────────────────
DEFAULT_CONSUMPTION_KWH_PER_M2 = 150 # ASHRAE office (kWh/m²/yr)
FALLBACK_CONSUMPTION_KWH = 50_000    # When floor area is unknown
```

### What each setting means

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `PANEL_EFFICIENCY` | 0.20 | How much of the roof area converts to electricity. 0.20 means 1 kW of solar capacity per 5 m² of roof. Premium panels are ~20%, budget panels ~15%. |
| `SYSTEM_LOSSES` | 14% | Energy lost in wiring, inverters, soiling, snow, etc. Industry standard is 14%. |
| `DEFAULT_ANGLE_TOLERANCE_DEG` | 15° | When grouping roof triangles by direction, faces within 15° of each other are merged into one segment. Lower = more segments, higher = fewer. |
| `MIN_SEGMENT_AREA_M2` | 1.0 | Roof faces smaller than 1 m² are ignored (geometric noise). |
| `DEFAULT_CONSUMPTION_KWH_PER_M2` | 150 | ASHRAE benchmark for a typical office building. Residential is ~100, hospitals ~300. Changes the LEED score denominator. |
| `NREL_API_KEY` | provided | Free API key from [developer.nrel.gov](https://developer.nrel.gov/signup/). Rate limit: 1 request/second. |

---

## File Structure

```
final_pipeline/
│
├── analyze.py                   ← THE MAIN FILE — one function does everything
│                                   analyze_ifc("building.ifc") → score
│
├── ifc_metadata_extractor.py    ← Step 2: reads building properties from IFC
│                                   (areas, location, orientation)
│
├── ifc_roof_parser.py           ← Step 3: 3D roof geometry → segments
│                                   (tilt, azimuth, area per face)
│
├── solar_production_engine.py   ← Step 4: calls PVWatts API → kWh/yr
│
├── run_solar_analysis.py        ← Advanced CLI with batch modes
│
├── config.py                    ← All configurable settings
│
├── key_aliases.json             ← Maps property names across BIM software
│                                   (Revit, ArchiCAD, Vectorworks, etc.)
│
├── __init__.py                  ← Package marker
└── README.md                    ← This file
```

---

## The Key Aliases System — Why It Exists

Different architecture software stores the same data under different names:

| Data | Revit calls it | ArchiCAD calls it | IFC4 standard |
|------|---------------|-------------------|---------------|
| Floor area | `GSA BIM Area` | `Netto-Grundfläche` | `NetFloorArea` |
| Window area | `Area` in `BaseQuantities` | `Fläche` in `ArchiCADQuantities` | `Area` in `Qto_WindowBaseQuantities` |
| Roof area | `GrossArea` in `BaseQuantities` | `Oberflächenbereich` | `NetArea` in `Qto_RoofBaseQuantities` |

The file `key_aliases.json` contains a **priority-ordered list** for each property. The tool tries each name in order and uses the first match. This means it works with IFC files from **any** BIM software without manual configuration.

You can add new aliases by editing the JSON file — no code changes needed.

---

## Supported IFC Versions

| Version | Status | Example projects |
|---------|--------|-----------------|
| IFC2x3 | ✅ Fully supported | duplex, schependomlaan, hitos |
| IFC4 | ✅ Fully supported | fzk_house, digital_hub, fantasy_* |
| IFC4x3 | ✅ Supported | city_house_munich |

---

## Requirements

| Package | Version | Why |
|---------|---------|-----|
| Python | ≥ 3.10 | Type hints, match statements |
| ifcopenshell | ≥ 0.7.0 | Open and parse IFC files |
| numpy | ≥ 1.21 | 3D geometry math |
| requests | ≥ 2.28 | Call the PVWatts API |
| tabulate | ≥ 0.9 | Pretty-print tables (optional) |

Install all at once:
```bash
pip install ifcopenshell numpy requests tabulate
```

---

## FAQ

**Q: Do I need internet?**
A: Yes, for the solar production numbers (PVWatts API). You can run offline with `call_api=False` to get just the roof geometry without kWh values.

**Q: What if my IFC file has no roof?**
A: The tool returns `ok=False` with error `"No roof segments found"`.

**Q: What if there's no location in the file?**
A: Pass `--lat` and `--lon` on the command line, or `lat=` and `lon=` in Python.

**Q: Can I change the panel type?**
A: Yes — edit `PANEL_EFFICIENCY`, `MODULE_TYPE`, and `SYSTEM_LOSSES` in `config.py`.

**Q: Is the score accurate?**
A: It's an **estimate**, not a guarantee. PVWatts uses real weather data and is widely trusted in the industry, but the actual production depends on shading, panel brand, inverter quality, and maintenance. This tool gives you a solid ballpark for early-stage design decisions and LEED pre-assessment.

**Q: What's LEED?**
A: Leadership in Energy and Environmental Design — the world's most widely used green building rating system. One of its credits rewards buildings that generate renewable energy on-site. The score this tool gives approximates that credit.
