"""Storm outlets follow the lateral that connects them (规划书 §5).

A catch basin's lead physically taps one main. Choosing the geometrically nearest pipe
instead gets it right most of the time and wrong exactly where two mains run close together
— which is where the errors concentrate. Twelve cities publish storm laterals; Victoria
publishes 11,969 of them, and none were used.
"""
from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.sources.cities import base

AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.3720, 48.4240], [-123.3660, 48.4240], [-123.3660, 48.4280],
    [-123.3720, 48.4280], [-123.3720, 48.4240]]]})


def point(name, lon, lat):
    return {"type": "Feature", "properties": {"AssetID": name},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def line(a, b):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]}}


def two_parallel_mains():
    """Two mains a short distance apart, as they run either side of a street. NORTH is
    nearer to the catch basin; SOUTH is the one the lateral actually reaches."""
    return NetworkIn(
        junctions=[JunctionIn("N1", 9.0, -123.3700, 48.4262),
                   JunctionIn("N2", 8.5, -123.3680, 48.4262),
                   JunctionIn("S1", 9.0, -123.3700, 48.4256),
                   JunctionIn("S2", 8.5, -123.3680, 48.4256)],
        outfalls=[OutfallIn("OUT_N", 7.0, -123.3665, 48.4262),
                  OutfallIn("OUT_S", 7.0, -123.3665, 48.4256)],
        conduits=[ConduitIn("CN", "N1", "N2", 150.0), ConduitIn("CN2", "N2", "OUT_N", 100.0),
                  ConduitIn("CS", "S1", "S2", 150.0), ConduitIn("CS2", "S2", "OUT_S", 100.0)])


def test_a_catch_basin_drains_to_the_main_its_lateral_reaches():
    """The tracer bullet: geometry says north, the lateral says south, the lateral wins."""
    cb_lon, cb_lat = -123.3690, 48.42605      # just south of the NORTH main
    cb = point("CB1", cb_lon, cb_lat)
    # The lead runs south, past the north main, to the south main.
    laterals = [line((cb_lon, cb_lat), (-123.3690, 48.4256))]

    subs, _imperv, _diag = base.delineate_catchbasin_subcatchments(
        two_parallel_mains(), [cb, point("CB2", -123.3672, 48.4259)], [], [], AOI,
        crs="EPSG:32610", laterals=laterals)

    cell = next(s for s in subs if s.name.startswith("S_CB1"))
    assert cell.outlet_node in ("S1", "S2"), (
        f"outlet {cell.outlet_node!r}: the lateral reaches the south main, but the north "
        f"main is geometrically nearer")


def test_without_laterals_the_nearest_main_still_wins():
    """The upgrade is additive. Most of the fleet publishes no storm laterals, and their
    delineation must be byte-identical to before."""
    cb_lon, cb_lat = -123.3690, 48.42605
    cbs = [point("CB1", cb_lon, cb_lat), point("CB2", -123.3672, 48.4259)]
    net = two_parallel_mains()

    subs, _i, _d = base.delineate_catchbasin_subcatchments(
        net, cbs, [], [], AOI, crs="EPSG:32610")
    cell = next(s for s in subs if s.name.startswith("S_CB1"))
    assert cell.outlet_node in ("N1", "N2"), "no lead published: geometry decides"


class TestLateralsReachTheDelineation:
    """A resolver nothing feeds is dead code. The leads have to travel from the city's
    open data, through the land fetch, into the delineation."""

    def test_victoria_land_includes_its_storm_laterals(self, monkeypatch):
        from swmmcanada.sources.cities import victoria

        asked = []

        def fake(base_url, layer, bbox, client):
            asked.append(layer)
            return []

        monkeypatch.setattr(victoria, "_fetch_layer_bbox", fake)
        land = victoria.fetch_victoria_land((-123.37, 48.42, -123.36, 48.43),
                                            client=object())
        assert "laterals" in land, "the leads are published; the land fetch must carry them"
        assert victoria.STORM_LATERALS in asked

    def test_the_pipeline_hands_them_to_the_delineator(self, monkeypatch):
        """Wired at the call site, so a future refactor that drops the argument fails here
        rather than silently reverting to nearest-pipe."""
        import inspect

        from swmmcanada import pipeline

        src = inspect.getsource(pipeline)
        assert "delineate_catchbasin_subcatchments(" in src
        # Read to the end of the CALL, not to the first ")" — the argument list contains
        # nested literals and slicing at the first bracket truncated inside one.
        start = src.index("delineate_catchbasin_subcatchments(")
        depth, end = 0, start
        for i in range(start + len("delineate_catchbasin_subcatchments"), len(src)):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        call = src[start:end]
        assert "laterals" in call, f"laterals not passed: {call}"


class TestWhatTheAgreementMetricCanAndCannotSee:
    """Measured on live Victoria (2026-08-12): the lead rule fires on 81% of catch basins
    and changes 21% of outlets, and the official-outlet agreement does not move
    (80.7% -> 80.1%).

    That is not evidence the rule is useless. The official catchments are drawn per
    OUTFALL, and two mains running either side of a street usually reach the same one — so
    changing which main an inlet taps is invisible to this yardstick. The metric validates
    outfall-level routing; it cannot referee a choice between adjacent mains.

    Pinned here because the temptation on a null result is to read it as "no effect" and
    revert something physically correct.
    """

    def test_two_mains_sharing_an_outfall_are_indistinguishable_to_the_metric(self):
        from swmmcanada.build.models import SurfaceCatchment
        from swmmcanada.validate.outlet_agreement import official_outlet_agreement

        ring = [(-123.3710, 48.4250), (-123.3670, 48.4250), (-123.3670, 48.4270),
                (-123.3710, 48.4270), (-123.3710, 48.4250)]
        net = NetworkIn(
            junctions=[JunctionIn("N1", 9.0, -123.3700, 48.4262),
                       JunctionIn("S1", 9.0, -123.3700, 48.4256)],
            outfalls=[OutfallIn("DOF1", 6.0, -123.3665, 48.4259)],
            conduits=[ConduitIn("CN", "N1", "DOF1", 120.0),
                      ConduitIn("CS", "S1", "DOF1", 120.0)])
        official = [{"type": "Feature",
                     "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
                     "properties": {"OUTLET": "DOF1"}}]

        rates = []
        for node in ("N1", "S1"):
            unit = SurfaceCatchment("S1c", node, 1.0, 50.0, 100.0, 1.0, polygon=ring)
            rate, _ = official_outlet_agreement([unit], net, official)
            rates.append(rate)
        assert rates == [1.0, 1.0], (
            "both mains reach the same outfall, so the metric scores either choice as "
            "correct — it cannot referee between them")


class TestKerbsReachTheDelineation:
    """Five cities publish kerb lines and kerb drops. Victoria publishes 26,809 kerbs and
    54,331 drops, in a service no adapter referenced until the capability scan found it."""

    def test_victoria_land_includes_kerbs_and_their_drops(self, monkeypatch):
        from swmmcanada.sources.cities import victoria

        asked = []

        def fake(base_url, layer, bbox, client):
            asked.append((base_url, layer))
            return []

        monkeypatch.setattr(victoria, "_fetch_layer_bbox", fake)
        land = victoria.fetch_victoria_land((-123.37, 48.42, -123.36, 48.43),
                                            client=object())
        assert "kerbs" in land and "kerb_openings" in land
        assert (victoria.PLANIMETRY_BASE, victoria.KERBS) in asked
        assert (victoria.PLANIMETRY_BASE, victoria.KERB_DROPS) in asked

    def test_a_city_without_kerbs_simply_has_none(self):
        """The key must be absent or empty, never a fabricated default — conditioning is
        skipped on falsy input and that is the honest behaviour for thirty cities."""
        from swmmcanada.sources.cities import ottawa

        assert not hasattr(ottawa, "KERBS")
