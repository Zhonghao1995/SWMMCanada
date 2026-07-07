"""Open-water layer for synthesis (ADR 0016): polygons from the landcover raster's Water
class, and the subcatchment water-subtraction that keeps cells honest around rivers.

No new data source: every derive-enabled build already ships the NALCMS landcover clip
(30 m) whose class 18 is Water. The honest cost of that reuse: watercourses narrower than
~30 m are invisible — recorded, and a higher-resolution hydro layer (CanVec) is the
documented upgrade slot.
"""
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import rasterio
from rasterio import features as rio_features
from shapely.geometry import Point, Polygon, shape as shp_shape
from shapely.ops import transform as shp_transform, unary_union

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.geo.crs import lonlat_projector, utm_crs_for

WATER_CLASS = 18            # NALCMS "Water" (acquire.landcover legend)
MIN_WATER_HA = 0.3          # blobs below this are raster noise, not waterbodies
NEAR_EMPTY_FRAC = 0.01      # a cell losing >99% of its area to water is open water itself


def _polygon_parts(geom) -> List[Polygon]:
    """Valid, non-trivial Polygon parts of any geometry (difference outputs can be
    Polygon / MultiPolygon / GeometryCollection, occasionally invalid): repair with
    buffer(0), keep polygonal pieces only."""
    if geom.is_empty:
        return []
    fixed = geom.buffer(0) if not geom.is_valid else geom
    if fixed.is_empty:
        return []
    if fixed.geom_type == "Polygon":
        return [fixed]
    if fixed.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [g for g in fixed.geoms if g.geom_type == "Polygon" and not g.is_empty]
    return []


def water_union(landcover_path, aoi):
    """Dissolved open-water geometry (EPSG:4326) inside the AOI, from the landcover clip's
    Water class; None when the AOI holds no mappable water."""
    with rasterio.open(landcover_path) as src:
        data = src.read(1)
        mask = data == WATER_CLASS
        if not mask.any():
            return None
        polys = [
            shp_shape(geom)
            for geom, val in rio_features.shapes(
                mask.astype(np.uint8), mask=mask, transform=src.transform)
            if val == 1
        ]
        src_crs = src.crs

    if not polys:
        return None
    # Raster CRS -> 4326 (landcover ships in its native CRS; AOI geometry is 4326).
    if src_crs is not None and src_crs.to_epsg() != 4326:
        from pyproj import Transformer

        tr = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True).transform
        polys = [shp_transform(tr, p) for p in polys]

    # Metric area filter + clip to the AOI.
    to_m = lonlat_projector(utm_crs_for(aoi))
    keep = [p for p in polys
            if shp_transform(to_m, p).area >= MIN_WATER_HA * 10_000.0]
    if not keep:
        return None
    merged = unary_union(keep).intersection(aoi.geometry)
    return None if merged.is_empty else merged


def subtract_water(
    subcatchments: List[SubcatchmentIn],
    water,                                    # 4326 geometry from water_union (or None)
    node_xy: Dict[str, Tuple[float, float]],  # outlet-node name -> (lon, lat) seed
    aoi,
) -> Tuple[List[SubcatchmentIn], dict]:
    """Remove open water from the subcatchment tiling (ADR 0016 §2): clip every cell to
    land, keep only the piece containing its seed node when a river splits it (the far
    bank belongs to the far bank's own nodes), drop cells that were essentially water.
    Areas are recomputed in the AOI's metric CRS; widths deliberately stay (first-pass)."""
    from dataclasses import replace

    diag = {"applied": water is not None, "n_clipped": 0, "n_split_trimmed": 0,
            "n_dropped_in_water": 0, "water_area_ha": 0.0}
    if water is None:
        return subcatchments, diag

    to_m = lonlat_projector(utm_crs_for(aoi))
    diag["water_area_ha"] = round(shp_transform(to_m, water).area / 10_000.0, 2)

    out: List[SubcatchmentIn] = []
    for s in subcatchments:
        if not s.polygon:
            out.append(s)
            continue
        poly = Polygon([(float(x), float(y)) for x, y in s.polygon])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.intersects(water):
            out.append(s)
            continue

        land = poly.difference(water)
        # A difference against raster-derived water can return GeometryCollections,
        # slivers, or self-touching rings — sanitise to clean polygon parts first.
        parts = _polygon_parts(land)
        orig_m2 = shp_transform(to_m, poly).area
        land_m2 = sum(shp_transform(to_m, q).area for q in parts)
        if not parts or (orig_m2 > 0 and land_m2 / orig_m2 < NEAR_EMPTY_FRAC):
            diag["n_dropped_in_water"] += 1
            continue

        if len(parts) > 1:
            seed = node_xy.get(s.outlet_node)
            if seed:
                pt = Point(seed)
                containing = [q for q in parts if q.contains(pt)]
                land = containing[0] if containing else min(parts, key=lambda q: q.distance(pt))
            else:
                land = max(parts, key=lambda q: q.area)
            diag["n_split_trimmed"] += 1
        else:
            land = parts[0]

        # Final validity gate in METRIC space — validation reprojects before checking, and
        # a ring that is barely valid in degrees can self-intersect in metres (found live
        # on the Duncan AOI). Repair there, largest part wins, inverse-transform back.
        land_m = shp_transform(to_m, land)
        if not land_m.is_valid:
            repaired = _polygon_parts(land_m)
            if not repaired:
                diag["n_dropped_in_water"] += 1
                continue
            land_m = max(repaired, key=lambda q: q.area)
            land = shp_transform(_inverse_projector(aoi), land_m)

        if land_m.area < 100.0:   # < 100 m² sliver — not a subcatchment
            diag["n_dropped_in_water"] += 1
            continue

        diag["n_clipped"] += 1
        area_ha = land_m.area / 10_000.0
        exterior = [(float(x), float(y)) for x, y in land.exterior.coords]
        out.append(replace(s, polygon=exterior, area_ha=area_ha))
    return out, diag


def _inverse_projector(aoi):
    """metric-CRS -> lon/lat transform (the inverse of lonlat_projector)."""
    from pyproj import Transformer

    return Transformer.from_crs(utm_crs_for(aoi), "EPSG:4326", always_xy=True).transform


def nodes_near_water(node_xy: Dict[str, Tuple[float, float]], water, aoi,
                     *, max_dist_m: float = 150.0) -> List[str]:
    """Node names within ``max_dist_m`` of open water (metric), for outlet candidacy.

    150 m, not "touching": streets sit back from rivers (floodplain setbacks) and the
    30 m-raster water edge is itself ±1 cell — measured on the Duncan AOI, 40 m catches
    ONE street node while 150 m catches the ~dozen bank-parallel candidates the spacing
    filter is meant to thin."""
    if water is None:
        return []
    to_m = lonlat_projector(utm_crs_for(aoi))
    water_m = shp_transform(to_m, water)
    out = []
    for name, (lon, lat) in node_xy.items():
        if water_m.distance(Point(to_m(lon, lat))) <= max_dist_m:
            out.append(name)
    return out


def thin_by_spacing(candidates: List[Tuple[str, float, Tuple[float, float]]], aoi,
                    *, min_spacing_m: float = 300.0) -> List[str]:
    """Greedy spacing filter: candidates as (name, elev, (lon, lat)), lowest elevation
    first, accepted only if >= min_spacing_m (metric) from every accepted one."""
    to_m = lonlat_projector(utm_crs_for(aoi))
    accepted: List[Tuple[str, Point]] = []
    for name, _elev, (lon, lat) in sorted(candidates, key=lambda c: c[1]):
        pt = Point(to_m(lon, lat))
        if all(pt.distance(q) >= min_spacing_m for _, q in accepted):
            accepted.append((name, pt))
    return [name for name, _ in accepted]
