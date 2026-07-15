"""Unit tests for the internal seams of `delineate_catchbasin_subcatchments` (cities.base):
`_impervious_fraction` (roofs + road right-of-way attribution, pure metric geometry — no
Voronoi/shaping runs) and `_shape_cells` (seeds -> repaired cell polygons, parcel vs Voronoi).
Hand-made shapely geometries only; no city fixtures, no network assembly.
"""
import geopandas as gpd
import pytest
from shapely.geometry import box

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.base import (
    CatchbasinSubcatchmentConfig,
    _impervious_fraction,
    _shape_cells,
)

# --- _impervious_fraction ------------------------------------------------------

CELL = box(0.0, 0.0, 100.0, 100.0)            # 10,000 m² cell in a metric CRS


def _gdf(geoms):
    return gpd.GeoDataFrame(geometry=gpd.GeoSeries(geoms))


def _sidx(g):
    return g.sindex if len(g) else None       # same convention as the orchestrator


def test_no_parcels_is_max_imperv_and_not_parcel_based():
    """No parcels intersect the cell -> no land-use evidence: pct = max_imperv and
    parcel_based=False (the caller leaves the cell out of imperv_map) — a roof alone,
    without parcels, does not make the computation parcel-based."""
    par, bld = _gdf([]), _gdf([box(0, 0, 50, 50)])         # roof covers 25%, but no parcels
    pct, parcel_based = _impervious_fraction(CELL, par, _sidx(par), bld, _sidx(bld))
    assert pct == CatchbasinSubcatchmentConfig().max_imperv == 100.0
    assert parcel_based is False


def test_full_parcel_imperv_is_the_roof_share():
    """Parcels tile the whole cell -> no right-of-way; imperviousness is the roof share."""
    par = _gdf([box(0, 0, 100, 100)])
    bld = _gdf([box(0, 0, 50, 50)])                        # 2,500 m² roof of 10,000 m²
    pct, parcel_based = _impervious_fraction(CELL, par, _sidx(par), bld, _sidx(bld))
    assert parcel_based is True
    assert pct == pytest.approx(25.0)


def test_full_parcel_no_buildings_clamps_to_min_imperv():
    """Fully parcelled, roofless cell computes 0% impervious and clamps to min_imperv."""
    par, bld = _gdf([box(0, 0, 100, 100)]), _gdf([])
    pct, parcel_based = _impervious_fraction(CELL, par, _sidx(par), bld, _sidx(bld))
    assert parcel_based is True
    assert pct == CatchbasinSubcatchmentConfig().min_imperv


def test_roofs_union_right_of_way():
    """Half the cell is parcels (holding a 400 m² roof), the other half is road
    right-of-way: pct = (400 + 5,000) / 10,000 = 54%."""
    par = _gdf([box(0, 0, 50, 100)])
    bld = _gdf([box(10, 10, 30, 30)])
    pct, parcel_based = _impervious_fraction(CELL, par, _sidx(par), bld, _sidx(bld))
    assert parcel_based is True
    assert pct == pytest.approx(54.0)


# --- _shape_cells ----------------------------------------------------------------

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.418], [-123.368, 48.418], [-123.368, 48.422], [-123.372, 48.422], [-123.372, 48.418]]]})
SEEDS = {"CB1": (-123.3705, 48.4195), "CB2": (-123.3695, 48.4205)}


def test_shape_cells_without_parcels_is_voronoi_shaped():
    pieces, method, n_dropped = _shape_cells(SEEDS, [], AOI, "EPSG:32610")
    assert method == "voronoi"
    assert n_dropped == 0
    assert {cb for cb, *_ in pieces} == set(SEEDS)         # one repaired cell per seed
    for cb_id, i, poly_m, exterior in pieces:
        assert i == 0                                      # Voronoi -> single piece per basin
        assert poly_m.is_valid and poly_m.area >= 1.0
        assert len(exterior) >= 4 and exterior[0] == exterior[-1]   # closed 4326 ring


def test_shape_method_flows_into_public_diag():
    """The `_shape_cells` method string is what the public diagnostics report."""
    from swmmcanada.build.models import JunctionIn, NetworkIn

    net = NetworkIn(junctions=[JunctionIn("J1", invert_m=10, x=-123.371, y=48.419),
                               JunctionIn("J2", invert_m=9, x=-123.369, y=48.421)],
                    outfalls=[], conduits=[])
    cbs = [{"type": "Feature", "properties": {"AssetID": cb},
            "geometry": {"type": "Point", "coordinates": list(xy)}} for cb, xy in SEEDS.items()]
    subs, _, diag = base.delineate_catchbasin_subcatchments(net, cbs, [], [], AOI, crs="EPSG:32610")
    assert diag["method"] == "catchbasin+parcel/building (voronoi-shaped)"
    assert len(subs) == len(SEEDS)


# --- #130: conduit offsets + non-circular cross-sections through the assembler --------

def test_drop_structure_survives_as_inlet_offset():
    """A pipe entering a node ABOVE the node bottom (published end elevation higher than
    the min-of-ends node invert) must carry the difference as an offset, not lose it."""
    from swmmcanada.sources.cities.base import RawPipe, assemble_network
    deep = RawPipe("DEEP", (0.0, 0.0), (0.001, 0.0), inv_a=10.0, inv_b=9.0)
    drop = RawPipe("DROP", (0.001, 0.001), (0.001, 0.0), inv_a=12.0, inv_b=11.5)  # lands 2.5 m up
    res = assemble_network([deep, drop])
    by = {c.name: c for c in res.network.conduits if c.name in ("DEEP", "DROP")}
    assert by["DEEP"].inlet_offset_m == 0.0 and by["DEEP"].outlet_offset_m == 0.0
    assert by["DROP"].outlet_offset_m == 2.5              # 11.5 above the node's 9.0 bottom
    assert res.diagnostics["n_offset_ends"] >= 1


def test_noncircular_shape_maps_and_falls_back():
    from swmmcanada.sources.cities.base import RawPipe, assemble_network, swmm_shape
    assert swmm_shape("BOX", 1.2, 2.4) == ("RECT_CLOSED", 1.2, 2.4)
    assert swmm_shape("EGG", 0.9, 0.6) == ("EGG", 0.9, 0.6)
    assert swmm_shape("BOX", None, 2.4) == ("CIRCULAR", None, None)   # missing a dim
    assert swmm_shape("ROUND", 0.3, 0.3) == ("CIRCULAR", None, None)
    box = RawPipe("BOX1", (0.0, 0.0), (0.001, 0.0), inv_a=10.0, inv_b=9.0,
                  shape="BOX", height_m=1.2, width_m=2.4)
    res = assemble_network([box])
    c = next(c for c in res.network.conduits if c.name == "BOX1")
    assert c.shape == "RECT_CLOSED" and c.height_m == 1.2 and c.width_m == 2.4
    assert res.diagnostics["n_noncircular"] == 1
