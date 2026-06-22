"""The build pathway is auto-selected by AOI: a real-network city adapter where one covers
the AOI, else synthesize from open data."""
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import (
    build_from_aoi,
    build_from_ottawa,
    build_from_victoria,
    pipeline_for_aoi,
)


def _aoi(lon, lat, d=0.005):
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]})


def test_real_network_cities_selected():
    fn, mode = pipeline_for_aoi(_aoi(-123.367, 48.423))      # downtown Victoria
    assert fn is build_from_victoria and "Victoria" in mode

    fn, mode = pipeline_for_aoi(_aoi(-75.695, 45.42))        # downtown Ottawa
    assert fn is build_from_ottawa and "Ottawa" in mode


def test_uncovered_aoi_synthesizes():
    fn, mode = pipeline_for_aoi(_aoi(-114.06, 51.05))        # Calgary — no adapter
    assert fn is build_from_aoi and "Synth" in mode
