"""
config.py — Shared constants and defaults for the solar pipeline.
"""

from pathlib import Path

__version__ = "1.0.0"

# ── Paths ─────────────────────────────────────────────────────────────────────

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
KEY_ALIASES_PATH = PACKAGE_DIR / "key_aliases.json"
DEFAULT_SAMPLE_ROOT = REPO_ROOT / "Sample projects" / "projects"

# ── NREL PVWatts v8 API ──────────────────────────────────────────────────────

NREL_API_KEY = "0zwEIS1adJrx658O3gjQYfI7AprKLjQf4KP420o9"
PVWATTS_BASE_URL = "https://developer.nrel.gov/api/pvwatts/v8.json"

# ── Solar panel assumptions ───────────────────────────────────────────────────

PANEL_EFFICIENCY = 0.20          # 1 kW per 5 m² of panel
ARRAY_TYPE = 1                   # Fixed — roof mount
MODULE_TYPE = 1                  # Premium (monocrystalline)
SYSTEM_LOSSES = 14               # % (wiring, soiling, mismatch, etc.)

# ── Roof geometry clustering ─────────────────────────────────────────────────

DEFAULT_ANGLE_TOLERANCE_DEG = 15.0   # max normal deviation within a cluster
MIN_SEGMENT_AREA_M2 = 1.0           # ignore clusters smaller than this

# ── LEED / energy benchmarks ─────────────────────────────────────────────────

DEFAULT_CONSUMPTION_KWH_PER_M2 = 150   # ASHRAE typical office (kWh/m²/yr)
FALLBACK_CONSUMPTION_KWH = 50_000      # used when floor area is unknown

# ── CSV output columns ───────────────────────────────────────────────────────

CSV_COLUMNS = [
    "project_name",
    "ifc_file",
    "window_area_m2",
    "floor_area_m2",
    "roof_area_m2",
    "true_north_angle_deg",
    "latitude",
    "longitude",
    "error",
]
