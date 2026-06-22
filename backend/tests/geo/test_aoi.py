"""TDD tests for geo.aoi_from_geojson (spec 01 §6). Offline, deterministic."""
import json
import math

import pytest

from swmmcanada.config import MAX_AOI_KM2
from swmmcanada.geo import AOI, aoi_from_geojson
from swmmcanada.geo.errors import (
    AOICRSUnsupportedError,
    AOIEmptyError,
    AOIGeometryTypeError,
    AOIOversizeError,
)

# ~2 km² rectangle near the Rideau River, Ottawa (WGS84, exterior CCW).
OTTAWA = {
    "type": "Polygon",
    "coordinates": [
        [
            [-75.695, 45.400],
            [-75.682, 45.400],
            [-75.682, 45.418],
            [-75.695, 45.418],
            [-75.695, 45.400],
        ]
    ],
}
FEATURE = {"type": "Feature", "geometry": OTTAWA, "properties": {}}
FC = {"type": "FeatureCollection", "features": [FEATURE]}

# ~35 km² rectangle (> 25 km² cap).
OVERSIZE = {
    "type": "Polygon",
    "coordinates": [
        [[-75.74, 45.36], [-75.66, 45.36], [-75.66, 45.41], [-75.74, 45.41], [-75.74, 45.36]]
    ],
}
LINE = {"type": "LineString", "coordinates": [[-75.69, 45.40], [-75.68, 45.41]]}
# Bowtie (self-intersecting) — make_valid should repair to two triangles.
BOWTIE = {
    "type": "Polygon",
    "coordinates": [
        [
            [-75.695, 45.400],
            [-75.682, 45.418],
            [-75.682, 45.400],
            [-75.695, 45.418],
            [-75.695, 45.400],
        ]
    ],
}
# Degenerate zero-area polygon.
ZERO = {
    "type": "Polygon",
    "coordinates": [[[-75.69, 45.40], [-75.69, 45.40], [-75.69, 45.40], [-75.69, 45.40]]],
}
# GeoJSON with a legacy non-4326 CRS member.
CRS3857 = {
    "type": "Feature",
    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
    "geometry": OTTAWA,
    "properties": {},
}


def test_polygon_basic():
    aoi = aoi_from_geojson(OTTAWA)
    assert isinstance(aoi, AOI)
    assert aoi.crs == "EPSG:4326"
    assert aoi.source == "geojson"
    minlon, minlat, maxlon, maxlat = aoi.bbox
    assert minlon < maxlon and minlat < maxlat
    assert math.isclose(minlon, -75.695, abs_tol=1e-6)
    assert math.isclose(maxlat, 45.418, abs_tol=1e-6)
    # Equal-area: ~2 km². A degree-area bug would yield ~0.0002 (deg²).
    assert 1.7 < aoi.area_km2 < 2.4
    assert aoi.geometry.is_valid and not aoi.geometry.is_empty


def test_accepts_feature_and_featurecollection():
    a1 = aoi_from_geojson(OTTAWA)
    a2 = aoi_from_geojson(FEATURE)
    a3 = aoi_from_geojson(FC)
    assert math.isclose(a1.area_km2, a2.area_km2, rel_tol=1e-9)
    assert math.isclose(a1.area_km2, a3.area_km2, rel_tol=1e-9)
    assert a1.bbox == a2.bbox == a3.bbox


def test_accepts_json_string():
    aoi = aoi_from_geojson(json.dumps(OTTAWA))
    assert 1.7 < aoi.area_km2 < 2.4


def test_oversize_raises():
    with pytest.raises(AOIOversizeError) as exc:
        aoi_from_geojson(OVERSIZE)
    assert str(int(MAX_AOI_KM2)) in str(exc.value)


def test_wrong_geometry_type_raises():
    with pytest.raises(AOIGeometryTypeError):
        aoi_from_geojson(LINE)


def test_zero_area_raises():
    with pytest.raises(AOIEmptyError):
        aoi_from_geojson(ZERO)


def test_selfintersection_repaired():
    aoi = aoi_from_geojson(BOWTIE)
    assert aoi.geometry.is_valid
    assert aoi.area_km2 > 0


def test_non4326_crs_member_rejected():
    with pytest.raises(AOICRSUnsupportedError):
        aoi_from_geojson(CRS3857)
