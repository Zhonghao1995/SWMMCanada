"""Delineation v2 (ADR 0010): DEM D8 basins behind the terrain honesty gate, on fully
synthetic DEMs — offline and deterministic. The valley DEM has a 10 % cross-slope (well
above the gate); the flat DEM sits below it; a half-coverage DEM trips the posterior gate.
"""
import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.crs import CRS

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.delineate_dem import (
    DemDelineationConfig,
    _burn_streets,
    delineate_junction_subcatchments,
)

DEM_CRS = "EPSG:32618"
RES = 10.0
N = 100
X0, Y0 = 500_000.0, 5_000_000.0          # top-left corner (UTM 18N)
_TO_LL = Transformer.from_crs(DEM_CRS, "EPSG:4326", always_xy=True).transform


def _write_dem(tmp_path, array, name="dem.tif"):
    path = tmp_path / name
    transform = Affine(RES, 0, X0, 0, -RES, Y0)
    with rasterio.open(
        path, "w", driver="GTiff", height=array.shape[0], width=array.shape[1],
        count=1, dtype="float32", crs=CRS.from_string(DEM_CRS), transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(array.astype("float32"), 1)
    return path


def _valley_dem():
    """V-valley along row 50 (10 % side slopes), tilted to fall eastward."""
    rows = np.abs(np.arange(N) - 50)[:, None] * 1.0
    tilt = (N - np.arange(N))[None, :] * 0.05
    return rows + tilt + 100.0


def _flat_dem():
    """Essentially flat: 0.01 % tilt — far below any sane gate threshold."""
    return (np.arange(N)[None, :] * 0.001) + np.zeros((N, N)) + 100.0


def _utm_point(col, row):
    return X0 + col * RES, Y0 - row * RES


def _aoi_inside_dem(margin_cells=5):
    """A lon/lat AOI box safely inside the DEM footprint."""
    x1, y1 = _utm_point(margin_cells, N - margin_cells)
    x2, y2 = _utm_point(N - margin_cells, margin_cells)
    lo1, la1 = _TO_LL(x1, y1)
    lo2, la2 = _TO_LL(x2, y2)
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [lo1, la1], [lo2, la1], [lo2, la2], [lo1, la2], [lo1, la1]]]})


def _valley_junctions():
    """Two manholes ON the valley floor (row 50), west and east."""
    j = {}
    for name, col in (("JW", 30), ("JE", 70)):
        x, y = _utm_point(col, 50)
        j[name] = _TO_LL(x, y)
    return j


AOI = _aoi_inside_dem()
CFG = DemDelineationConfig(slope_gate_pct=3.0)


# --- the DEM path ---------------------------------------------------------------


def test_valley_dem_delineates_basins(tmp_path):
    dem = _write_dem(tmp_path, _valley_dem())
    subs, diag = delineate_junction_subcatchments(
        _valley_junctions(), AOI, dem_path=dem, config=CFG)

    assert diag["method"] == "junction_dem"
    assert diag["gate"]["decision"] == "dem"
    assert diag["gate"]["median_slope_pct"] > 3.0        # the valley walls dominate
    assert diag["width_method"] == "area_over_flow_length"
    assert {s.name for s in subs} == {"S_JW", "S_JE"}
    for s in subs:
        assert s.outlet_node in ("JW", "JE")
        assert s.area_ha > 0 and s.polygon               # real polygons, real areas
        # SWMM width = area / longest DEM flow path — narrower than √area for these
        # elongated valley basins, and never the √area fallback.
        assert 0 < s.width_m < (s.area_ha * 1e4) ** 0.5


def test_dem_width_uses_flow_length_voronoi_keeps_sqrt(tmp_path):
    dem = _write_dem(tmp_path, _valley_dem())
    dem_subs, _ = delineate_junction_subcatchments(_valley_junctions(), AOI, dem_path=dem, config=CFG)
    vor_subs, _ = delineate_junction_subcatchments(_valley_junctions(), AOI, dem_path=None, config=CFG)
    for s in vor_subs:
        assert s.width_m == pytest.approx((s.area_ha * 1e4) ** 0.5)   # voronoi contract intact
    assert all(s.width_m != pytest.approx((s.area_ha * 1e4) ** 0.5) for s in dem_subs)


def test_dem_cells_cover_aoi_without_gross_overlap(tmp_path):
    """Absorption must leave no blank holes; basins must not double-count ground."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    dem = _write_dem(tmp_path, _valley_dem())
    subs, diag = delineate_junction_subcatchments(
        _valley_junctions(), AOI, dem_path=dem, config=CFG)

    polys = [Polygon(s.polygon) for s in subs]
    union = unary_union(polys)
    uncovered = AOI.geometry.difference(union.buffer(1e-9)).area / AOI.geometry.area
    overlap = (sum(p.area for p in polys) - union.area) / union.area
    assert uncovered < 0.02                              # < 2 % blank (validation warn line)
    assert overlap < 0.005
    assert diag["n_cells_absorbed"] >= 0                 # absorption ran and is reported


def test_dem_delineation_is_deterministic(tmp_path):
    dem = _write_dem(tmp_path, _valley_dem())
    a, _ = delineate_junction_subcatchments(_valley_junctions(), AOI, dem_path=dem, config=CFG)
    b, _ = delineate_junction_subcatchments(_valley_junctions(), AOI, dem_path=dem, config=CFG)
    assert [(s.name, s.area_ha, s.polygon) for s in a] == [(s.name, s.area_ha, s.polygon) for s in b]


# --- the honesty gate -----------------------------------------------------------


def test_flat_dem_falls_back_to_voronoi_with_reading(tmp_path):
    dem = _write_dem(tmp_path, _flat_dem())
    subs, diag = delineate_junction_subcatchments(
        _valley_junctions(), AOI, dem_path=dem, config=CFG)

    assert diag["method"] == "junction_voronoi"
    assert diag["gate"]["decision"] == "below_slope_gate"
    assert diag["gate"]["median_slope_pct"] < 1.0        # the reading is recorded
    assert diag["gate"]["threshold_pct"] == 3.0
    assert {s.name for s in subs} == {"S_JW", "S_JE"}    # voronoi still delivers


def test_no_dem_falls_back_with_reason():
    subs, diag = delineate_junction_subcatchments(_valley_junctions(), AOI, dem_path=None, config=CFG)
    assert diag["method"] == "junction_voronoi"
    assert diag["gate"]["decision"] == "no_dem"
    assert len(subs) == 2


def test_partial_dem_trips_posterior_gate(tmp_path):
    """A DEM covering only the AOI's west half leaves the east blank → the posterior
    validation gate must reject the DEM result and fall back to Voronoi."""
    dem = _write_dem(tmp_path, _valley_dem()[:, :40])    # only cols 0..39 (west strip)
    subs, diag = delineate_junction_subcatchments(
        {"JW": _valley_junctions()["JW"]} | {"JE": _valley_junctions()["JE"]},
        AOI, dem_path=dem, config=CFG)

    assert diag["method"] == "junction_voronoi"
    assert diag["gate"]["decision"] in ("posterior_fallback", "dem_degenerate")
    if diag["gate"]["decision"] == "posterior_fallback":
        assert "aoi_coverage" in diag["gate"]["posterior_errors"]
    assert {s.name for s in subs} == {"S_JW", "S_JE"}


# --- street burning -------------------------------------------------------------


def test_burn_streets_lowers_only_street_cells():
    import networkx as nx

    dem = _flat_dem().astype("float32")
    transform = Affine(RES, 0, X0, 0, -RES, Y0)
    g = nx.Graph()
    (lo1, la1), (lo2, la2) = _TO_LL(*_utm_point(10, 50)), _TO_LL(*_utm_point(90, 50))
    g.add_node("a", x=lo1, y=la1)
    g.add_node("b", x=lo2, y=la2)
    g.add_edge("a", "b")

    burned, n = _burn_streets(dem, transform, DEM_CRS, g, DemDelineationConfig(burn_depth_m=2.0))
    assert n > 0
    diff = dem - burned
    assert diff.max() == pytest.approx(2.0)              # burned cells: exactly the depth
    assert (diff > 0).sum() == n                         # ...and nothing else moved
    assert diff[0, 0] == 0.0


def test_burn_streets_none_is_noop():
    dem = _flat_dem().astype("float32")
    transform = Affine(RES, 0, X0, 0, -RES, Y0)
    burned, n = _burn_streets(dem, transform, DEM_CRS, None, DemDelineationConfig())
    assert n == 0 and (burned == dem).all()
