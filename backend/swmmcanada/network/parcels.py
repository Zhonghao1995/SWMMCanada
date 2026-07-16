"""Snap synthesis cells to cadastral lot lines (ADR 0023 cut 2, #138).

The frontage split (ADR 0017/0022) draws geometric mid-block divides; a municipal
engineer draws PARCEL lines. Where an open parcel fabric exists, each lot joins the
junction whose geometric cell it most overlaps — a lot fronts one street, so this is the
same "drains to the street it faces" logic with real cadastre instead of a Voronoi
midline. The non-parcel remainder (road surface, unparcelled slivers) stays with its
geometric cell, so coverage is conserved exactly; a snap that would fragment a cell
re-attaches the scraps to the neighbour sharing the longest boundary (the edge_split
convention).
"""
from dataclasses import replace
from typing import Dict, List, Tuple

from shapely.geometry import Polygon, shape
from shapely.ops import transform as shp_transform, unary_union

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.geo.crs import lonlat_projector, utm_crs_for

_MIN_SCRAP_M2 = 25.0


def snap_subcatchments_to_parcels(
    subcatchments: List[SubcatchmentIn],
    parcels: List[dict],
    aoi,
) -> Tuple[List[SubcatchmentIn], dict]:
    """Reshape cell polygons onto parcel boundaries; areas re-measured, outlets/params
    untouched. Returns ``(new_subcatchments, diagnostics)``; without parcels this is a
    documented no-op (non-BC provinces, WFS failure)."""
    diag = {"applied": False, "n_parcels": len(parcels or []), "n_cells_reshaped": 0,
            "source": "ParcelMap BC (OGL-BC)" if parcels else "none (geometric cells)"}
    with_poly = [s for s in subcatchments if s.polygon]
    if not parcels or len(with_poly) < 2:
        return list(subcatchments), diag

    to_m = lonlat_projector(utm_crs_for(aoi))
    from pyproj import Transformer

    to_deg = Transformer.from_crs(utm_crs_for(aoi), "EPSG:4326", always_xy=True).transform

    cells_m: Dict[str, Polygon] = {}
    for s in with_poly:
        g = shp_transform(to_m, Polygon([(float(x), float(y)) for x, y in s.polygon])).buffer(0)
        if not g.is_empty:
            cells_m[s.name] = g
    if len(cells_m) < 2:
        return list(subcatchments), diag

    from shapely import STRtree

    names = list(cells_m)
    tree = STRtree([cells_m[n] for n in names])

    parcels_m = []
    for f in parcels:
        try:
            g = shp_transform(to_m, shape(f["geometry"])).buffer(0)
        except Exception:  # noqa: BLE001 — one bad ring must not kill the snap
            continue
        if not g.is_empty and g.area > 0:
            parcels_m.append(g)
    if not parcels_m:
        return list(subcatchments), diag

    # each parcel -> the cell it most overlaps (a lot fronts exactly one street)
    assigned: Dict[str, list] = {}
    for g in parcels_m:
        idxs = tree.query(g, predicate="intersects")
        best, best_area = None, 0.0
        for i in idxs:
            ov = g.intersection(cells_m[names[int(i)]]).area
            if ov > best_area:
                best, best_area = names[int(i)], ov
        if best is not None:
            assigned.setdefault(best, []).append(g)

    all_parcels = unary_union(parcels_m)
    picked: Dict[str, Polygon] = {}
    scraps: List[Polygon] = []
    for name, cell in cells_m.items():
        base = cell.difference(all_parcels)          # roads + unparcelled slivers stay home
        mine = unary_union(assigned.get(name, []) + ([base] if not base.is_empty else []))
        mine = mine.intersection(shp_transform(to_m, aoi.geometry)).buffer(0)
        parts = [p for p in (mine.geoms if hasattr(mine, "geoms") else [mine])
                 if p.geom_type == "Polygon" and not p.is_empty]
        if not parts:
            continue
        main = max(parts, key=lambda p: p.area)
        scraps.extend(p for p in parts if p is not main and p.area >= _MIN_SCRAP_M2)
        picked[name] = main

    for scrap in scraps:                              # fragments re-attach to a neighbour
        best, best_len = None, 0.0
        for name, cell in picked.items():
            if not scrap.intersects(cell):
                continue
            shared = scrap.intersection(cell).length
            if shared > best_len:
                best, best_len = name, shared
        if best is None:
            continue
        merged = unary_union([picked[best], scrap])
        if merged.geom_type == "Polygon":
            picked[best] = merged

    out: List[SubcatchmentIn] = []
    n_reshaped = n_kept_original = 0
    for s in subcatchments:
        g = picked.get(s.name)
        if g is None or not s.polygon:
            out.append(s)
            continue
        # sanitize before shipping (the ADR 0016 lesson: union/difference products must
        # be buffer(0)-cleaned and validity-checked in metric, or downstream validation
        # rightly kills the build); a cell the snap degenerated keeps its geometric shape
        g = g.buffer(0)
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda q: q.area)
        if g.is_empty or g.geom_type != "Polygon" or g.area < _MIN_SCRAP_M2:
            out.append(s)
            n_kept_original += 1
            continue
        # Net-land bookkeeping (F-005/ADR 0024 §4): hydrology uses the pre-strip area
        # (holes = enclosed foreign lots, not our runoff), width scales with area.
        net_m2 = g.area
        # SubcatchmentIn carries one exterior ring, so holes are stripped IN METRIC and
        # the ring re-cleaned there: a valid polygon's exterior can self-touch where a
        # hole met the boundary, and reprojection rounding then breaks 4326 validity.
        g = Polygon(g.exterior).buffer(0)
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda q: q.area)
        g_deg = shp_transform(to_deg, g)
        ring = [(float(x), float(y)) for x, y in g_deg.exterior.coords]
        check = Polygon(ring).buffer(0)               # final gate in the stored CRS...
        check_m = shp_transform(to_m, check) if (not check.is_empty and
                                                 check.geom_type == "Polygon") else None
        # ...AND back in metric: validation reprojects before checking, and a ring that is
        # barely valid in degrees can self-intersect in metres (the Duncan lesson).
        if (check_m is None or check.is_empty or check.geom_type != "Polygon"
                or not check.is_valid or check_m.is_empty or not check_m.is_valid):
            out.append(s)
            n_kept_original += 1
            continue
        orig_m2 = cells_m[s.name].area if s.name in cells_m else 0.0
        scale = (net_m2 / orig_m2) if orig_m2 > 0 else 1.0
        out.append(replace(
            s, polygon=[(float(x), float(y)) for x, y in check.exterior.coords],
            area_ha=round(net_m2 / 10_000.0, 4),
            width_m=round(s.width_m * scale, 2)))
        n_reshaped += 1

    diag.update(applied=True, n_cells_reshaped=n_reshaped, n_kept_geometric=n_kept_original)
    return out, diag
