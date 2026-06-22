"""TDD tests for geo.select_stations (spec 01 §6 tests 4 & 6). A triangular AOI lets a
station sit inside the bbox but outside the polygon — the case strict PIP must exclude."""
import geopandas as gpd
from shapely.geometry import Point

from swmmcanada.geo import AOI, aoi_from_geojson, select_stations
from swmmcanada.geo.stations import StationSelection

# Right triangle: BL, BR, TR. The top-left of the bbox is outside the polygon.
TRIANGLE = {
    "type": "Polygon",
    "coordinates": [
        [[-75.695, 45.400], [-75.682, 45.400], [-75.682, 45.418], [-75.695, 45.400]]
    ],
}


def _stations(rows):
    return gpd.GeoDataFrame(
        {"climate_id": [r[0] for r in rows]},
        geometry=[Point(r[1]) for r in rows],
        crs="EPSG:4326",
    )


class FakeSource:
    def __init__(self, gdf):
        self.gdf = gdf
        self.called_bbox = None

    def stations_in_bbox(self, bbox):
        self.called_bbox = bbox
        return self.gdf


def test_point_in_polygon_excludes_bbox_only_station():
    aoi = aoi_from_geojson(TRIANGLE)
    climate = FakeSource(
        _stations([("INSIDE", (-75.685, 45.405)), ("BBOX_ONLY", (-75.694, 45.417))])
    )
    hydro = FakeSource(_stations([]))
    sel = select_stations(aoi, climate=climate, hydro=hydro)

    assert isinstance(sel, StationSelection)
    assert list(sel.climate["climate_id"]) == ["INSIDE"]   # the bbox-only station is dropped
    assert len(sel.hydro) == 0
    assert climate.called_bbox == aoi.bbox                  # bbox pre-filter uses AOI.bbox


def test_empty_selection_is_non_fatal():
    aoi = aoi_from_geojson(TRIANGLE)
    src = FakeSource(_stations([]))
    sel = select_stations(aoi, climate=src, hydro=src)
    assert len(sel.climate) == 0 and len(sel.hydro) == 0
