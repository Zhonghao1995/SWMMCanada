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
        (-123.12, 49.28, "vancouver", "Vancouver"),    # Vancouver, BC
        (-122.793, 49.276, "coquitlam", "Coquitlam"),  # Coquitlam, BC (Town Centre)
        (-79.694, 44.389, "barrie", "Barrie"),         # Barrie, ON (downtown)
        (-122.309, 49.035, "abbotsford", "Abbotsford"), # Abbotsford, BC
        (-106.665, 52.127, "saskatoon", "Saskatoon"),   # Saskatoon, SK (downtown)
        (-79.390, 43.650, "toronto", "Toronto"),        # Toronto, ON (King West)
        (-76.565, 44.259, "kingston", "Kingston"),      # Kingston, ON (Cataraqui West)
        (-78.324, 44.301, "peterborough", "Peterborough"), # Peterborough, ON
        (-122.999, 49.226, "burnaby", "Burnaby"),       # Burnaby, BC (Metrotown-north)
        (-123.999, 49.234, "nanaimo", "Nanaimo"),       # Nanaimo, BC (north)
        (-122.929, 49.207, "newwestminster", "New Westminster"),  # nests inside Burnaby box
        (-119.595, 49.487, "penticton", "Penticton"),   # Penticton, BC
        (-122.807, 49.023, "whiterock", "White Rock"),  # nests inside Surrey box
        (-82.400, 42.970, "sarnia", "Sarnia"),          # Sarnia, ON (downtown)
        (-78.945, 43.878, "whitby", "Whitby"),          # Whitby, ON
        (-123.410, 48.433, "esquimalt", "Esquimalt"),   # nests inside Victoria box
        (-64.805, 46.092, "moncton", "Moncton"),        # Moncton, NB (first Atlantic city)
    ]
    for lon, lat, key, label in cases:
        assert city_for_point(lon, lat).key == key, (lon, lat)
        got_fn, mode = pipeline_for_aoi(_aoi(lon, lat))
        # pipeline_for_aoi binds build_city to the matched spec
        assert got_fn.args[0].key == key and label in mode, (lon, lat, mode)


def test_uncovered_aoi_synthesizes():
    assert city_for_point(-97.14, 49.90) is None                # downtown Winnipeg — no adapter
    fn, mode = pipeline_for_aoi(_aoi(-97.14, 49.90))
    assert fn is build_from_aoi and "Synth" in mode


def test_registry_invariants():
    """Smallest-box dispatch is sound if keys are unique and any two coverage boxes are
    either disjoint or strictly nested (a suburb's tight box inside a neighbour's envelope).
    PARTIAL overlap stays illegal — it would create seams where 'smallest' is ambiguous."""
    keys = [s.key for s in CITIES]
    assert len(keys) == len(set(keys))
    for i, a in enumerate(CITIES):
        for b in CITIES[i + 1:]:
            ax1, ay1, ax2, ay2 = a.coverage
            bx1, by1, bx2, by2 = b.coverage
            disjoint = ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1
            a_in_b = bx1 <= ax1 and by1 <= ay1 and ax2 <= bx2 and ay2 <= by2
            b_in_a = ax1 <= bx1 and ay1 <= by1 and bx2 <= ax2 and by2 <= ay2
            assert disjoint or a_in_b or b_in_a, f"partial coverage overlap: {a.key} vs {b.key}"
            if a_in_b or b_in_a:
                area = lambda s: (s.coverage[2] - s.coverage[0]) * (s.coverage[3] - s.coverage[1])
                assert area(a) != area(b), f"nested boxes with equal area: {a.key} vs {b.key}"


def test_nested_coverage_prefers_smaller_box():
    """A point inside two nested boxes dispatches to the tighter one (synthetic specs —
    the live registry may or may not contain a nested pair at any given time)."""
    import swmmcanada.sources.cities.registry as reg
    outer = reg.CitySpec(key="outer", label="Outer", coverage=(-10.0, -10.0, 10.0, 10.0),
                         sub_crs="EPSG:32610", network_source="t", storm=None, land=None)
    inner = reg.CitySpec(key="inner", label="Inner", coverage=(-1.0, -1.0, 1.0, 1.0),
                         sub_crs="EPSG:32610", network_source="t", storm=None, land=None)
    orig = reg.CITIES
    reg.CITIES = (outer, inner)          # outer listed first: order must NOT win
    try:
        assert reg.city_for_point(0.0, 0.0).key == "inner"
        assert reg.city_for_point(5.0, 5.0).key == "outer"
        assert reg.city_for_point(50.0, 50.0) is None
    finally:
        reg.CITIES = orig
