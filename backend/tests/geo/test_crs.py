"""The shared CRS seam (geo.crs): one UTM-zone decision + one projector, consumed by
pipeline, validate and build — previously three independent copies."""
from types import SimpleNamespace

import pytest

from swmmcanada.geo import lonlat_projector, utm_crs_for


def _aoi(min_lon, max_lon):
    return SimpleNamespace(bbox=(min_lon, 48.0, max_lon, 48.1))


def test_utm_zone_victoria():
    assert utm_crs_for(_aoi(-123.43, -123.33)) == "EPSG:32610"   # zone 10N


def test_utm_zone_ottawa():
    assert utm_crs_for(_aoi(-75.75, -75.60)) == "EPSG:32618"     # zone 18N


def test_projector_identity_without_crs():
    p = lonlat_projector(None)
    assert p(-75.69, 45.41) == (-75.69, 45.41)


def test_projector_projects_to_metres():
    x, y = lonlat_projector("EPSG:32618")(-75.69, 45.41)
    assert x == pytest.approx(446_000, abs=5_000)     # UTM 18N easting
    assert y == pytest.approx(5_029_000, abs=5_000)   # northing
