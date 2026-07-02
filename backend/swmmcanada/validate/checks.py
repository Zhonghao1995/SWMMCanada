"""Individual subcatchment checks (PRD: subcatchment validation).

Each `check_*` is a small pure function returning a `core.CheckResult`. Geometric checks
share one `GeoContext` (a single reprojection of the AOI + cell polygons into a metric CRS),
and use `unary_union` so coverage/overlap stay O(n) rather than O(n²) pairwise.
"""
import math
from typing import Dict, List, Tuple

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.validate import schema


def _result(id, severity, passed, message, **metrics):
    from swmmcanada.validate.core import CheckResult
    return CheckResult(id=id, severity=severity, passed=passed, message=message, metrics=metrics)


# --- topological checks (no polygon needed) -----------------------------------


def check_outlet_present(subs: List[SubcatchmentIn]):
    bad = [s.name for s in subs if not (s.outlet_node and str(s.outlet_node).strip())]
    return _result("outlet_present", schema.ERROR, not bad,
                   "every subcatchment has an outlet node" if not bad
                   else f"{len(bad)} subcatchment(s) have no outlet node",
                   n_missing=len(bad), sample=bad[:10])


def check_outlet_exists(subs: List[SubcatchmentIn], node_names):
    bad = [s.name for s in subs if s.outlet_node not in node_names]
    return _result("outlet_exists", schema.ERROR, not bad,
                   "every outlet resolves to a network node" if not bad
                   else f"{len(bad)} outlet(s) reference a node not in the network",
                   n_dangling=len(bad), sample=bad[:10])


def check_area_positive(subs: List[SubcatchmentIn]):
    bad = [s.name for s in subs if not (s.area_ha and s.area_ha > 0)]
    return _result("area_positive", schema.ERROR, not bad,
                   "every subcatchment has positive area" if not bad
                   else f"{len(bad)} subcatchment(s) have non-positive area",
                   n_bad=len(bad), sample=bad[:10])


def check_geometry_absent(subs: List[SubcatchmentIn]):
    missing = [s.name for s in subs if not s.polygon]
    return _result("geometry_absent", schema.WARNING, not missing,
                   "every subcatchment carries a polygon" if not missing
                   else f"{len(missing)} cell(s) have no polygon (geometric checks skipped for them)",
                   n_missing=len(missing), sample=missing[:10])


# --- geometric context (one reprojection, shared) -----------------------------


class GeoContext:
    """Cell polygons + AOI reprojected once into a metric CRS, with a cached cell union."""

    def __init__(self, subcatchments: List[SubcatchmentIn], aoi):
        from shapely.geometry import Polygon
        from shapely.ops import transform as shp_transform

        from swmmcanada.geo.crs import lonlat_projector, utm_crs_for

        self._tr = lonlat_projector(utm_crs_for(aoi))
        self.aoi_m = shp_transform(self._tr, aoi.geometry)
        self.cells: List[Tuple[SubcatchmentIn, object]] = []
        for s in subcatchments:
            if not s.polygon:
                continue
            try:
                poly = Polygon([(float(x), float(y)) for x, y in s.polygon])
            except Exception:
                poly = Polygon()
            self.cells.append((s, shp_transform(self._tr, poly)))
        self._union = None

    def point_m(self, lonlat):
        from shapely.geometry import Point
        return Point(self._tr(lonlat[0], lonlat[1]))

    def valid_polys(self):
        """Metric cell polygons, repaired (buffer(0)) and non-empty — safe for set ops."""
        out = []
        for _, p in self.cells:
            if p.is_empty:
                continue
            out.append(p if p.is_valid else p.buffer(0))
        return [p for p in out if not p.is_empty]

    def union(self):
        if self._union is None:
            from shapely.ops import unary_union
            self._union = unary_union(self.valid_polys())
        return self._union


def _na(id, severity, msg="no cell polygons to check"):
    return _result(id, severity, True, msg, n_cells=0)


# --- geometric checks ---------------------------------------------------------


def check_geometry_valid(geo: GeoContext):
    bad = [s.name for s, p in geo.cells if p.is_empty or not p.is_valid]
    return _result("geometry_valid", schema.ERROR, not bad,
                   "every cell polygon is valid" if not bad
                   else f"{len(bad)} cell polygon(s) are invalid or empty",
                   n_bad=len(bad), sample=bad[:10])


def check_overlap(geo: GeoContext):
    polys = geo.valid_polys()
    if not polys:
        return _na("overlap", schema.WARNING)
    total = sum(p.area for p in polys)
    overlap = max(0.0, total - geo.union().area)
    frac = overlap / total if total > 0 else 0.0
    sev = schema.ERROR if frac > schema.OVERLAP_ERROR_FRAC else schema.WARNING
    passed = frac <= schema.OVERLAP_WARN_FRAC
    return _result("overlap", sev, passed,
                   f"cells overlap by {frac*100:.2f}% of total area",
                   overlap_m2=round(overlap, 1), fraction=round(frac, 4))


def check_area_conservation(subs: List[SubcatchmentIn], aoi):
    sum_m2 = sum((s.area_ha or 0.0) for s in subs) * 1e4
    aoi_m2 = aoi.area_km2 * 1e6
    frac = abs(sum_m2 - aoi_m2) / aoi_m2 if aoi_m2 > 0 else 0.0
    passed = frac <= schema.AREA_CONSERVATION_TOL
    return _result("area_conservation", schema.WARNING, passed,
                   f"Σ subcatchment area differs from AOI by {frac*100:.1f}%",
                   sum_m2=round(sum_m2, 1), aoi_m2=round(aoi_m2, 1), fraction=round(frac, 4))


def check_aoi_coverage(geo: GeoContext):
    if not geo.valid_polys():
        return _na("aoi_coverage", schema.WARNING)
    aoi_area = geo.aoi_m.area
    covered = geo.union().intersection(geo.aoi_m).area
    frac = max(0.0, aoi_area - covered) / aoi_area if aoi_area > 0 else 0.0
    sev = schema.ERROR if frac > schema.AOI_COVERAGE_ERROR_FRAC else schema.WARNING
    passed = frac <= schema.AOI_COVERAGE_WARN_FRAC
    return _result("aoi_coverage", sev, passed,
                   f"{frac*100:.1f}% of the AOI is covered by no subcatchment (blank holes)",
                   uncovered_fraction=round(frac, 4), uncovered_m2=round(max(0.0, aoi_area - covered), 1))


def check_aoi_containment(geo: GeoContext):
    polys = geo.valid_polys()
    if not polys:
        return _na("aoi_containment", schema.WARNING)
    aoi_m = geo.aoi_m
    mostly_outside = []
    for s, p in geo.cells:
        if p.is_empty:
            continue
        pp = p if p.is_valid else p.buffer(0)
        if pp.is_empty:
            continue
        outside = pp.area - pp.intersection(aoi_m).area
        if pp.area > 0 and outside / pp.area > schema.CELL_OUTSIDE_ERROR_FRAC:
            mostly_outside.append(s.name)
    union_area = geo.union().area
    agg_outside = union_area - geo.union().intersection(aoi_m).area
    frac = agg_outside / union_area if union_area > 0 else 0.0
    if mostly_outside:
        return _result("aoi_containment", schema.ERROR, False,
                       f"{len(mostly_outside)} cell(s) lie mostly outside the AOI",
                       n_cells_mostly_outside=len(mostly_outside), outside_fraction=round(frac, 4),
                       sample=mostly_outside[:10])
    passed = frac <= schema.AOI_OUTSIDE_WARN_FRAC
    return _result("aoi_containment", schema.WARNING, passed,
                   f"{frac*100:.1f}% of cell area falls outside the AOI",
                   n_cells_mostly_outside=0, outside_fraction=round(frac, 4))


def check_node_coverage(geo: GeoContext, node_coords: Dict[str, tuple]):
    if not geo.valid_polys():
        return _na("node_coverage", schema.WARNING)
    union = geo.union()
    uncovered = [name for name, xy in node_coords.items() if not union.covers(geo.point_m(xy))]
    return _result("node_coverage", schema.WARNING, not uncovered,
                   "every network node is covered by a subcatchment" if not uncovered
                   else f"{len(uncovered)} network node(s) fall in no subcatchment",
                   n_uncovered=len(uncovered), n_nodes=len(node_coords), sample=uncovered[:10])


def check_outlet_distance(geo: GeoContext, node_coords: Dict[str, tuple]):
    dists = []
    for s, p in geo.cells:
        if p.is_empty:
            continue
        oc = node_coords.get(s.outlet_node)
        if oc is None:
            continue
        dists.append(geo.point_m(oc).distance(p))   # 0 if the outlet is inside its cell
    if not dists:
        return _na("outlet_distance", schema.WARNING, "no cell has a resolvable outlet")
    dists.sort()
    n_warn = sum(1 for d in dists if schema.OUTLET_DIST_WARN_M < d <= schema.OUTLET_DIST_HIGH_M)
    n_high = sum(1 for d in dists if d > schema.OUTLET_DIST_HIGH_M)
    p95 = dists[min(len(dists) - 1, int(0.95 * (len(dists) - 1)))]
    passed = (n_warn + n_high) == 0
    return _result("outlet_distance", schema.WARNING, passed,
                   f"{n_high} cell(s) route >50 m to their outlet, {n_warn} route 20–50 m",
                   max_m=round(dists[-1], 1), p95_m=round(p95, 1), n_20_50m=n_warn, n_gt_50m=n_high)


def check_shape_plausibility(geo: GeoContext):
    cells = [(s, p) for s, p in geo.cells if not p.is_empty and p.area > 0]
    if not cells:
        return _na("shape_plausibility", schema.WARNING)
    areas = sorted(p.area for _, p in cells)
    median = areas[len(areas) // 2]
    area_outliers, elongated = [], []
    for s, p in cells:
        if median > 0 and (p.area > schema.SHAPE_AREA_OUTLIER_FACTOR * median
                           or p.area < median / schema.SHAPE_AREA_OUTLIER_FACTOR):
            area_outliers.append(s.name)
        thinness = (p.length ** 2) / (4 * math.pi * p.area) if p.area > 0 else 0.0
        if thinness > schema.SHAPE_THINNESS_MAX:
            elongated.append(s.name)
    flagged = sorted(set(area_outliers) | set(elongated))
    return _result("shape_plausibility", schema.WARNING, not flagged,
                   "subcatchment shapes are within plausible bounds" if not flagged
                   else f"{len(flagged)} cell(s) are extreme in size or very elongated",
                   n_area_outliers=len(area_outliers), n_elongated=len(elongated), sample=flagged[:10])
