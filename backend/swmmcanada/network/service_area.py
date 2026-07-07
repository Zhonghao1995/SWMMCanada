"""Street service corridor + sliver merging (ADR 0017) — the municipal worldview for
synthesis-mode subcatchments.

Real municipal delineation starts from WHERE WATER ENTERS THE SYSTEM: streets collect
(gutters → inlets), lots drain to the street they front, and land beyond a lot's depth is
simply not served by the network. The corridor encodes that: street edges buffered by one
lot depth each side; everything outside is honestly unserved. Inside the corridor, the
ADR 0010 machinery (DEM basins behind the honesty gate, else junction Voronoi) still
decides WHICH junction each piece drains to — terrain is demoted from landlord to referee.
"""
from dataclasses import replace
from typing import Dict, List, Tuple

from shapely.geometry import LineString, Polygon
from shapely.ops import transform as shp_transform, unary_union

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.geo.crs import lonlat_projector, utm_crs_for

# One lot depth each side of the street — the served band. Urban lot depths run ~40-60 m
# (engineering practice; verify against municipal design manuals before citing in print).
LOT_DEPTH_M = 50.0
# A city block whose un-buffered interior is at most this big is served WHOLE: municipal
# grading drains back yards to their fronting street, so a mid-block lens smaller than a
# couple of lots is not "unserved land", it is the middle of the lots themselves. Bigger
# interiors (superblocks, fields ringed by roads) honestly stay unserved.
MAX_INTERIOR_GAP_HA = 0.5
# Size discipline: cells below this merge into a neighbour (typical municipal subcatchments
# are 0.5-10 ha; 0.05 ha = 500 m² is noise from adjacent pour points on one flow path).
MIN_CELL_HA = 0.05


def street_service_corridor(streets, aoi, *, lot_depth_m: float = LOT_DEPTH_M):
    """The served corridor (EPSG:4326): every street edge (node-to-node chord) buffered by
    ``lot_depth_m`` each side in the AOI's metric CRS, dissolved, clipped to the AOI.
    Returns None for a street graph with no edges."""
    to_m = lonlat_projector(utm_crs_for(aoi))
    from pyproj import Transformer

    to_deg = Transformer.from_crs(utm_crs_for(aoi), "EPSG:4326", always_xy=True).transform

    segments = []
    for u, v in streets.edges():
        a, b = streets.nodes[u], streets.nodes[v]
        segments.append(LineString([to_m(a["x"], a["y"]), to_m(b["x"], b["y"])]))
    if not segments:
        return None
    corridor_m = unary_union([seg.buffer(lot_depth_m) for seg in segments])
    aoi_m = shp_transform(to_m, aoi.geometry)
    corridor_m = corridor_m.intersection(aoi_m)
    if corridor_m.is_empty:
        return None
    return shp_transform(to_deg, corridor_m)


def block_aware_service_area(streets, aoi, *, lot_depth_m: float = LOT_DEPTH_M,
                             max_interior_gap_ha: float = MAX_INTERIOR_GAP_HA,
                             buildings=None):
    """The service mask with the municipal block look (ADR 0017 amendment): the street
    corridor PLUS every city block (planar face of the street network) whose interior
    lens beyond the corridor is small — those interiors are the backs of lots that drain
    to their fronting streets, so cells become wall-to-wall block polygons bounded by
    street centrelines instead of street-hugging sausages with mid-block holes."""
    from shapely.ops import polygonize

    to_m = lonlat_projector(utm_crs_for(aoi))
    from pyproj import Transformer

    to_deg = Transformer.from_crs(utm_crs_for(aoi), "EPSG:4326", always_xy=True).transform

    segments = []
    for u, v in streets.edges():
        a, b = streets.nodes[u], streets.nodes[v]
        segments.append(LineString([to_m(a["x"], a["y"]), to_m(b["x"], b["y"])]))
    if not segments:
        return None
    corridor_m = unary_union([seg.buffer(lot_depth_m) for seg in segments])

    buildings_m = None
    if buildings:
        buildings_m = unary_union([shp_transform(to_m, b) for b in buildings])

    served = [corridor_m]
    for face in polygonize(unary_union(segments)):
        gap = face.difference(corridor_m)
        if gap.is_empty or gap.area <= max_interior_gap_ha * 10_000.0:
            served.append(face)               # small lens: the backs of the lots
        elif buildings_m is not None and gap.intersects(buildings_m):
            served.append(face)               # EVIDENCE: buildings in the interior — these
            #                                   are lots whose roofs drain to their street
    mask_m = unary_union(served).intersection(shp_transform(to_m, aoi.geometry))
    if mask_m.is_empty:
        return None
    return shp_transform(to_deg, mask_m)


def merge_slivers(
    subcatchments: List[SubcatchmentIn],
    aoi,
    *,
    min_cell_ha: float = MIN_CELL_HA,
) -> Tuple[List[SubcatchmentIn], dict]:
    """Size discipline (ADR 0017 §3): cells below ``min_cell_ha`` merge into the polygon
    neighbour they share the longest boundary with (area conserved, union geometry).
    Cells without polygons pass through untouched."""
    to_m = lonlat_projector(utm_crs_for(aoi))

    keep: List[SubcatchmentIn] = [s for s in subcatchments if not s.polygon]
    cells = [(s, Polygon([(float(x), float(y)) for x, y in s.polygon]))
             for s in subcatchments if s.polygon]
    cells = [(s, p if p.is_valid else p.buffer(0)) for s, p in cells]

    diag = {"n_merged": 0, "min_cell_ha": min_cell_ha}
    big = [(s, p) for s, p in cells if (s.area_ha or 0.0) >= min_cell_ha]
    small = [(s, p) for s, p in cells if (s.area_ha or 0.0) < min_cell_ha]
    if not big:                                   # nothing to merge into — leave as-is
        return subcatchments, diag

    for s, p in small:
        # neighbour with the longest shared boundary; fall back to nearest.
        best, best_len = None, -1.0
        for i, (bs, bp) in enumerate(big):
            if not p.intersects(bp):
                continue
            shared = shp_transform(to_m, p.intersection(bp)).length
            if shared > best_len:
                best, best_len = i, shared
        if best is None:
            best = min(range(len(big)), key=lambda i: p.distance(big[i][1]))
        bs, bp = big[best]
        merged = unary_union([bp, p])
        if merged.geom_type != "Polygon":         # keep single-ring cells (SWMM [POLYGONS])
            polys = [g for g in getattr(merged, "geoms", []) if g.geom_type == "Polygon"]
            merged = max(polys, key=lambda g: g.area) if polys else bp
        big[best] = (
            replace(bs, area_ha=(bs.area_ha or 0.0) + (s.area_ha or 0.0),
                    polygon=[(float(x), float(y)) for x, y in merged.exterior.coords]),
            merged,
        )
        diag["n_merged"] += 1

    keep.extend(s for s, _ in big)
    return keep, diag
