"""TDD for Voronoi subcatchment delineation (borrowed method, reimplemented)."""
import math

from shapely.geometry import box

from swmmcanada.geo import aoi_from_geojson
from swmmcanada.network.subcatchments import delineate_subcatchments

# ~3 km² Ottawa box AOI.
BOX_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]
    ],
}


def test_voronoi_partitions_aoi_among_nodes():
    aoi = aoi_from_geojson(BOX_GEOJSON)
    # Four manholes in the four quadrants of the box.
    points = {
        "A": (-75.695, 45.4125),
        "B": (-75.685, 45.4125),
        "C": (-75.695, 45.4175),
        "D": (-75.685, 45.4175),
    }
    cells = delineate_subcatchments(points, aoi.geometry)

    assert set(cells) == set(points)                       # one cell per node
    for c in cells.values():
        assert c.area_m2 > 0
        assert c.polygon_4326.within(aoi.geometry.buffer(1e-9))  # cells stay inside the AOI
        assert len(c.exterior) >= 4                          # a real ring

    # Cells partition the AOI: their areas sum to ~the AOI area.
    total = sum(c.area_m2 for c in cells.values())
    assert math.isclose(total, aoi.area_km2 * 1e6, rel_tol=0.02)
    # Symmetric layout → roughly equal quarters.
    quarter = aoi.area_km2 * 1e6 / 4
    for c in cells.values():
        assert 0.5 * quarter < c.area_m2 < 1.5 * quarter


def test_too_few_points_returns_empty():
    aoi = aoi_from_geojson(BOX_GEOJSON)
    assert delineate_subcatchments({"only": (-75.69, 45.415)}, aoi.geometry) == {}


def test_delineation_is_deterministic():
    """Same input delineated twice -> identical cells (reproducible builds; PRD #2)."""
    aoi = aoi_from_geojson(BOX_GEOJSON)
    points = {"A": (-75.695, 45.4125), "B": (-75.685, 45.4125),
              "C": (-75.695, 45.4175), "D": (-75.685, 45.4175)}
    a = delineate_subcatchments(points, aoi.geometry)
    b = delineate_subcatchments(points, aoi.geometry)
    assert set(a) == set(b)
    for k in a:
        assert math.isclose(a[k].area_m2, b[k].area_m2, rel_tol=1e-12)
        assert a[k].exterior == b[k].exterior
