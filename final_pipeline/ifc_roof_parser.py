"""
ifc_roof_parser.py — Geometry-based roof segment extraction from IFC files.

Analyses the 3D triangulated mesh of roof elements to produce per-segment
tilt, azimuth, and area values that the solar production engine consumes.

Algorithm:
    1. Find all IfcRoof + decomposed IfcSlab children + standalone ROOF slabs
    2. Triangulate each element via ifcopenshell.geom (world coordinates)
    3. Compute face normals and triangle areas
    4. Cluster upward-facing triangles by normal direction (angular tolerance)
    5. Compute area-weighted average tilt & azimuth per cluster
    6. Apply true-north rotation so azimuths are real-world compass bearings

Cross-validates geometry-derived total area against property-set roof_area.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np

import ifcopenshell
import ifcopenshell.geom

from final_pipeline.config import DEFAULT_ANGLE_TOLERANCE_DEG, MIN_SEGMENT_AREA_M2
from final_pipeline.ifc_metadata_extractor import (
    extract_roof_area,
    extract_true_north,
    get_area_scale,
)

log = logging.getLogger(__name__)


# ── Element discovery ─────────────────────────────────────────────────────────

def get_roof_elements(model: ifcopenshell.file) -> list:
    """
    Collect all roof-related IFC elements:
      - IfcRoof entities (decomposed through IfcRelAggregates if present)
      - Standalone IfcSlab with PredefinedType == ROOF
    Returns leaf elements (slabs or monolithic roofs) ready for geometry extraction.
    """
    elements: list = []
    seen_ids: set[int] = set()

    # IfcRoof → decomposed sub-elements
    for roof in model.by_type("IfcRoof"):
        children = _get_decomposed_children(roof)
        if children:
            for child in children:
                if child.id() not in seen_ids:
                    elements.append(child)
                    seen_ids.add(child.id())
        else:
            # Monolithic roof — process the IfcRoof itself
            if roof.id() not in seen_ids:
                elements.append(roof)
                seen_ids.add(roof.id())

    # Standalone IfcSlab with ROOF type (not already captured)
    for slab in model.by_type("IfcSlab"):
        pt = getattr(slab, "PredefinedType", None)
        if pt == "ROOF" and slab.id() not in seen_ids:
            elements.append(slab)
            seen_ids.add(slab.id())

    log.info("  Found %d roof element(s) for geometry extraction", len(elements))
    return elements


def _get_decomposed_children(element) -> list:
    """Walk IfcRelAggregates to find child elements (e.g. IfcSlab under IfcRoof)."""
    children: list = []
    for rel in getattr(element, "IsDecomposedBy", []):
        if rel.is_a("IfcRelAggregates"):
            children.extend(rel.RelatedObjects)
    return children


# ── Geometry extraction ───────────────────────────────────────────────────────

def _make_geom_settings() -> ifcopenshell.geom.settings:
    """Create geometry settings for triangulated mesh in world coordinates."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    return settings


def extract_geometry(
    element,
    settings: ifcopenshell.geom.settings,
) -> tuple[np.ndarray, np.ndarray] | None:
    """
    Triangulate an IFC element and return (vertices, faces).

    vertices: (N, 3) float64 — XYZ coordinates
    faces:    (M, 3) int      — triangle vertex indices

    Returns None if geometry extraction fails.
    """
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
    except Exception as exc:
        log.warning("  Geometry extraction failed for #%d (%s): %s",
                    element.id(), element.is_a(), exc)
        return None

    # ifcopenshell returns flat arrays
    verts_flat = shape.geometry.verts
    faces_flat = shape.geometry.faces

    if not verts_flat or not faces_flat:
        return None

    vertices = np.array(verts_flat, dtype=np.float64).reshape(-1, 3)
    faces = np.array(faces_flat, dtype=np.int32).reshape(-1, 3)

    return vertices, faces


# ── Normal & area computation ─────────────────────────────────────────────────

def compute_face_normals(
    vertices: np.ndarray,
    faces: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute unit normals and areas for every triangle.

    Returns:
        normals: (M, 3) float64 — unit normal per face
        areas:   (M,)   float64 — area per face in model units²
    """
    v0 = vertices[faces[:, 0]]
    v1 = vertices[faces[:, 1]]
    v2 = vertices[faces[:, 2]]

    edge1 = v1 - v0
    edge2 = v2 - v0
    cross = np.cross(edge1, edge2)

    magnitudes = np.linalg.norm(cross, axis=1, keepdims=True)
    # Avoid division by zero for degenerate triangles
    safe_mag = np.where(magnitudes > 1e-12, magnitudes, 1e-12)
    normals = cross / safe_mag
    areas = (magnitudes.flatten() / 2.0)

    return normals, areas


# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_faces_by_normal(
    normals: np.ndarray,
    areas: np.ndarray,
    angle_tolerance: float = DEFAULT_ANGLE_TOLERANCE_DEG,
) -> list[list[int]]:
    """
    Greedy angular clustering of upward-facing triangles.

    Only considers triangles where normal Z > 0 (upward-facing — solar relevant).
    Groups triangles whose normals are within *angle_tolerance* degrees.

    Returns list of clusters, each a list of face indices.
    """
    cos_tol = math.cos(math.radians(angle_tolerance))
    clusters: list[list[int]] = []
    cluster_normals: list[np.ndarray] = []

    # Filter to upward-facing triangles
    upward_mask = normals[:, 2] > 0
    upward_indices = np.where(upward_mask)[0]

    for idx in upward_indices:
        n = normals[idx]
        placed = False
        for ci, cn in enumerate(cluster_normals):
            dot = float(np.dot(n, cn))
            if dot >= cos_tol:
                clusters[ci].append(int(idx))
                # Update cluster normal (area-weighted running average)
                total_area = sum(areas[i] for i in clusters[ci])
                if total_area > 0:
                    weighted = sum(areas[i] * normals[i] for i in clusters[ci])
                    mag = np.linalg.norm(weighted)
                    if mag > 1e-12:
                        cluster_normals[ci] = weighted / mag
                placed = True
                break

        if not placed:
            clusters.append([int(idx)])
            cluster_normals.append(n.copy())

    return clusters


# ── Segment properties ────────────────────────────────────────────────────────

def compute_segment_properties(
    normals: np.ndarray,
    areas: np.ndarray,
    cluster_indices: list[int],
    area_scale: float = 1.0,
) -> dict:
    """
    Compute tilt, azimuth, and total area for one cluster of faces.

    tilt:    degrees from horizontal (0° = flat, 90° = vertical)
    azimuth: degrees clockwise from north (0° N, 90° E, 180° S, 270° W)
             — in MODEL coordinates (true-north correction applied later)
    area:    cluster area in m²

    Returns dict with keys: area, tilt, azimuth.
    """
    idx = np.array(cluster_indices)
    cluster_areas = areas[idx]
    cluster_normals = normals[idx]

    total_area = float(cluster_areas.sum()) * area_scale

    # Area-weighted average normal
    weighted = (cluster_normals.T * cluster_areas).T.sum(axis=0)
    mag = np.linalg.norm(weighted)
    if mag < 1e-12:
        return {"area": total_area, "tilt": 0.0, "azimuth": 0.0}
    avg_n = weighted / mag

    # Tilt = arccos(nz)
    tilt = math.degrees(math.acos(min(max(avg_n[2], -1.0), 1.0)))

    # Azimuth = atan2(nx, ny) mod 360
    # Convention: 0° = North (+Y), 90° = East (+X), 180° = South (-Y)
    azimuth = math.degrees(math.atan2(avg_n[0], avg_n[1])) % 360.0

    return {
        "area": round(total_area, 2),
        "tilt": round(tilt, 1),
        "azimuth": round(azimuth, 1),
    }


# ── Main entry point ─────────────────────────────────────────────────────────

def parse_roof_segments(
    ifc_path: str | Path,
    angle_tolerance: float = DEFAULT_ANGLE_TOLERANCE_DEG,
    min_area: float = MIN_SEGMENT_AREA_M2,
    apply_true_north: bool = True,
) -> list[dict]:
    """
    Parse an IFC file and return a list of roof segments.

    Each segment dict has keys: id, area, tilt, azimuth, global_id, ifc_type.

    Parameters
    ----------
    ifc_path : path to the .ifc file
    angle_tolerance : degrees — max normal deviation within a cluster
    min_area : m² — ignore clusters smaller than this
    apply_true_north : rotate azimuths by the model's TrueNorth angle

    Returns
    -------
    list of dicts: [{"id": str, "area": float, "tilt": float, "azimuth": float,
                     "global_id": str|None, "ifc_type": str|None}, ...]
    """
    ifc_path = Path(ifc_path)
    log.info("Parsing roof geometry: %s", ifc_path.name)

    try:
        model = ifcopenshell.open(str(ifc_path))
    except Exception as exc:
        log.error("Cannot open %s: %s", ifc_path, exc)
        return []

    area_scale = get_area_scale(model)
    # For geometry (vertex coordinates) we need the length scale squared
    # because area = length². However ifcopenshell.geom with world-coords
    # already applies the unit conversion, so we assume metres here.
    # If models are in feet, the geom API still returns metres when
    # USE_WORLD_COORDS is True.  We keep area_scale for the cross-validation
    # with property-set values only.
    geom_area_scale = 1.0  # world-coords are in metres

    elements = get_roof_elements(model)
    if not elements:
        log.warning("  No roof elements found in %s", ifc_path.name)
        return []

    settings = _make_geom_settings()

    # Collect all face normals + areas across all roof elements
    # Also track which element each face belongs to (for GlobalId propagation)
    all_normals: list[np.ndarray] = []
    all_areas: list[np.ndarray] = []
    all_face_elem_ids: list[str] = []       # GlobalId per face
    all_face_elem_types: list[str] = []     # IFC type per face

    for elem in elements:
        geom_data = extract_geometry(elem, settings)
        if geom_data is None:
            continue
        verts, faces = geom_data
        normals, areas = compute_face_normals(verts, faces)
        all_normals.append(normals)
        all_areas.append(areas)
        # Tag every face with its source element
        gid = getattr(elem, "GlobalId", None) or ""
        etype = elem.is_a() if hasattr(elem, "is_a") else ""
        all_face_elem_ids.extend([gid] * len(areas))
        all_face_elem_types.extend([etype] * len(areas))

    if not all_normals:
        log.warning("  Could not extract geometry from any roof element in %s",
                    ifc_path.name)
        return []

    normals = np.vstack(all_normals)
    areas = np.concatenate(all_areas)

    # Cluster faces by orientation
    clusters = cluster_faces_by_normal(normals, areas, angle_tolerance)

    # Build segment list
    segments: list[dict] = []
    seg_idx = 1
    for cluster in clusters:
        props = compute_segment_properties(normals, areas, cluster, geom_area_scale)
        if props["area"] < min_area:
            continue
        props["id"] = f"Roof_Seg_{seg_idx:02d}"

        # Determine dominant element in this cluster (majority by area)
        elem_area: dict[str, float] = {}
        elem_type_map: dict[str, str] = {}
        for fi in cluster:
            gid = all_face_elem_ids[fi]
            elem_area[gid] = elem_area.get(gid, 0.0) + float(areas[fi])
            elem_type_map[gid] = all_face_elem_types[fi]
        dominant_gid = max(elem_area, key=elem_area.get) if elem_area else ""
        props["global_id"] = dominant_gid or None
        props["ifc_type"] = elem_type_map.get(dominant_gid) or None

        segments.append(props)
        seg_idx += 1

    # ── True-north azimuth correction ─────────────────────────────────────
    if apply_true_north and segments:
        tn = extract_true_north(model)
        if tn is not None and abs(tn) > 0.01 and abs(tn - 360.0) > 0.01:
            log.info("  Applying true-north correction: %.2f°", tn)
            for seg in segments:
                seg["azimuth"] = round((seg["azimuth"] + tn) % 360.0, 1)

    # ── Cross-validate against property-set roof area ─────────────────────
    if segments:
        geom_total = sum(s["area"] for s in segments)
        pset_area = extract_roof_area(model)
        if pset_area is not None and pset_area > 0:
            diff_pct = abs(geom_total - pset_area) / pset_area * 100
            if diff_pct > 20:
                log.warning(
                    "  Roof area mismatch: geometry=%.1f m², property-set=%.1f m² (%.0f%% diff)",
                    geom_total, pset_area, diff_pct,
                )
            else:
                log.info(
                    "  Roof area validated: geometry=%.1f m², property-set=%.1f m² (%.0f%% diff)",
                    geom_total, pset_area, diff_pct,
                )

    log.info("  Extracted %d roof segment(s) from %s", len(segments), ifc_path.name)
    return segments
