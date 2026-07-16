"""Physical imperviousness from mapped roofs + roads (ADR 0023 cut 1, #138).

The 30 m land-cover raster paints every urban cell one flat "built-up" imperviousness
(~70%), erasing the difference between a downtown block and a suburban house on a big
lot. Where OSM actually maps buildings, roof area + road area IS the impervious surface,
so the physical estimate replaces the raster mean:

    pct_imperv = min(CAP, 100 * (roof_frac + road_frac) + DRIVEWAY_ALLOWANCE_PCT)

Cells without building evidence (roof_frac < MIN_ROOF_EVIDENCE_FRAC) keep their existing
(land-cover) value — OSM's suburban sparsity is a fact of life (#118 lesson), and a
missing roof must degrade to the raster, never to "no imperviousness". All constants are
documented model assumptions (ASSUMPTIONS.md).
"""
from dataclasses import replace
from typing import List, Tuple

from shapely.geometry import LineString, Polygon
from shapely.ops import transform as shp_transform, unary_union

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.geo.crs import lonlat_projector, utm_crs_for

# Half of a local-street carriageway (~8 m curb to curb): the paved band each side of the
# centreline. Arterials are wider, lanes narrower — one documented number for synthesis.
ROAD_HALF_WIDTH_M = 4.0
# Driveways, sidewalks, patios that ride along mapped roofs but are not mapped themselves.
DRIVEWAY_ALLOWANCE_PCT = 10.0
# Physical estimate applies only where buildings are actually mapped in the cell.
MIN_ROOF_EVIDENCE_FRAC = 0.02
CAP_PCT = 90.0


def refine_imperviousness(
    subcatchments: List[SubcatchmentIn],
    buildings,
    streets,
    aoi,
) -> Tuple[List[SubcatchmentIn], dict]:
    """Replace land-cover imperviousness with the roof+road physical estimate on every
    cell with mapped-building evidence. Returns ``(new_subcatchments, diagnostics)``;
    with no buildings at all this is a documented no-op."""
    diag = {"applied": False, "n_refined": 0, "n_kept_landcover": len(subcatchments),
            "road_half_width_m": ROAD_HALF_WIDTH_M,
            "driveway_allowance_pct": DRIVEWAY_ALLOWANCE_PCT}
    if not buildings or not subcatchments:
        return list(subcatchments), diag

    to_m = lonlat_projector(utm_crs_for(aoi))
    roofs_m = unary_union([shp_transform(to_m, b) for b in buildings]).buffer(0)

    road_m = None
    if streets is not None and streets.number_of_edges() > 0:
        segments = []
        for u, v in streets.edges():
            a, b = streets.nodes[u], streets.nodes[v]
            segments.append(LineString([to_m(a["x"], a["y"]), to_m(b["x"], b["y"])]))
        road_m = unary_union([s.buffer(ROAD_HALF_WIDTH_M) for s in segments])

    out: List[SubcatchmentIn] = []
    n_refined = 0
    for sub in subcatchments:
        if not sub.polygon:
            out.append(sub)
            continue
        cell_m = shp_transform(
            to_m, Polygon([(float(x), float(y)) for x, y in sub.polygon])).buffer(0)
        if cell_m.is_empty or cell_m.area <= 0:
            out.append(sub)
            continue
        roof_frac = cell_m.intersection(roofs_m).area / cell_m.area
        if roof_frac < MIN_ROOF_EVIDENCE_FRAC:
            out.append(sub)                       # no evidence -> land-cover value stands
            continue
        road_frac = (cell_m.intersection(road_m).area / cell_m.area) if road_m is not None else 0.0
        physical = min(CAP_PCT, 100.0 * (roof_frac + road_frac) + DRIVEWAY_ALLOWANCE_PCT)
        out.append(replace(sub, pct_imperv=round(physical, 1)))
        n_refined += 1

    diag.update(applied=n_refined > 0, n_refined=n_refined,
                n_kept_landcover=len(subcatchments) - n_refined)
    return out, diag
