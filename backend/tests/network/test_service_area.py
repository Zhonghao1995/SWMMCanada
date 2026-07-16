"""ADR 0017 — street service corridor + sliver merging: the municipal worldview for
synthesis subcatchments."""
import networkx as nx
import pytest
from shapely.geometry import Polygon, box

from swmmcanada.build.models import SubcatchmentIn
from swmmcanada.network.delineate_dem import delineate_junction_subcatchments
from swmmcanada.network.service_area import (
    block_aware_service_area, merge_slivers, street_service_corridor,
)


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


def test_block_faces_close_small_interior_lenses():
    """A ~150 m-deep grid block leaves a mid-block lens beyond the 50 m corridor; the
    block-aware mask serves the WHOLE block (lots drain to their fronting streets)."""
    aoi = _Aoi(-123.40, 48.40, -123.394, 48.405)     # ~450 x 550 m box
    g = nx.Graph()
    # one closed block ~150 x 150 m — a normal city block; its mid-block lens beyond the
    # 50 m buffers is ~0.25 ha (< MAX_INTERIOR_GAP_HA), i.e. the backs of the lots.
    coords = {(0, 0): (-123.3995, 48.4005), (1, 0): (-123.3975, 48.4005),
              (1, 1): (-123.3975, 48.40185), (0, 1): (-123.3995, 48.40185)}
    for n, (x, y) in coords.items():
        g.add_node(n, x=x, y=y, elev=10.0)
    g.add_edge((0, 0), (1, 0)); g.add_edge((1, 0), (1, 1))
    g.add_edge((1, 1), (0, 1)); g.add_edge((0, 1), (0, 0))

    corridor = street_service_corridor(g, aoi)
    block = block_aware_service_area(g, aoi)
    assert block.area > corridor.area                 # the lens got closed
    from shapely.geometry import Point
    centre = Point(-123.3985, 48.401175)              # middle of the block
    assert block.contains(centre) and not corridor.contains(centre)


def test_huge_face_interior_stays_unserved():
    """A face ringed by roads but km-deep (fields) keeps its interior honestly unserved."""
    aoi = _Aoi(-123.40, 48.40, -123.34, 48.44)        # ~4.4 x 4.4 km
    g = nx.Graph()
    coords = {(0, 0): (-123.395, 48.405), (1, 0): (-123.345, 48.405),
              (1, 1): (-123.345, 48.435), (0, 1): (-123.395, 48.435)}
    for n, (x, y) in coords.items():
        g.add_node(n, x=x, y=y, elev=10.0)
    g.add_edge((0, 0), (1, 0)); g.add_edge((1, 0), (1, 1))
    g.add_edge((1, 1), (0, 1)); g.add_edge((0, 1), (0, 0))

    block = block_aware_service_area(g, aoi)
    from shapely.geometry import Point
    assert not block.contains(Point(-123.37, 48.42))  # deep interior not served


def test_buildings_close_big_interiors_as_evidence():
    """A deep block (lens > threshold) WITH buildings inside is lots → served whole;
    the same block without buildings honestly keeps its interior unserved."""
    from shapely.geometry import Point, box as _box

    aoi = _Aoi(-123.40, 48.40, -123.394, 48.406)
    g = nx.Graph()
    coords = {(0, 0): (-123.3995, 48.4005), (1, 0): (-123.3960, 48.4005),
              (1, 1): (-123.3960, 48.4040), (0, 1): (-123.3995, 48.4040)}
    for n, (x, y) in coords.items():
        g.add_node(n, x=x, y=y, elev=10.0)
    g.add_edge((0, 0), (1, 0)); g.add_edge((1, 0), (1, 1))
    g.add_edge((1, 1), (0, 1)); g.add_edge((0, 1), (0, 0))
    centre = Point(-123.39775, 48.40225)

    bare = block_aware_service_area(g, aoi)
    house = _box(-123.3980, 48.4020, -123.3976, 48.4024)     # a roof in the interior
    with_evidence = block_aware_service_area(g, aoi, buildings=[house])
    assert not bare.contains(centre)
    assert with_evidence.contains(centre)


def test_frontage_split_beats_triangle_fans():
    """The municipal property: a point in front of a street edge's middle belongs to that
    edge's end junction — NOT to a diagonally-nearer intersection (the triangle-fan bug)."""
    from shapely.geometry import Point
    from swmmcanada.network.service_area import block_aware_service_area, edge_split_cells

    aoi = _Aoi(-123.40, 48.40, -123.394, 48.406)
    g = nx.Graph()
    # one ~150 x 300 m block: corners are junctions; the long west edge runs south-north
    coords = {(0, 0): (-123.3990, 48.4010), (1, 0): (-123.3970, 48.4010),
              (1, 1): (-123.3970, 48.4037), (0, 1): (-123.3990, 48.4037)}
    for n, (x, y) in coords.items():
        g.add_node(n, x=x, y=y, elev=10.0)
    g.add_edge((0, 0), (1, 0)); g.add_edge((1, 0), (1, 1))
    g.add_edge((1, 1), (0, 1)); g.add_edge((0, 1), (0, 0))
    jxy = {str(n): (d["x"], d["y"]) for n, d in g.nodes(data=True)}
    mask = block_aware_service_area(g, aoi)

    cells = edge_split_cells(g, jxy, mask, aoi)
    assert set(cells) == set(jxy)                        # every junction serves something
    # a point just INSIDE the block, in front of the west edge's lower half:
    probe = Point(-123.39875, 48.40155)
    owner = next(n for n, c in cells.items() if c.polygon_4326.contains(probe))
    assert owner == str((0, 0))                          # the west edge's south junction
    # cells are disjoint and jointly cover most of the mask
    from shapely.ops import unary_union
    union = unary_union([c.polygon_4326 for c in cells.values()])
    assert union.area >= mask.area * 0.95
    overlap = sum(c.polygon_4326.area for c in cells.values()) - union.area
    assert overlap <= union.area * 0.01


# --- ADR 0022 (#118): full-coverage synthesis semantics -------------------------------

def test_full_aoi_mask_tiles_the_suburban_blanks():
    """The Langford scenario in miniature: streets hug one corner of a larger drawn AOI.
    With the corridor mask the far land vanished (rain deleted); with the whole AOI as the
    mask (ADR 0022, what the pipeline now passes) the frontage split must tile ~everything,
    while cells keep their street-anchored municipal shapes."""
    import networkx as nx
    from swmmcanada.geo import aoi_from_geojson
    from swmmcanada.network.delineate_dem import delineate_junction_subcatchments
    from swmmcanada.network.service_area import MIN_CELL_HA, street_service_corridor

    # 1 km x 1 km AOI; two short streets only in the SW corner
    aoi = aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [-123.510, 48.440], [-123.496, 48.440], [-123.496, 48.449],
        [-123.510, 48.449], [-123.510, 48.440]]]})
    g = nx.Graph()
    g.add_node("a", x=-123.5095, y=48.4405)
    g.add_node("b", x=-123.5065, y=48.4405)
    g.add_node("c", x=-123.5065, y=48.4425)
    g.add_edge("a", "b"); g.add_edge("b", "c")
    # junction names == street node names, as synthesise_network guarantees
    junction_xy = {"a": (-123.5095, 48.4405), "b": (-123.5065, 48.4405),
                   "c": (-123.5065, 48.4425)}

    # old semantics: corridor mask -> most of the AOI unserved
    corridor = street_service_corridor(g, aoi)
    subs_old, _ = delineate_junction_subcatchments(
        junction_xy, aoi, streets=g, service_mask=corridor, min_cell_ha=MIN_CELL_HA)
    # new semantics: whole-AOI mask -> tiled
    subs_new, diag = delineate_junction_subcatchments(
        junction_xy, aoi, streets=g, service_mask=aoi.geometry, min_cell_ha=MIN_CELL_HA)

    aoi_ha = aoi.area_km2 * 100.0
    old_ha = sum(s.area_ha for s in subs_old)
    new_ha = sum(s.area_ha for s in subs_new)
    assert old_ha < aoi_ha * 0.5, "corridor mask should leave the blanks (the #118 bug)"
    assert new_ha > aoi_ha * 0.95, "full-coverage mask must tile the drawn AOI"
    assert all(s.outlet_node in junction_xy for s in subs_new)
