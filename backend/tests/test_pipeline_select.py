"""The build pathway is auto-selected by AOI: a real-network city adapter where one covers
the AOI, else synthesize from open data."""
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import (
    build_from_aoi,
    build_from_calgary,
    build_from_kelowna,
    build_from_kitchener,
    build_from_london,
    build_from_ottawa,
    build_from_surrey,
    build_from_victoria,
    pipeline_for_aoi,
)


def _aoi(lon, lat, d=0.005):
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]})


def test_real_network_cities_selected():
    # (downtown point, expected adapter, substring of the mode label)
    cases = [
        (-123.367, 48.423, build_from_victoria, "Victoria"),    # Victoria, BC
        (-75.695, 45.42, build_from_ottawa, "Ottawa"),          # Ottawa, ON
        (-81.25, 42.98, build_from_london, "London"),           # London, ON
        (-80.49, 43.45, build_from_kitchener, "Kitchener"),     # Kitchener/Waterloo, ON
        (-114.06, 51.05, build_from_calgary, "Calgary"),        # Calgary, AB
        (-122.82, 49.12, build_from_surrey, "Surrey"),          # Surrey, BC
        (-119.47, 49.88, build_from_kelowna, "Kelowna"),        # Kelowna, BC
    ]
    for lon, lat, fn, label in cases:
        got_fn, mode = pipeline_for_aoi(_aoi(lon, lat))
        assert got_fn is fn and label in mode, (lon, lat, mode)


def test_uncovered_aoi_synthesizes():
    fn, mode = pipeline_for_aoi(_aoi(-79.38, 43.65))            # downtown Toronto — no adapter
    assert fn is build_from_aoi and "Synth" in mode
