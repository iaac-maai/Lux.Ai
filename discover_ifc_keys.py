"""
discover_ifc_keys.py
Scans all IFC files and inventories every property set and quantity set
name used per element type, producing:
  - ifc_key_inventory.json  (raw: all keys found, with file counts)
  - key_aliases.json         (canonical: standard key → all alias paths)

Run:
    .venv/Scripts/python.exe discover_ifc_keys.py
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

import ifcopenshell
from tabulate import tabulate


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Element types to inventory ────────────────────────────────────────────────

TARGET_TYPES = [
    "IfcWindow",
    "IfcDoor",
    "IfcSpace",
    "IfcSlab",
    "IfcRoof",
    "IfcWall",
    "IfcWallStandardCase",
    "IfcCovering",
    "IfcSite",
    "IfcBuilding",
    "IfcBuildingStorey",
]

# Keywords that flag a property/quantity as area-related for highlighting
AREA_KEYWORDS = {"area", "fläche", "superficie", "superficie"}
HEIGHT_KEYWORDS = {"height", "höhe", "width", "length", "depth", "perimeter"}
ORIENTATION_KEYWORDS = {"north", "latitude", "longitude", "orientation", "azimuth"}


# ── Inventory data structure ──────────────────────────────────────────────────
#
# global_inventory[element_type]["quantity_sets"][qset_name][qty_name]
#   = {"file_count": int, "projects": [str, ...]}
# global_inventory[element_type]["property_sets"][pset_name][prop_name]
#   = {"file_count": int, "projects": [str, ...]}

def _empty_entry() -> dict:
    return {"file_count": 0, "projects": []}


def make_inventory() -> dict:
    return {
        t: {"quantity_sets": defaultdict(lambda: defaultdict(_empty_entry)),
            "property_sets": defaultdict(lambda: defaultdict(_empty_entry))}
        for t in TARGET_TYPES
    }


# ── Per-element collection ────────────────────────────────────────────────────

def collect_qsets(model: ifcopenshell.file, element_type: str) -> dict:
    """
    Returns {qset_name: {qty_name: count_in_this_file}} for all
    instances of element_type in this model.
    """
    result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for elem in model.by_type(element_type):
        for rel in getattr(elem, "IsDefinedBy", []):
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            defn = rel.RelatingPropertyDefinition
            if not defn.is_a("IfcElementQuantity"):
                continue
            qset_name = defn.Name or "(unnamed)"
            for qty in defn.Quantities:
                qty_name = qty.Name or "(unnamed)"
                result[qset_name][qty_name] += 1
    return result


def collect_psets(model: ifcopenshell.file, element_type: str) -> dict:
    """
    Returns {pset_name: {prop_name: count_in_this_file}} for all
    instances of element_type in this model.
    """
    result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for elem in model.by_type(element_type):
        for rel in getattr(elem, "IsDefinedBy", []):
            if not rel.is_a("IfcRelDefinesByProperties"):
                continue
            defn = rel.RelatingPropertyDefinition
            if not defn.is_a("IfcPropertySet"):
                continue
            pset_name = defn.Name or "(unnamed)"
            for prop in getattr(defn, "HasProperties", []):
                prop_name = getattr(prop, "Name", None) or "(unnamed)"
                result[pset_name][prop_name] += 1
    return result


# ── Merge into global inventory ───────────────────────────────────────────────

def merge_into(global_inv: dict, element_type: str, section: str,
               file_data: dict, project_name: str) -> None:
    """
    Merge one file's {set_name: {key_name: count}} into the global inventory,
    incrementing file_count and appending project_name.
    """
    target = global_inv[element_type][section]
    for set_name, keys in file_data.items():
        for key_name in keys:
            entry = target[set_name][key_name]
            entry["file_count"] += 1
            if project_name not in entry["projects"]:
                entry["projects"].append(project_name)


# ── Serialisation helpers ─────────────────────────────────────────────────────

def inventory_to_plain(global_inv: dict) -> dict:
    """Convert defaultdict structure to plain dicts for JSON serialisation."""
    out = {}
    for etype, sections in global_inv.items():
        out[etype] = {}
        for section, sets in sections.items():
            out[etype][section] = {}
            for set_name, keys in sets.items():
                out[etype][section][set_name] = {}
                for key_name, entry in keys.items():
                    out[etype][section][set_name][key_name] = {
                        "file_count": entry["file_count"],
                        "projects": sorted(entry["projects"]),
                    }
    return out


# ── Build key_aliases.json ────────────────────────────────────────────────────

def build_aliases(inventory: dict) -> dict:
    """
    Build the canonical key → alias-list mapping.
    Each alias is tried in order; the first one that yields data wins.
    Pre-seeds with known patterns, then extends with anything discovered
    in the inventory that looks area-related but isn't already covered.
    """
    aliases: dict[str, list[dict]] = {
        "window_area": [
            {"entity": "IfcWindow", "source": "qset",
             "set_name": "Qto_WindowBaseQuantities", "key": "Area"},
            {"entity": "IfcWindow", "source": "qset",
             "set_name": "BaseQuantities", "key": "Area"},
            {"entity": "IfcWindow", "source": "qset",
             "set_name": "BaseQuantities", "key": "GrossArea"},
            {"entity": "IfcWindow", "source": "attr",
             "keys": ["OverallHeight", "OverallWidth"], "op": "multiply"},
        ],
        "floor_area": [
            {"entity": "IfcSpace", "source": "qset",
             "set_name": "Qto_SpaceBaseQuantities", "key": "NetFloorArea"},
            {"entity": "IfcSpace", "source": "qset",
             "set_name": "Qto_SpaceBaseQuantities", "key": "GrossFloorArea"},
            {"entity": "IfcSpace", "source": "qset",
             "set_name": "BaseQuantities", "key": "NetFloorArea"},
            {"entity": "IfcSpace", "source": "qset",
             "set_name": "BaseQuantities", "key": "GrossFloorArea"},
            {"entity": "IfcSpace", "source": "qset",
             "set_name": "GSA Space Areas", "key": "GSA BIM Area"},
            {"entity": "IfcSlab",  "source": "qset",
             "set_name": "Qto_SlabBaseQuantities", "key": "NetArea",
             "predefined_type": "FLOOR"},
            {"entity": "IfcSlab",  "source": "qset",
             "set_name": "BaseQuantities", "key": "NetArea",
             "predefined_type": "FLOOR"},
            {"entity": "IfcSlab",  "source": "qset",
             "set_name": "BaseQuantities", "key": "GrossArea",
             "predefined_type": "FLOOR"},
        ],
        "roof_area": [
            {"entity": "IfcRoof", "source": "qset",
             "set_name": "Qto_RoofBaseQuantities", "key": "NetArea"},
            {"entity": "IfcRoof", "source": "qset",
             "set_name": "Qto_RoofBaseQuantities", "key": "GrossArea"},
            {"entity": "IfcRoof", "source": "qset",
             "set_name": "BaseQuantities", "key": "NetArea"},
            {"entity": "IfcSlab",  "source": "qset",
             "set_name": "Qto_SlabBaseQuantities", "key": "NetArea",
             "predefined_type": "ROOF"},
            {"entity": "IfcSlab",  "source": "qset",
             "set_name": "Qto_SlabBaseQuantities", "key": "GrossArea",
             "predefined_type": "ROOF"},
            {"entity": "IfcSlab",  "source": "qset",
             "set_name": "BaseQuantities", "key": "NetArea",
             "predefined_type": "ROOF"},
            {"entity": "IfcSlab",  "source": "qset",
             "set_name": "BaseQuantities", "key": "GrossArea",
             "predefined_type": "ROOF"},
        ],
        "true_north_angle": [
            {"entity": "IfcGeometricRepresentationContext",
             "source": "attr", "key": "TrueNorth",
             "note": "DirectionRatios (X,Y) → atan2(x,y) → compass bearing"},
        ],
        "latitude": [
            {"entity": "IfcSite", "source": "attr", "key": "RefLatitude",
             "note": "IfcCompoundPlaneAngleMeasure [deg,min,sec,microsec]"},
        ],
        "longitude": [
            {"entity": "IfcSite", "source": "attr", "key": "RefLongitude",
             "note": "IfcCompoundPlaneAngleMeasure [deg,min,sec,microsec]"},
        ],
    }

    # Auto-extend: scan the inventory for any quantity set / property set
    # entries that look area-related and are not already covered above.
    already_covered: set[tuple] = set()
    for canonical, alias_list in aliases.items():
        for a in alias_list:
            if a["source"] == "qset":
                already_covered.add(
                    (a["entity"], "quantity_sets", a["set_name"], a["key"])
                )

    auto_discovered: list[dict] = []
    for etype, sections in inventory.items():
        for set_name, keys in sections.get("quantity_sets", {}).items():
            for key_name, entry in keys.items():
                if entry["file_count"] == 0:
                    continue
                key_lower = key_name.lower()
                is_area = any(kw in key_lower for kw in AREA_KEYWORDS)
                if not is_area:
                    continue
                tag = (etype, "quantity_sets", set_name, key_name)
                if tag in already_covered:
                    continue
                auto_discovered.append({
                    "entity": etype,
                    "source": "qset",
                    "set_name": set_name,
                    "key": key_name,
                    "file_count": entry["file_count"],
                    "projects": entry["projects"],
                    "auto_discovered": True,
                })

    if auto_discovered:
        aliases["_auto_discovered_area_keys"] = auto_discovered

    return aliases


# ── Console reporting ─────────────────────────────────────────────────────────

def print_report(inventory: dict) -> None:
    """Print a per-element-type summary table to the console."""
    HIGHLIGHT = {"area", "fläche", "netfloorarea", "grossfloorarea",
                 "netarea", "grossarea", "grossfootprintarea"}

    for etype in TARGET_TYPES:
        data = inventory.get(etype, {})
        qsets = data.get("quantity_sets", {})
        psets = data.get("property_sets", {})

        if not qsets and not psets:
            continue

        print(f"\n{'='*70}")
        print(f"  {etype}")
        print(f"{'='*70}")

        # Quantity sets
        if qsets:
            print("\n  QUANTITY SETS:")
            rows = []
            for set_name, keys in sorted(qsets.items()):
                for key_name, entry in sorted(
                    keys.items(),
                    key=lambda x: -x[1]["file_count"]
                ):
                    flag = "*" if key_name.lower() in HIGHLIGHT else " "
                    rows.append([
                        flag,
                        set_name,
                        key_name,
                        entry["file_count"],
                        ", ".join(entry["projects"][:5]) +
                        ("..." if len(entry["projects"]) > 5 else ""),
                    ])
            print(tabulate(
                rows,
                headers=["!", "QSet Name", "Quantity", "Files", "Projects"],
                tablefmt="simple",
            ))

        # Property sets (condensed — just set name + count of unique props)
        if psets:
            print("\n  PROPERTY SETS (summary):")
            rows = []
            for set_name, keys in sorted(psets.items()):
                max_files = max(e["file_count"] for e in keys.values())
                rows.append([set_name, len(keys), max_files])
            print(tabulate(
                rows,
                headers=["PSet Name", "# Props", "Max Files"],
                tablefmt="simple",
            ))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    root = Path(__file__).parent / "Sample projects" / "projects"
    ifc_files = sorted(root.rglob("*.ifc"))
    log.info(f"Found {len(ifc_files)} IFC files")

    global_inv = make_inventory()

    for i, ifc_path in enumerate(ifc_files, 1):
        project = ifc_path.parent.name
        log.info(f"[{i}/{len(ifc_files)}] {project}/{ifc_path.name}")
        try:
            model = ifcopenshell.open(str(ifc_path))
        except Exception as exc:
            log.warning(f"  Cannot open: {exc}")
            continue

        for etype in TARGET_TYPES:
            try:
                qdata = collect_qsets(model, etype)
                merge_into(global_inv, etype, "quantity_sets", qdata, project)
            except Exception as exc:
                log.debug(f"  qset {etype}: {exc}")

            try:
                pdata = collect_psets(model, etype)
                merge_into(global_inv, etype, "property_sets", pdata, project)
            except Exception as exc:
                log.debug(f"  pset {etype}: {exc}")

    # Serialise inventory
    plain_inv = inventory_to_plain(global_inv)
    inv_path = Path(__file__).parent / "ifc_key_inventory.json"
    with open(inv_path, "w", encoding="utf-8") as f:
        json.dump(plain_inv, f, indent=2, ensure_ascii=False)
    log.info(f"Inventory written to: {inv_path}")

    # Build and write alias map
    aliases = build_aliases(plain_inv)
    alias_path = Path(__file__).parent / "key_aliases.json"
    with open(alias_path, "w", encoding="utf-8") as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)
    log.info(f"Alias map written to: {alias_path}")

    # Print console report
    print_report(plain_inv)

    # Final summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for etype in TARGET_TYPES:
        qsets = plain_inv.get(etype, {}).get("quantity_sets", {})
        psets = plain_inv.get(etype, {}).get("property_sets", {})
        if qsets or psets:
            total_qkeys = sum(len(v) for v in qsets.values())
            total_pkeys = sum(len(v) for v in psets.values())
            print(f"  {etype:<30} "
                  f"{len(qsets)} qsets / {total_qkeys} qty-keys  |  "
                  f"{len(psets)} psets / {total_pkeys} prop-keys")


if __name__ == "__main__":
    main()
