"""Subcatchment delineation (network, ADR 0001 — method borrowed from SWMMAnywhere,
reimplemented independently).

SWMMAnywhere's documented approach: derive manholes from the street graph, then use the
DEM for sub-catchment delineation. Our v1 borrows the spatial-partition idea with a
**Voronoi tessellation of the manhole points, clipped to the AOI** — each manhole gets the
area nearest to it. Areas are measured in a metric CRS. A DEM-flow-direction delineation
(pysheds/pyflwdir) is the planned fidelity upgrade; this gives real polygons + areas now.
"""
from dataclasses import dataclass
from typing import Dict, List, Tuple

from pyproj import Transformer
from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform
from shapely.ops import voronoi_diagram

LonLat = Tuple[float, float]

# Equal-area CRS for true ground area (matches geo.aoi). 3979 (LCC) is NOT equal-area.
_AREA_CRS = "ESRI:102001"
_AEA_FALLBACK = (
    "+proj=aea +lat_1=50 +lat_2=70 +lat_0=40 +lon_0=-96 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs"
)


@dataclass(frozen=True)
class SubcatchmentCell:
    polygon_4326: BaseGeometry            # the clipped Voronoi cell (WGS84)
    area_m2: float                        # true ground area (metric CRS)
    exterior: List[Tuple[float, float]]   # exterior ring coords (lon, lat) for SWMM [POLYGONS]


def delineate_subcatchments(
    points: Dict[str, LonLat], aoi_polygon: BaseGeometry, *, area_crs: str = _AREA_CRS
) -> Dict[str, SubcatchmentCell]:
    """points: {node_name: (lon, lat)} (the manholes). Returns one Voronoi cell per node,
    clipped to the AOI polygon, with true (equal-area) ground area."""
    names = list(points)
    if len(names) < 2:
        return {}
    pts = {n: Point(points[n]) for n in names}
    cells = voronoi_diagram(MultiPoint(list(pts.values())), envelope=aoi_polygon)
    try:
        to_metric = Transformer.from_crs("EPSG:4326", area_crs, always_xy=True).transform
    except Exception:
        to_metric = Transformer.from_crs("EPSG:4326", _AEA_FALLBACK, always_xy=True).transform

    out: Dict[str, SubcatchmentCell] = {}
    for name, p in pts.items():
        cell = next((c for c in cells.geoms if c.covers(p)), None)
        if cell is None:
            continue
        clipped = cell.intersection(aoi_polygon)
        clipped = _largest_polygon(clipped)
        if clipped is None or clipped.is_empty or clipped.area == 0:
            continue
        area_m2 = shp_transform(to_metric, clipped).area
        out[name] = SubcatchmentCell(
            polygon_4326=clipped,
            area_m2=area_m2,
            exterior=[(float(x), float(y)) for x, y in clipped.exterior.coords],
        )
    return out


def _largest_polygon(geom: BaseGeometry):
    if geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        polys = [g for g in geom.geoms if g.geom_type == "Polygon" and not g.is_empty]
        return max(polys, key=lambda g: g.area) if polys else None
    return None
