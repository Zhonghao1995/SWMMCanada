"""ADR 0016 — the open-water layer: polygons from the landcover Water class, subcatchment
subtraction semantics (clip / keep-seed-side / drop-in-water), multi-outlet selection, and
the validation coverage discount."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.network import synth as SY
from swmmcanada.network.water import (
    WATER_CLASS, nodes_near_water, subtract_water, thin_by_spacing, water_union,
)


class _Aoi:
    """Minimal AOI: a lon/lat box around Victoria-ish coordinates."""
    def __init__(self, min_lon=-123.40, min_lat=48.40, max_lon=-123.30, max_lat=48.46):
        self.bbox = (min_lon, min_lat, max_lon, max_lat)
        self.geometry = box(*self.bbox)
        self.area_km2 = 50.0


def _landcover_tif(tmp_path, aoi, water_cols=(10, 16)):
    """A 4326 landcover raster over the AOI: urban (17) with a vertical water band (18)
    spanning full height between the given column bounds — a 'river'."""
    w, h = 40, 30
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    px = (max_lon - min_lon) / w
    py = (max_lat - min_lat) / h
    data = np.full((h, w), 17, dtype=np.uint8)
    data[:, water_cols[0]:water_cols[1]] = WATER_CLASS
    path = tmp_path / "landcover.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1, dtype="uint8",
        crs="EPSG:4326", transform=from_origin(min_lon, max_lat, px, py),
    ) as dst:
        dst.write(data, 1)
    return path


def test_water_union_extracts_the_river(tmp_path):
    aoi = _Aoi()
    water = water_union(_landcover_tif(tmp_path, aoi), aoi)
    assert water is not None and not water.is_empty
    # the band sits between cols 10..16 of 40 → lon -123.375 .. -123.36
    assert water.bounds[0] == pytest.approx(-123.375, abs=1e-6)
    assert water.bounds[2] == pytest.approx(-123.360, abs=1e-6)


def test_water_union_none_without_water(tmp_path):
    aoi = _Aoi()
    assert water_union(_landcover_tif(tmp_path, aoi, water_cols=(0, 0)), aoi) is None


def _sub(name, outlet, poly):
    return SubcatchmentIn(name, outlet, area_ha=1.0, pct_imperv=50.0, width_m=50.0,
                          pct_slope=1.0, polygon=[(x, y) for x, y in poly.exterior.coords])


def test_subtract_water_clip_split_and_drop(tmp_path):
    aoi = _Aoi()
    water = water_union(_landcover_tif(tmp_path, aoi), aoi)   # band lon -123.375..-123.36

    west = box(-123.395, 48.42, -123.377, 48.44)              # dry, untouched
    spanning = box(-123.385, 48.42, -123.350, 48.44)          # crosses the river
    in_water = box(-123.374, 48.42, -123.361, 48.44)          # almost entirely water
    subs = [
        _sub("S_dry", "Jdry", west),
        _sub("S_span", "Jspan", spanning),
        _sub("S_wet", "Jwet", in_water),
    ]
    node_xy = {"Jdry": (-123.386, 48.43), "Jspan": (-123.381, 48.43),  # seed on the WEST bank
               "Jwet": (-123.367, 48.43)}

    out, diag = subtract_water(subs, water, node_xy, aoi)
    names = {s.name for s in out}
    assert names == {"S_dry", "S_span"}                       # open-water cell dropped
    assert diag["n_dropped_in_water"] == 1 and diag["n_split_trimmed"] == 1

    span = next(s for s in out if s.name == "S_span")
    xs = [x for x, _ in span.polygon]
    assert max(xs) <= -123.36 + 1e-6                          # east-bank fragment gone
    assert 100 < span.area_ha < 200                           # west bank only (~165 ha), not the full ~570 ha


def test_subtract_water_noop_without_water():
    subs = [_sub("S", "J", box(0, 0, 0.001, 0.001))]
    out, diag = subtract_water(subs, None, {}, _Aoi())
    assert out == subs and diag["applied"] is False


def _grid_graph(aoi, nx_=5, ny=4):
    """Street grid across the AOI with elevation rising eastward (west = low)."""
    import networkx as nx
    g = nx.Graph()
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    for i in range(nx_):
        for j in range(ny):
            lon = min_lon + i * (max_lon - min_lon) / (nx_ - 1)
            lat = min_lat + j * (max_lat - min_lat) / (ny - 1)
            g.add_node((i, j), x=lon, y=lat, elev=10.0 + i)
    for i in range(nx_):
        for j in range(ny):
            if i + 1 < nx_:
                g.add_edge((i, j), (i + 1, j))
            if j + 1 < ny:
                g.add_edge((i, j), (i, j + 1))
    return g


def test_synth_multi_outfall_near_water(tmp_path):
    aoi = _Aoi()
    water = water_union(_landcover_tif(tmp_path, aoi, water_cols=(0, 3)), aoi)  # west river
    g = _grid_graph(aoi)
    res = SY.synthesise_network(g, aoi=aoi, water=water)
    assert res.diagnostics["n_outfalls"] >= 2
    assert res.diagnostics["outlet_basis"].startswith("water-adjacent")
    # every conduit still exists, one per forest edge + one per outfall
    n_nodes = res.diagnostics["n_nodes"]
    assert len(res.network.conduits) == (n_nodes - res.diagnostics["n_outfalls"]) + res.diagnostics["n_outfalls"]
    # invert monotonicity: every conduit falls from upstream to downstream node
    inv = {j.name: j.invert_m for j in res.network.junctions}
    inv.update({o.name: o.invert_m for o in res.network.outfalls})
    for c in res.network.conduits:
        assert inv[c.from_node] > inv[c.to_node]


def test_synth_single_outfall_without_water():
    aoi = _Aoi()
    res = SY.synthesise_network(_grid_graph(aoi), aoi=aoi)
    assert res.diagnostics["n_outfalls"] == 1
    assert res.diagnostics["outlet_basis"].startswith("lowest node")


def test_validation_coverage_discounts_water(tmp_path):
    """Cells tile the LAND only; with the water layer the river gap is not a 'hole'."""
    from swmmcanada.validate import MethodDescriptor, validate_model
    from swmmcanada.build.models import JunctionIn, NetworkIn, OutfallIn, ConduitIn

    aoi = _Aoi(-123.40, 48.40, -123.30, 48.44)
    water = box(-123.36, 48.40, -123.34, 48.44)               # a vertical river band
    west = box(-123.40, 48.40, -123.36, 48.44)
    east = box(-123.34, 48.40, -123.30, 48.44)
    subs = [
        SubcatchmentIn("SW", "JW", area_ha=1, pct_imperv=50, width_m=50, pct_slope=1,
                       polygon=list(west.exterior.coords)),
        SubcatchmentIn("SE", "JE", area_ha=1, pct_imperv=50, width_m=50, pct_slope=1,
                       polygon=list(east.exterior.coords)),
    ]
    net = NetworkIn(
        junctions=[JunctionIn("JW", 10, -123.38, 48.42), JunctionIn("JE", 10, -123.32, 48.42)],
        outfalls=[OutfallIn("O", 9, -123.39, 48.42)],
        conduits=[ConduitIn("C1", "JW", "O", 10.0), ConduitIn("C2", "JE", "JW", 10.0)],
    )
    method = MethodDescriptor("junction_voronoi", "nearest node service area", "low")

    without = validate_model(net, subs, aoi, method=method)
    with_water = validate_model(net, subs, aoi, method=method, water=water)
    cov_no = next(c for c in without.checks if c.id == "aoi_coverage")
    cov_yes = next(c for c in with_water.checks if c.id == "aoi_coverage")
    assert not cov_no.passed                                  # river reads as a 20% hole
    assert cov_yes.passed                                     # effective AOI excludes it


def test_thin_by_spacing_orders_by_elevation():
    aoi = _Aoi()
    cands = [("hi", 12.0, (-123.39, 48.41)), ("lo", 9.0, (-123.3901, 48.4101)),
             ("far", 11.0, (-123.31, 48.45))]
    chosen = thin_by_spacing(cands, aoi)
    assert chosen[0] == "lo" and "far" in chosen and "hi" not in chosen
