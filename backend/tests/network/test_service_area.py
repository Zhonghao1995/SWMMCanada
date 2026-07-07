"""ADR 0017 — street service corridor + sliver merging: the municipal worldview for
synthesis subcatchments."""
import networkx as nx
import pytest
from shapely.geometry import Polygon, box

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.network.delineate_dem import delineate_junction_subcatchments
from swmmcanada.network.service_area import merge_slivers, street_service_corridor


class _Aoi:
    def __init__(self, min_lon=-123.40, min_lat=48.40, max_lon=-123.36, max_lat=48.43):
        self.bbox = (min_lon, min_lat, max_lon, max_lat)
        self.geometry = box(*self.bbox)
        self.area_km2 = 10.0


def _streets(aoi, nx_=4, ny=3):
    g = nx.Graph()
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    for i in range(nx_):
        for j in range(ny):
            g.add_node((i, j),
                       x=min_lon + i * (max_lon - min_lon) / (nx_ - 1),
                       y=min_lat + j * (max_lat - min_lat) / (ny - 1),
                       elev=10.0 + i)
    for i in range(nx_):
        for j in range(ny):
            if i + 1 < nx_: g.add_edge((i, j), (i + 1, j))
            if j + 1 < ny: g.add_edge((i, j), (i, j + 1))
    return g


def test_corridor_is_a_band_around_streets():
    aoi = _Aoi()
    corridor = street_service_corridor(_streets(aoi), aoi, lot_depth_m=50.0)
    assert corridor is not None
    # a 50 m band around a sparse grid must be a strict subset of the AOI
    assert corridor.within(aoi.geometry.buffer(1e-9))
    assert corridor.area < 0.6 * aoi.geometry.area


def test_corridor_none_without_edges():
    aoi = _Aoi()
    g = nx.Graph(); g.add_node(0, x=-123.39, y=48.41, elev=1.0)
    assert street_service_corridor(g, aoi) is None


def test_voronoi_cells_confined_to_corridor():
    aoi = _Aoi()
    streets = _streets(aoi)
    corridor = street_service_corridor(streets, aoi)
    jxy = {str(n): (d["x"], d["y"]) for n, d in streets.nodes(data=True)}
    subs, diag = delineate_junction_subcatchments(jxy, aoi, service_mask=corridor)
    assert diag["method"] == "junction_voronoi" and diag["service"]["applied"]
    for s in subs:
        if not s.polygon:
            continue
        cell = Polygon([(x, y) for x, y in s.polygon])
        assert cell.difference(corridor.buffer(1e-7)).area < cell.area * 0.01


def test_merge_slivers_conserves_area():
    aoi = _Aoi()
    big = box(-123.40, 48.40, -123.38, 48.42)
    tiny = box(-123.38, 48.40, -123.3799, 48.42)          # sliver sharing big's east edge
    other = box(-123.37, 48.40, -123.36, 48.42)
    subs = [
        SubcatchmentIn("B", "J1", area_ha=100.0, pct_imperv=50, width_m=50, pct_slope=1,
                       polygon=list(big.exterior.coords)),
        SubcatchmentIn("T", "J2", area_ha=0.01, pct_imperv=50, width_m=50, pct_slope=1,
                       polygon=list(tiny.exterior.coords)),
        SubcatchmentIn("O", "J3", area_ha=50.0, pct_imperv=50, width_m=50, pct_slope=1,
                       polygon=list(other.exterior.coords)),
    ]
    out, diag = merge_slivers(subs, aoi, min_cell_ha=0.05)
    assert diag["n_merged"] == 1 and len(out) == 2
    merged = next(s for s in out if s.name == "B")
    assert merged.area_ha == pytest.approx(100.01)
    assert sum(s.area_ha for s in out) == pytest.approx(150.01)


def test_none_parameters_change_nothing():
    aoi = _Aoi()
    streets = _streets(aoi)
    jxy = {str(n): (d["x"], d["y"]) for n, d in streets.nodes(data=True)}
    a, _ = delineate_junction_subcatchments(jxy, aoi)
    b, _ = delineate_junction_subcatchments(jxy, aoi, service_mask=None, min_cell_ha=None)
    assert [s.area_ha for s in a] == [s.area_ha for s in b]
