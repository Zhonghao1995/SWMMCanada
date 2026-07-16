"""TDD for derive.core.derive_parameters (spec 07): zonal stats over fixture
DEM/landcover/soil rasters, offline & deterministic.

Fixtures are tiny EPSG:3979 GeoTIFFs synthesised in a tmp dir with rasterio+numpy:
  - landcover : every pixel = NALCMS class 17 (urban) with impervious=0.70
  - hsg       : every pixel = 2 (HSG "B")
  - dem       : a planar ramp in the x-direction with a known % slope

SubcatchmentIn polygons are real (lon, lat) WGS84 rings that fall inside the AOI box.
"""
import math

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from swmmcanada.acquire.landcover import (
    DEFAULT_NALCMS_IMPERVIOUS,
    DEFAULT_NALCMS_LEGEND,
    LandcoverResult,
)
from swmmcanada.acquire.soil import DEFAULT_HSG_TO_CN, SoilResult
from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.derive.core import derive_parameters

# A small AOI near Ottawa (WGS84). We project to 3979 to lay out the rasters.
AOI_BBOX_4326 = (-75.70, 45.40, -75.66, 45.44)  # (minlon, minlat, maxlon, maxlat)
RES_M = 30.0
URBAN_CLASS = 17       # NALCMS urban/built-up, impervious=0.70
HSG_B_CODE = 2         # HYSOGs "B"
DEM_SLOPE_PCT = 5.0    # planar ramp: 5% rise per metre of easting

_to_3979 = Transformer.from_crs("EPSG:4326", "EPSG:3979", always_xy=True).transform
_to_4326 = Transformer.from_crs("EPSG:3979", "EPSG:4326", always_xy=True).transform


def _box_3979():
    """Return (left, bottom, right, top) of the AOI in EPSG:3979 metres."""
    minlon, minlat, maxlon, maxlat = AOI_BBOX_4326
    xs, ys = [], []
    for lon in (minlon, maxlon):
        for lat in (minlat, maxlat):
            x, y = _to_3979(lon, lat)
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _grid():
    left, bottom, right, top = _box_3979()
    width = max(8, int((right - left) // RES_M))
    height = max(8, int((top - bottom) // RES_M))
    transform = from_origin(left, top, RES_M, RES_M)
    return left, bottom, right, top, width, height, transform


def _write_raster(path, data, transform, dtype, nodata=None):
    with rasterio.open(
        path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1],
        count=1, dtype=dtype, crs="EPSG:3979", transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data, 1)


def _make_fixtures(tmp_path):
    left, bottom, right, top, width, height, transform = _grid()

    landcover = np.full((height, width), URBAN_CLASS, dtype="uint8")
    hsg = np.full((height, width), HSG_B_CODE, dtype="uint8")

    # planar ramp along easting: elevation = slope_frac * x_metres -> known % slope.
    slope_frac = DEM_SLOPE_PCT / 100.0
    xs = left + (np.arange(width) + 0.5) * RES_M
    dem = np.tile((slope_frac * xs).astype("float32"), (height, 1))

    lc_path = tmp_path / "landcover.tif"
    hsg_path = tmp_path / "hsg.tif"
    dem_path = tmp_path / "dem.tif"
    _write_raster(lc_path, landcover, transform, "uint8")
    _write_raster(hsg_path, hsg, transform, "uint8", nodata=255)
    _write_raster(dem_path, dem, transform, "float32")

    landcover_result = LandcoverResult(
        raster_path=lc_path,
        crs="EPSG:3979",
        legend=dict(DEFAULT_NALCMS_LEGEND),
        impervious=dict(DEFAULT_NALCMS_IMPERVIOUS),
    )
    soil_result = SoilResult(
        hsg_raster=hsg_path,
        crs="EPSG:3979",
        hsg_to_cn=dict(DEFAULT_HSG_TO_CN),
    )
    return dem_path, landcover_result, soil_result


def _polygon_4326(frac_left, frac_bottom, frac_right, frac_top):
    """Build a (lon, lat) ring covering a sub-rectangle of the AOI box (fractions 0..1)."""
    left, bottom, right, top, _w, _h, _t = _grid()
    x0 = left + (right - left) * frac_left
    x1 = left + (right - left) * frac_right
    y0 = bottom + (top - bottom) * frac_bottom
    y1 = bottom + (top - bottom) * frac_top
    corners_3979 = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return [tuple(_to_4326(x, y)) for x, y in corners_3979]


def test_derive_overwrites_imperv_cn_slope(tmp_path):
    dem_path, landcover, soil = _make_fixtures(tmp_path)

    sub = SubcatchmentIn(
        name="S1",
        outlet_node="J1",
        area_ha=1.0,
        pct_imperv=99.0,   # placeholder to be overwritten
        width_m=50.0,
        pct_slope=99.0,    # placeholder to be overwritten
        cn=99.0,           # placeholder to be overwritten
        polygon=_polygon_4326(0.25, 0.25, 0.75, 0.75),
    )

    out = derive_parameters([sub], dem_path, landcover, soil)
    assert len(out) == 1
    got = out[0]

    # pct_imperv = 100 * impervious_fraction(class 17) = 70
    assert got.pct_imperv == approx_pct(70.0)
    # dominant HSG = 2 -> "B"
    # F-021 (ADR 0024, round-2 semantics): CN is landcover x HSG for the PERVIOUS
    # remainder — an all-built-up cell on B soils reads the urban-pervious row (69);
    # pct_imperv carries the impervious share separately, so no double count.
    assert got.cn == 69.0
    # planar ramp -> positive mean slope, near the analytic value
    assert got.pct_slope > 0
    assert abs(got.pct_slope - DEM_SLOPE_PCT) < 1.0

    # untouched pass-through fields preserved
    assert got.name == "S1"
    assert got.outlet_node == "J1"
    assert got.width_m == 50.0
    assert got.area_ha == 1.0
    assert got.polygon == sub.polygon


def test_subcatchment_without_polygon_returned_unchanged(tmp_path):
    dem_path, landcover, soil = _make_fixtures(tmp_path)
    sub = SubcatchmentIn(
        name="S_no_poly",
        outlet_node="J9",
        area_ha=2.0,
        pct_imperv=42.0,
        width_m=70.0,
        pct_slope=3.0,
        cn=80.0,
        polygon=None,
    )
    out = derive_parameters([sub], dem_path, landcover, soil)
    assert out == [sub]


def approx_pct(value, tol=0.5):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) <= tol

        def __repr__(self):
            return f"~{value}"

    return _Approx()


def test_empty_overlap_keeps_existing_values(tmp_path):
    """A polygon entirely outside the rasters keeps the subcatchment's prior values."""
    dem_path, landcover, soil = _make_fixtures(tmp_path)
    # Build a polygon far away (shift the AOI box ~10 km north-east in 3979).
    left, bottom, right, top, _w, _h, _t = _grid()
    dx = (right - left) + 10_000.0
    dy = (top - bottom) + 10_000.0
    corners_3979 = [
        (left + dx, bottom + dy),
        (right + dx, bottom + dy),
        (right + dx, top + dy),
        (left + dx, top + dy),
        (left + dx, bottom + dy),
    ]
    ring = [tuple(_to_4326(x, y)) for x, y in corners_3979]
    sub = SubcatchmentIn(
        name="S_far",
        outlet_node="J7",
        area_ha=1.0,
        pct_imperv=33.0,
        width_m=40.0,
        pct_slope=2.5,
        cn=70.0,
        polygon=ring,
    )
    out = derive_parameters([sub], dem_path, landcover, soil)
    got = out[0]
    # No overlap -> existing values retained (no crash, no NaN).
    assert got.pct_imperv == 33.0
    assert got.cn == 70.0
    assert got.pct_slope == 2.5
