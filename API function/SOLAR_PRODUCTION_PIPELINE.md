# ☀️ Solar Production Pipeline — Full Documentation

## 1. Project Overview

This pipeline estimates the **annual solar energy production** of a building's roof by:

1. **Parsing** an IFC (Industry Foundation Classes) file to extract roof geometry
2. **Segmenting** the roof into logical surfaces based on orientation
3. **Querying** the NREL PVWatts v8 API for each segment's solar yield
4. **Aggregating** results for a total building-level production estimate (kWh/yr)

This per-segment approach is critical for **LEED certification** accuracy — a south-facing surface may produce 2.5× more energy than a north-facing one on the same roof.

---

## 2. Architecture

```
┌─────────────────────┐
│   IFC File (.ifc)   │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│         ifc_roof_parser.py                  │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │ get_roof_elements(model)            │    │
│  │  → finds IfcRoof + decomposed slabs │    │
│  └──────────────┬──────────────────────┘    │
│                 │                            │
│  ┌──────────────▼──────────────────────┐    │
│  │ extract_geometry(element, settings) │    │
│  │  → triangulated mesh (verts/faces)  │    │
│  └──────────────┬──────────────────────┘    │
│                 │                            │
│  ┌──────────────▼──────────────────────┐    │
│  │ cluster_faces_by_normal(normals,    │    │
│  │   areas, angle_tolerance=15°)       │    │
│  │  → groups triangles by orientation  │    │
│  └──────────────┬──────────────────────┘    │
│                 │                            │
│  ┌──────────────▼──────────────────────┐    │
│  │ compute_segment_properties(normals, │    │
│  │   areas, cluster_indices)           │    │
│  │  → tilt, azimuth, total area        │    │
│  └──────────────┬──────────────────────┘    │
│                 │                            │
│  ┌──────────────▼──────────────────────┐    │
│  │ parse_roof_segments(ifc_path)       │    │
│  │  → list[{id, area, tilt, azimuth}]  │    │
│  └──────────────┬──────────────────────┘    │
└─────────────────┼───────────────────────────┘
                  │
                  ▼  list of segment dicts
┌─────────────────────────────────────────────┐
│      solar_production_engine.py             │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │ Location(lat, lon, name)            │    │
│  │  → dataclass for site coordinates   │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │ calculate_segment_production(       │    │
│  │   area, tilt, azimuth, location)    │    │
│  │  → annual kWh for one segment       │    │
│  └──────────────┬──────────────────────┘    │
│                 │                            │
│  ┌──────────────▼──────────────────────┐    │
│  │ run_production_analysis(            │    │
│  │   segments, location)               │    │
│  │  → total kWh + per-segment results  │    │
│  └──────────────┬──────────────────────┘    │
└─────────────────┼───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│      run_solar_analysis.py (orchestrator)   │
│                                             │
│  1. Reads IFC path (CLI arg or default)     │
│  2. Calls parse_roof_segments()             │
│  3. Calls run_production_analysis()         │
│  4. Prints full report + LEED estimate      │
└─────────────────────────────────────────────┘
```

---

## 3. Files Created

### 3.1 `app/src/ifc_roof_parser.py` — IFC Geometry Parser

**Purpose:** Extract roof segments (area, tilt, azimuth) from any IFC file by analyzing 3D geometry.

| Function | Input | Output | Description |
|---|---|---|---|
| `get_roof_elements(model)` | `ifcopenshell.file` | `list[IfcElement]` | Finds all roof-related elements: `IfcRoof` entities, their decomposed `IfcSlab` children (via `IfcRelAggregates`), and standalone `IfcSlab` entities with `.ROOF.` predefined type |
| `extract_geometry(element, settings)` | IFC element + geom settings | `(vertices, faces)` as numpy arrays | Uses `ifcopenshell.geom.create_shape()` with `USE_WORLD_COORDS=True` to get triangulated mesh data |
| `compute_face_normals(vertices, faces)` | numpy arrays | `(normals, areas)` — Nx3 unit normals + N areas | Computes cross-product normals and triangle areas for every face in the mesh |
| `cluster_faces_by_normal(normals, areas, angle_tolerance)` | normals, areas, tolerance (default 15°) | `list[list[int]]` — cluster indices | Groups triangles whose normals are within `angle_tolerance` degrees of each other. Only considers **upward-facing** triangles (normal Z > 0). Uses greedy angular clustering. |
| `compute_segment_properties(normals, areas, cluster_indices)` | normals, areas, index groups | `dict{area, tilt, azimuth}` | Calculates **area-weighted average normal** per cluster, then derives: **tilt** = `arccos(nz)` in degrees, **azimuth** = `atan2(nx, ny) mod 360` in degrees |
| `parse_roof_segments(ifc_path)` | file path string | `list[{"id", "area", "tilt", "azimuth"}]` | **Main entry point.** Orchestrates all the above. Returns the standardized segment list that the production engine consumes. |

**Key Design Decisions:**
- **Geometry-based extraction** — does NOT rely on IFC property sets (`PitchAngle`, etc.) because these are unreliably populated across different BIM authoring tools
- **Clustering by face normals** — handles curved roofs (barrel vaults → many normals) and planar roofs (hip/gable → few distinct normals) with the same algorithm
- **Filters downward-facing triangles** — eliminates soffit/interior faces that aren't solar-relevant
- **Minimum area threshold** — ignores clusters smaller than 1 m² to filter geometric noise

---

### 3.2 `app/src/solar_production_engine.py` — PVWatts API Client

**Purpose:** Calculate annual solar energy production (kWh) for roof segments using the NREL PVWatts v8 API.

| Component | Type | Description |
|---|---|---|
| `Location` | `@dataclass` | Stores `latitude: float`, `longitude: float`, `name: str` for a project site |
| `API_KEY` | `str` constant | NREL API key (`0zwEIS1a...` — production key) |
| `BASE_URL` | `str` constant | `https://developer.nrel.gov/api/pvwatts/v8.json` |
| `calculate_segment_production(area, tilt, azimuth, location)` | function | Computes `system_capacity = area × 0.20` (20% panel efficiency = 1 kW per 5 m²), calls PVWatts API with fixed roof mount settings, returns annual AC output in kWh |
| `run_production_analysis(segments, location)` | function | Iterates a segment list, calls `calculate_segment_production()` for each, returns `(total_kwh, per_segment_results)` |
| `main()` | function | Standalone mode with hardcoded dummy segments for testing |

**API Parameters Sent:**

| Parameter | Value | Meaning |
|---|---|---|
| `system_capacity` | `area × 0.20` | kW capacity based on roof area |
| `azimuth` | from segment | Compass bearing (180° = south) |
| `tilt` | from segment | Angle from horizontal in degrees |
| `array_type` | `1` | Fixed — roof mount |
| `module_type` | `1` | Premium (monocrystalline) |
| `losses` | `14` | System losses (wiring, soiling, etc.) |

**Error Handling:**
- `requests.RequestException` → prints error, returns 0 kWh for that segment
- `KeyError` on missing `outputs` → prints API error response, returns 0
- `response.raise_for_status()` → catches HTTP 4xx/5xx errors
- `time.sleep(1)` between API calls → respects rate limits

---

### 3.3 `app/src/run_solar_analysis.py` — Orchestrator

**Purpose:** Glue script that connects the parser to the production engine.

| Component | Description |
|---|---|
| `SITE` | Hardcoded `Location(41.38, 2.17, "Barcelona_Project_Alpha")` — to be parameterized later |
| `DEFAULT_IFC` | Points to `00_data/ifc_models/Ifc4_SampleHouse_1_Roof.ifc` |
| CLI support | Accepts optional IFC file path as first argument: `python run_solar_analysis.py path/to/file.ifc` |
| Report output | Prints per-segment table + total production + LEED score example |

---

### 3.4 `requirements.txt` — Updated Dependencies

Added: `requests>=2.28.0` (required by the production engine for API calls)

Already present: `ifcopenshell`, `numpy`, `trimesh` (used by the parser)

---

## 4. Inputs & Outputs

### Inputs

| Input | Format | Source | Required |
|---|---|---|---|
| **IFC file** | `.ifc` (IFC2x3 or IFC4) | BIM authoring tool (Revit, ArchiCAD, etc.) | ✅ |
| **Site location** | Latitude/Longitude | Hardcoded (future: extracted from `IfcSite`) | ✅ |
| **NREL API key** | String | `api.nrel.gov` registration | ✅ |

### Outputs

| Output | Format | Description |
|---|---|---|
| **Per-segment data** | `list[dict]` | `{"id": str, "area": float, "tilt": float, "azimuth": float}` |
| **Per-segment yield** | `float` | Annual AC energy production in kWh/yr per roof surface |
| **Total building production** | `float` | Sum of all segment yields in kWh/yr |
| **LEED score estimate** | `float` | `(Total Production / Assumed Consumption) × 100` |

### Example Output (SampleHouse IFC)

```
=== SOLAR PRODUCTION ANALYSIS REPORT ===
Site: Barcelona_Project_Alpha (41.38°N, 2.17°E)
IFC: Ifc4_SampleHouse_1_Roof.ifc

--- Roof Segments Detected ---
  Roof_Seg_01 | Area:  71.48 m² | Tilt: 10.3° | Azimuth: 180° | Capacity: 14.3 kW | Yield: 19,063 kWh/yr
  Roof_Seg_02 | Area:  45.44 m² | Tilt: 27.9° | Azimuth:   0° | Capacity:  9.1 kW | Yield:  7,566 kWh/yr

--- Summary ---
  Total Roof Area:           116.92 m²
  Total System Capacity:      23.4 kW
  TOTAL ANNUAL PRODUCTION:   26,629 kWh/yr

--- LEED Estimate ---
  Assumed consumption:       50,000 kWh/yr
  Renewable coverage:         53.3%
  Score = Total Production / Consumption × 100
```

---

## 5. IFC Compatibility

### Does the parser work for different IFC files?

**Yes, with the following conditions:**

| IFC Feature | Supported | How |
|---|---|---|
| **IFC4** (`.ifc`) | ✅ | Tested with SampleHouse |
| **IFC2x3** (`.ifc`) | ✅ | Same `ifcopenshell.geom` API; element types are identical |
| **IfcRoof (monolithic)** | ✅ | Geometry is clustered by face normals into logical segments |
| **IfcRoof → IfcSlab (decomposed)** | ✅ | Sub-slabs found via `IfcRelAggregates`; each processed individually |
| **IfcSlab with `.ROOF.` type** | ✅ | Standalone roof slabs detected by predefined type |
| **Barrel vault / curved roofs** | ✅ | Many triangle normals → clustering groups them into ~2-4 orientations |
| **Hip / gable roofs** | ✅ | Few distinct planar groups → clustering identifies each face |
| **Flat roofs** | ✅ | All normals ≈ (0,0,1) → single segment with tilt ≈ 0° |
| **IFC-XML** (`.ifcXML`) | ⚠️ Partial | ifcopenshell can open it, but geometry extraction may differ |
| **IFC-ZIP** (`.ifcZIP`) | ❌ | Must be extracted first |
| **No roof elements** | ✅ Handled | Returns empty list with warning message |
| **Roof with skylights/openings** | ⚠️ | Openings not subtracted from area yet — future enhancement |

### Potential Variations Across BIM Software

| BIM Tool | Typical IFC Export | Parser Behavior |
|---|---|---|
| **Autodesk Revit** | IfcRoof as single Brep; sub-slabs sometimes decomposed | ✅ Both paths handled |
| **ArchiCAD** | IfcRoof with clean decomposition into IfcSlab children | ✅ Ideal case |
| **Tekla / Trimble** | IfcSlab with `.ROOF.` type (no IfcRoof parent) | ✅ Caught by type scan |
| **Blender (BlenderBIM)** | Varies — may use IfcRoof or IfcSlab | ✅ Both paths handled |
| **Generic/Unknown** | May have non-standard geometry representations | ⚠️ Falls back to Brep triangulation |

---

## 6. Math Reference

### System Capacity

$$P_{capacity} = A_{segment} \times 0.20 \text{ kW}$$

Where 0.20 = 20% module efficiency (1 kW per 5 m²)

### Tilt from Normal Vector

$$\theta_{tilt} = \arccos(n_z) \times \frac{180}{\pi}$$

### Azimuth from Normal Vector

$$\phi_{azimuth} = \text{atan2}(n_x, n_y) \mod 360°$$

Where 0° = North, 90° = East, 180° = South, 270° = West

### Area-Weighted Average Normal (per cluster)

$$\vec{n}_{avg} = \frac{\sum_{i \in cluster} A_i \cdot \vec{n}_i}{\left\| \sum_{i \in cluster} A_i \cdot \vec{n}_i \right\|}$$

### LEED Renewable Energy Score

$$Score = \frac{\sum_{s=1}^{N} P_{s}}{C_{total}} \times 100$$

Where $P_s$ = annual production of segment $s$, $C_{total}$ = total building consumption

---

## 7. How to Run

### Full Pipeline (IFC → Solar Report)

```bash
cd app/src
python run_solar_analysis.py
```

With a custom IFC file:

```bash
python run_solar_analysis.py "C:\path\to\your\model.ifc"
```

### Parser Only (IFC → Segment List)

```python
from ifc_roof_parser import parse_roof_segments

segments = parse_roof_segments("path/to/model.ifc")
for seg in segments:
    print(f"{seg['id']}: {seg['area']:.1f} m², tilt={seg['tilt']:.1f}°, azimuth={seg['azimuth']:.0f}°")
```

### Production Engine Only (Segments → kWh)

```python
from solar_production_engine import Location, run_production_analysis

segments = [
    {"id": "South_Face", "area": 120, "tilt": 30, "azimuth": 180},
    {"id": "East_Face",  "area": 85,  "tilt": 15, "azimuth": 90},
]
site = Location(latitude=41.38, longitude=2.17, name="Barcelona")

total, results = run_production_analysis(segments, site)
print(f"Total: {total:,.0f} kWh/yr")
```

---

## 8. Future Enhancements

| Enhancement | Impact | Difficulty |
|---|---|---|
| Extract location from `IfcSite` lat/lon automatically | Eliminates hardcoded coordinates | Low |
| Subtract skylight/opening areas from roof segments | More accurate area calculation | Medium |
| Add `pvlib` as offline fallback (no API dependency) | Works without internet | Medium |
| Export results to JSON/CSV for dashboard integration | Data pipeline ready | Low |
| Add panel layout optimizer (avoid low-yield segments) | Cost optimization | Medium |
| Support multiple buildings in one IFC file | Campus-scale analysis | Medium |
| Read building consumption from IFC `Pset_SpaceCommon` | Auto-calculate LEED score | High |

---

## 9. Dependencies

| Package | Version | Used By | Purpose |
|---|---|---|---|
| `ifcopenshell` | ≥ 0.7.0 | `ifc_roof_parser.py` | Open and query IFC files |
| `numpy` | ≥ 1.21.0 | `ifc_roof_parser.py` | Geometry math (normals, areas, clustering) |
| `requests` | ≥ 2.28.0 | `solar_production_engine.py` | HTTP calls to NREL PVWatts API |
| `trimesh` | ≥ 3.0.0 | `ifc_roof_parser.py` | (Optional) mesh utilities — currently using raw numpy |

---

## 10. File Tree

```
iaac-bimwise-starter/
├── 00_data/
│   └── ifc_models/
│       └── Ifc4_SampleHouse_1_Roof.ifc    ← Test IFC file (barrel vault, London)
├── app/
│   └── src/
│       ├── ifc_roof_parser.py              ← NEW: IFC geometry → roof segments
│       ├── solar_production_engine.py      ← NEW: Segments → PVWatts API → kWh
│       ├── run_solar_analysis.py           ← NEW: Orchestrator (parser + engine)
│       ├── ifc_checker.py                  ← Existing: Basic IFC validation
│       └── ifc_visualizer.py               ← Existing: 3D IFC viewer
├── docs/
│   └── SOLAR_PRODUCTION_PIPELINE.md        ← This file
└── requirements.txt                         ← Updated with requests>=2.28.0
```