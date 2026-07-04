"""The build pathway is auto-selected by AOI: a real-network city adapter where one covers
the AOI, else synthesize from open data. Dispatch lives in the city registry."""
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_from_aoi, pipeline_for_aoi
from swmmcanada.sources.cities.registry import CITIES, city_for_point


def _aoi(lon, lat, d=0.005):
    return aoi_from_geojson({"type": "Polygon", "coordinates": [[
        [lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]})


def test_real_network_cities_selected():
    # (downtown point, expected registry key, substring of the mode label)
    cases = [
        (-123.367, 48.423, "victoria", "Victoria"),    # Victoria, BC
        (-75.695, 45.42, "ottawa", "Ottawa"),          # Ottawa, ON
        (-81.25, 42.98, "london", "London"),           # London, ON
        (-80.49, 43.45, "kitchener", "Kitchener"),     # Kitchener/Waterloo, ON
        (-114.06, 51.05, "calgary", "Calgary"),        # Calgary, AB
        (-122.82, 49.12, "surrey", "Surrey"),          # Surrey, BC
        (-119.47, 49.88, "kelowna", "Kelowna"),        # Kelowna, BC
        (-104.61, 50.445, "regina", "Regina"),         # Regina, SK
    ]
    for lon, lat, key, label in cases:
        assert city_for_point(lon, lat).key == key, (lon, lat)
        got_fn, mode = pipeline_for_aoi(_aoi(lon, lat))
        # pipeline_for_aoi binds build_city to the matched spec
        assert got_fn.args[0].key == key and label in mode, (lon, lat, mode)


def test_uncovered_aoi_synthesizes():
    assert city_for_point(-79.38, 43.65) is None                # downtown Toronto — no adapter
    fn, mode = pipeline_for_aoi(_aoi(-79.38, 43.65))
    assert fn is build_from_aoi and "Synth" in mode


def test_registry_invariants():
    """First-match dispatch is only sound if keys are unique and coverage boxes disjoint."""
    keys = [s.key for s in CITIES]
    assert len(keys) == len(set(keys))
    for i, a in enumerate(CITIES):
        for b in CITIES[i + 1:]:
            ax1, ay1, ax2, ay2 = a.coverage
            bx1, by1, bx2, by2 = b.coverage
            disjoint = ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1
            assert disjoint, f"coverage overlap: {a.key} vs {b.key}"
