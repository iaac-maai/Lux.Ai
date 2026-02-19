"""
final_pipeline — Unified IFC metadata extraction + solar production analysis.

Merges:
  - IFC property-set/quantity-set metadata scanner (window, floor, roof area,
    orientation, lat/lon) with multi-exporter alias fallback
  - Geometry-based roof segment parser (per-face tilt & azimuth via 3D mesh)
  - NREL PVWatts v8 solar production engine

Supports single-file solar analysis and batch scanning of project directories.
"""

from final_pipeline.config import __version__

__all__ = ["__version__"]
