"""Canonical AOI parsing (spec 01).

`geo` is the single front door for spatial input: a drawn GeoJSON polygon or an
uploaded shapefile becomes one canonical `AOI` (WGS84 geometry + bbox + true area),
regardless of input method — both paths converge on `_canonicalize`.
"""
import json
import os
import tempfile
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple, Union

from pyproj import Transformer
from shapely.geometry import MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union
from shapely.validation import make_valid

from swmmcanada.config import MAX_AOI_KM2
from swmmcanada.geo.errors import (
    AOICRSUnknownError,
    AOICRSUnsupportedError,
    AOIEmptyError,
    AOIGeometryTypeError,
    AOIOversizeError,
)

WORKING_CRS = "EPSG:4326"   # AOI.geometry stored/returned in lon/lat (WGS84)
AREA_CRS = "ESRI:102001"    # Canada Albers Equal Area — used ONLY to measure area
_AEA_FALLBACK = (
    "+proj=aea +lat_1=50 +lat_2=70 +lat_0=40 +lon_0=-96 "
    "+x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs"
)
_4326_NAMES = ("CRS84", "EPSG::4326", "EPSG:4326")


@dataclass(frozen=True)
class AOI:
    geometry: BaseGeometry                       # WORKING_CRS (lon/lat), valid, non-empty
    crs: str                                     # always WORKING_CRS
    bbox: Tuple[float, float, float, float]      # (min_lon, min_lat, max_lon, max_lat) WGS84
    area_km2: float                              # true ground area via AREA_CRS
    source: Literal["geojson", "shapefile"]


def aoi_from_geojson(geojson: Union[dict, str]) -> AOI:
    """Drawn-polygon path: GeoJSON Polygon/MultiPolygon, Feature, or FeatureCollection
    (single or multiple features, dissolved) → canonical AOI."""
    obj = json.loads(geojson) if isinstance(geojson, str) else geojson
    if not isinstance(obj, dict):
        raise AOIGeometryTypeError("GeoJSON must be a JSON object.")
    _reject_non_4326_crs(obj)
    geom = _geometry_from_geojson(obj)
    return _canonicalize(geom, source="geojson")


def aoi_from_shapefile(
    data: Union[bytes, "os.PathLike[str]", str], *, filename: Optional[str] = None
) -> AOI:
    """Upload path: a `.zip` (with .shp/.shx/.dbf/.prj) or a `.shp` path/bytes →
    the SAME canonical AOI a drawn equivalent of the same region would produce."""
    geom = _read_shapefile_union(data, filename)
    return _canonicalize(geom, source="shapefile")


# --- internals ---------------------------------------------------------------


def _canonicalize(geom: BaseGeometry, *, source: Literal["geojson", "shapefile"]) -> AOI:
    """Shared tail both input paths converge on, so input method cannot change the
    result: type-check → repair → keep polygonal → empty-check → CCW → area + cap."""
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise AOIGeometryTypeError(f"AOI must be a (Multi)Polygon; got {geom.geom_type}.")
    geom = _keep_polygonal(make_valid(geom))
    if geom.is_empty or geom.area == 0:
        raise AOIEmptyError("AOI is empty or has zero area.")
    geom = _orient_ccw(geom)
    bbox = tuple(round(v, 6) for v in geom.bounds)  # type: ignore[assignment]
    area_km2 = _area_km2(geom)
    if area_km2 > MAX_AOI_KM2:
        raise AOIOversizeError(
            f"AOI area {area_km2:.2f} km² exceeds the {MAX_AOI_KM2:g} km² cap."
        )
    return AOI(geometry=geom, crs=WORKING_CRS, bbox=bbox, area_km2=area_km2, source=source)


def _reject_non_4326_crs(obj: dict) -> None:
    crs = obj.get("crs")
    if not crs:
        return  # RFC 7946: absence means WGS84 lon/lat
    name = ""
    if isinstance(crs, dict):
        name = str(crs.get("properties", {}).get("name", ""))
    if not any(tag in name for tag in _4326_NAMES):
        raise AOICRSUnsupportedError(
            f"GeoJSON must be WGS84 lon/lat (EPSG:4326); got CRS {name!r}."
        )


def _geometry_from_geojson(obj: dict) -> BaseGeometry:
    t = obj.get("type")
    if t == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in (obj.get("features") or []) if f.get("geometry")]
        if not geoms:
            raise AOIEmptyError("FeatureCollection has no geometries.")
        return unary_union(geoms)
    if t == "Feature":
        g = obj.get("geometry")
        if not g:
            raise AOIEmptyError("Feature has no geometry.")
        return shape(g)
    if t in (
        "Polygon", "MultiPolygon", "Point", "MultiPoint",
        "LineString", "MultiLineString", "GeometryCollection",
    ):
        return shape(obj)
    raise AOIGeometryTypeError(f"Unsupported GeoJSON type: {t!r}")


def _keep_polygonal(geom: BaseGeometry) -> BaseGeometry:
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if geom.geom_type == "GeometryCollection":
        parts: List[BaseGeometry] = [
            g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")
        ]
        return unary_union(parts) if parts else geom
    return geom  # a degenerate point/line → caller raises AOIEmptyError (area == 0)


def _orient_ccw(geom: BaseGeometry) -> BaseGeometry:
    if geom.geom_type == "Polygon":
        return orient(geom, sign=1.0)
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([orient(p, sign=1.0) for p in geom.geoms])
    return geom


def _area_km2(geom_4326: BaseGeometry) -> float:
    try:
        tr = Transformer.from_crs(WORKING_CRS, AREA_CRS, always_xy=True)
    except Exception:
        tr = Transformer.from_crs(WORKING_CRS, _AEA_FALLBACK, always_xy=True)
    projected = shp_transform(tr.transform, geom_4326)
    return projected.area / 1e6


def _read_shapefile_union(
    data: Union[bytes, "os.PathLike[str]", str], filename: Optional[str]
) -> BaseGeometry:
    """Read a shapefile (.shp path or .zip path/bytes), reproject to WGS84, and dissolve
    all features to one geometry. Refuses to guess a missing CRS."""
    import geopandas as gpd  # local import keeps the GeoJSON path free of geopandas

    cleanup: Optional[str] = None
    if isinstance(data, (bytes, bytearray)):
        is_zip = (filename or "").lower().endswith(".zip") or bytes(data[:4]) == b"PK\x03\x04"
        fd, tmp = tempfile.mkstemp(suffix=".zip" if is_zip else ".shp")
        os.close(fd)
        with open(tmp, "wb") as fh:
            fh.write(data)
        path, cleanup = tmp, tmp
    else:
        path = os.fspath(data)

    try:
        read_path = f"zip://{path}" if str(path).lower().endswith(".zip") else path
        gdf = gpd.read_file(read_path)
    finally:
        if cleanup:
            try:
                os.remove(cleanup)
            except OSError:
                pass

    if gdf.empty:
        raise AOIEmptyError("Shapefile has no features.")
    if gdf.crs is None:
        raise AOICRSUnknownError(
            "Shapefile has no CRS (.prj). Provide a shapefile with a .prj / known projection."
        )
    gdf = gdf.to_crs(WORKING_CRS)
    return unary_union(list(gdf.geometry.values))
