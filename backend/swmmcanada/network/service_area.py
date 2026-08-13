"""Street frontage split + sliver merging (ADR 0017 / ADR 0022) — the municipal look for
synthesis-mode subcatchments.

ADR 0022 (#118) reversed the CORRIDOR's exclusion semantics: the pipeline now passes the
whole AOI as the mask, so every piece of land is a subcatchment — forests and deep lots
participate with landcover-driven parameters (low imperviousness, high infiltration)
instead of being deleted, exactly like rain physics wants. What survives from ADR 0017 is
the LOOK: ``edge_split_cells`` still assigns ground to its nearest street segment with
midpoint gutter divides (the rectangular hand-drawn municipal cells). The corridor
functions below are retained for diagnostics and the city-path fallbacks.
"""
from dataclasses import replace
from typing import Dict, List, Tuple

from shapely.geometry import LineString, Polygon
from shapely.ops import transform as shp_transform, unary_union

from swmmcanada.build.models import SurfaceCatchment
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
    subcatchments: List[SurfaceCatchment],
    aoi,
    *,
    min_cell_ha: float = MIN_CELL_HA,
) -> Tuple[List[SurfaceCatchment], dict]:
    """Size discipline (ADR 0017 §3): cells below ``min_cell_ha`` merge into the polygon
    neighbour they share the longest boundary with (area conserved, union geometry).
    Cells without polygons pass through untouched."""
    to_m = lonlat_projector(utm_crs_for(aoi))

    keep: List[SurfaceCatchment] = [s for s in subcatchments if not s.polygon]
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


def _inlet_fractions(inlet_m, a, b, *, corridor_m: float = 20.0):
    """Positions along an edge, as fractions, where a published inlet sits beside it."""
    import numpy as np

    if inlet_m is None or not len(inlet_m):
        return []
    ab = np.array([b[0] - a[0], b[1] - a[1]], dtype="float64")
    l2 = float(ab @ ab)
    if l2 <= 0:
        return []
    rel = inlet_m - np.array(a, dtype="float64")
    t = np.clip((rel @ ab) / l2, 0.0, 1.0)
    proj = np.array(a, dtype="float64") + t[:, None] * ab
    near = np.hypot(*(inlet_m - proj).T) <= corridor_m
    return sorted(float(x) for x in t[near])


def _first_inlet_downhill(catches, t, crest):
    """The first inlet reached going downhill from ``t``, or ``None`` if the stretch runs
    clear to the end of the segment."""
    if not catches:
        return None
    if t < crest:                       # falls towards a
        below = [c for c in catches if c <= t]
        return max(below) if below else None
    above = [c for c in catches if c >= t]
    return min(above) if above else None


def _crest_fraction(elevation, lon_a, lat_a, lon_b, lat_b, n_samples):
    """Where along an edge the ground is highest, as a fraction from a to b, or ``None``.

    ``None`` means "no usable ground here" and the caller keeps the geometric midpoint — a
    surface that cannot be read must cost the refinement, never the delineation. A crest at
    an end means the street falls the whole way and the far node takes all of it.
    """
    if elevation is None:
        return None
    try:
        zs = []
        for i in range(n_samples + 1):
            t = i / n_samples
            zs.append(float(elevation(lon_a + t * (lon_b - lon_a),
                                      lat_a + t * (lat_b - lat_a))))
    except Exception:  # noqa: BLE001 — an unreadable surface is not a failed delineation
        return None
    if not zs or max(zs) - min(zs) <= 0:
        return None
    return zs.index(max(zs)) / n_samples


def edge_split_cells(streets, junction_xy, mask, aoi, *, sample_step_m: float = 10.0,
                     elevation=None, inlets=None):
    """Municipal split (ADR 0017 amendment 3): assign ground to the nearest STREET SEGMENT
    (not the nearest intersection point), each segment half draining to its end junction.

    Point-Voronoi seeded at intersections carves every grid block into a diagonal triangle
    fan meeting at the block centre — nothing like hand-drawn cells. Frontage logic ("a lot
    drains to the street it faces") is nearest-EDGE assignment: rectangular blocks split
    along the rear-lot midline with 45° corner hips and mid-segment gutter divides —
    the rectangular municipal look. Implemented as dense samples along each half-edge
    labelled by its end junction, one Voronoi over the samples, unioned per junction.

    ``elevation`` — optional ``(lon, lat) -> z``. Water in a gutter runs downhill to
    whichever node is LOWER, not whichever is nearer, and maintenance holes are placed for
    pipe runs rather than for symmetry. Given ground, the divide moves from the geometric
    midpoint to the crest between the two nodes; without it, or if the lookup fails, the
    midpoint stands and the delineation is exactly what it was.

    ``inlets`` — optional ``[(lon, lat), ...]``. Grade alone sends a whole falling street to
    its lowest node and leaves every node above it dry, which is not what happens: inlets
    exist so water does not run the length of a block. Each stretch of gutter is caught by
    the first inlet below it, and that inlet resolves to a node — several inlets on one reach
    merge into one cell. This is catch basins doing the job they are kept for, evidence about
    where surface water enters, without becoming the unit land is divided among.

    Terrain is used here and not to cut cells: cutting by terrain produced a majority of
    noise-scale slivers. It decides which way the gutter runs inside a cell that street
    frontage already shaped.

    Returns {junction_name: SubcatchmentCell} for `_build_subcatchments(cells=...)`.
    """
    from shapely import STRtree
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import voronoi_diagram

    from swmmcanada.network.subcatchments import SubcatchmentCell

    to_m = lonlat_projector(utm_crs_for(aoi))
    from pyproj import Transformer

    to_deg = Transformer.from_crs(utm_crs_for(aoi), "EPSG:4326", always_xy=True).transform
    mask_m = shp_transform(to_m, mask)

    # Each street sample belongs to the NEAREST node. In synthesis the nodes sit on the
    # street graph and this reproduces the midpoint gutter divide exactly; on a published
    # network the maintenance holes sit wherever the pipes go, and keying off the street
    # graph's own node ids matched nothing at all — Victoria's 391 real nodes against 121
    # street edges returned zero cells, and every subcatchment silently took a placeholder
    # area. Land still goes to the street it faces; only "whose street is this" changed.
    import numpy as np

    names = list(junction_xy)
    if not names:
        return {}
    node_m = np.array([to_m(*junction_xy[n]) for n in names])
    inlet_m = np.array([to_m(lon, lat) for lon, lat in (inlets or [])]) if inlets else None

    labels: List[str] = []
    pts: List[Point] = []
    for u, v in streets.edges():
        lon_a, lat_a = streets.nodes[u]["x"], streets.nodes[u]["y"]
        lon_b, lat_b = streets.nodes[v]["x"], streets.nodes[v]["y"]
        a = to_m(lon_a, lat_a)
        b = to_m(lon_b, lat_b)
        length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        n = max(2, int(length // sample_step_m))

        crest = _crest_fraction(elevation, lon_a, lat_a, lon_b, lat_b, n)
        catches = _inlet_fractions(inlet_m, a, b) if crest is not None else []
        end_names = None
        if crest is not None:
            ka = int(((node_m[:, 0] - a[0]) ** 2 + (node_m[:, 1] - a[1]) ** 2).argmin())
            kb = int(((node_m[:, 0] - b[0]) ** 2 + (node_m[:, 1] - b[1]) ** 2).argmin())
            end_names = (names[ka], names[kb])

        for i in range(n + 1):
            t = i / n
            x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            if end_names is None:
                k = int(((node_m[:, 0] - x) ** 2 + (node_m[:, 1] - y) ** 2).argmin())
                name = names[k]
            else:
                # Downhill is away from the crest. The first inlet in that direction takes
                # this stretch; with none, it runs to the end of the segment.
                catch = _first_inlet_downhill(catches, t, crest)
                if catch is None:
                    name = end_names[0] if t < crest else end_names[1]
                else:
                    cx = a[0] + catch * (b[0] - a[0])
                    cy = a[1] + catch * (b[1] - a[1])
                    k = int(((node_m[:, 0] - cx) ** 2 + (node_m[:, 1] - cy) ** 2).argmin())
                    name = names[k]
            labels.append(name)
            pts.append(Point(x, y))
    if not pts:
        return {}

    vor = voronoi_diagram(MultiPoint(pts), envelope=mask_m.buffer(200.0))
    tree = STRtree(list(vor.geoms))
    by_label: Dict[str, list] = {}
    for name, pt in zip(labels, pts):
        idx = tree.query(pt, predicate="within")
        if len(idx):
            by_label.setdefault(name, []).append(vor.geoms[int(idx[0])])

    picked_m: Dict[str, object] = {}
    leftovers: List[object] = []
    for name, regions in by_label.items():
        merged = unary_union(regions).intersection(mask_m)
        parts = [g for g in (merged.geoms if hasattr(merged, "geoms") else [merged])
                 if g.geom_type == "Polygon" and not g.is_empty]
        if not parts:
            continue
        seed = Point(to_m(*junction_xy[name]))
        containing = [q for q in parts if q.contains(seed)]
        cell_m = containing[0] if containing else max(parts, key=lambda q: q.area)
        leftovers.extend(q for q in parts if q is not cell_m)
        if not cell_m.is_valid:
            fixed = [g for g in ([cell_m.buffer(0)] if cell_m.buffer(0).geom_type == "Polygon"
                     else list(cell_m.buffer(0).geoms)) if g.geom_type == "Polygon"]
            if not fixed:
                continue
            cell_m = max(fixed, key=lambda g: g.area)
        if cell_m.area < 25.0:
            leftovers.append(cell_m)
            continue
        picked_m[name] = cell_m

    # Corner scraps from the split (a junction's samples wrapping a corner produce
    # disconnected bits) go to whichever final cell shares the longest boundary —
    # coverage stays whole instead of leaking sliver holes.
    for scrap in leftovers:
        if scrap.area < 25.0:
            continue
        best, best_len = None, 0.0
        for name, cell_m in picked_m.items():
            if not scrap.intersects(cell_m):
                continue
            shared = scrap.intersection(cell_m).length
            if shared > best_len:
                best, best_len = name, shared
        if best is None:
            continue
        merged = unary_union([picked_m[best], scrap])
        if merged.geom_type == "Polygon" and merged.is_valid:
            picked_m[best] = merged

    cells: Dict[str, SubcatchmentCell] = {}
    for name, cell_m in picked_m.items():
        cell_deg = shp_transform(to_deg, cell_m)
        cells[name] = SubcatchmentCell(
            polygon_4326=cell_deg,
            area_m2=cell_m.area,
            exterior=[(float(x), float(y)) for x, y in cell_deg.exterior.coords],
        )
    return cells
