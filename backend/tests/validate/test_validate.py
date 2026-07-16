"""TDD for the subcatchment validation layer (PRD: subcatchment validation).

Tests assert external behaviour — which checks pass/fail and the report's verdict — on
hand-built models, not the internal merge steps.
"""
from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn, SubcatchmentIn
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.validate import MethodDescriptor, validate_model

# ~0.13 km² box (under the AOI cap), split by lon -123.370 into a left/right half.
AOI = aoi_from_geojson({"type": "Polygon", "coordinates": [[
    [-123.372, 48.418], [-123.368, 48.418], [-123.368, 48.422], [-123.372, 48.422], [-123.372, 48.418]]]})
NET = NetworkIn(
    junctions=[JunctionIn("J1", 10.0, -123.371, 48.420), JunctionIn("J2", 9.0, -123.369, 48.420)],
    outfalls=[], conduits=[])
METHOD = MethodDescriptor("catchbasin_voronoi", "nearest inlet service area", "low")
HALF_HA = AOI.area_km2 * 1e6 / 2 / 1e4          # area of one half, in hectares


def _rect(lo, la, lo2, la2):
    return [(lo, la), (lo2, la), (lo2, la2), (lo, la2)]


def _sub(name, outlet, ring, area_ha=HALF_HA):
    return SubcatchmentIn(name=name, outlet_node=outlet, area_ha=area_ha,
                          pct_imperv=50.0, width_m=100.0, pct_slope=1.0, polygon=ring)


LEFT = _rect(-123.372, 48.418, -123.370, 48.422)
RIGHT = _rect(-123.370, 48.418, -123.368, 48.422)


def _clean_subs():
    return [_sub("A", "J1", LEFT), _sub("B", "J2", RIGHT)]


def _ids(report):
    return {c.id: c for c in report.checks}


# --- the happy path -----------------------------------------------------------


def test_clean_model_is_ok_with_no_failures():
    r = validate_model(NET, _clean_subs(), AOI, method=METHOD)
    assert r.ok
    assert r.errors == [] and r.warnings == []


# --- topological errors -------------------------------------------------------


def test_missing_outlet_is_error():
    subs = _clean_subs()
    subs[0] = _sub("A", "", LEFT)
    r = validate_model(NET, subs, AOI, method=METHOD)
    assert not r.ok and not _ids(r)["outlet_present"].passed


def test_dangling_outlet_is_error():
    subs = _clean_subs()
    subs[0] = _sub("A", "NOPE", LEFT)
    r = validate_model(NET, subs, AOI, method=METHOD)
    assert not r.ok and not _ids(r)["outlet_exists"].passed


def test_zero_area_is_error():
    subs = _clean_subs()
    subs[0] = _sub("A", "J1", LEFT, area_ha=0.0)
    r = validate_model(NET, subs, AOI, method=METHOD)
    assert not r.ok and not _ids(r)["area_positive"].passed


# --- geometric errors / the Victoria blank-hole symptom -----------------------


def test_blank_hole_is_flagged():
    # Only the left half exists -> the right half is an uncovered AOI hole (the Victoria symptom).
    r = validate_model(NET, [_sub("A", "J1", LEFT)], AOI, method=METHOD)
    cov = _ids(r)["aoi_coverage"]
    assert not cov.passed
    assert cov.metrics["uncovered_fraction"] > 0.4          # ~half the AOI is blank
    assert not r.ok                                          # >10% uncovered -> error


def test_overlap_is_error():
    # Two cells on the same left half -> 50% overlap (double-counted runoff).
    r = validate_model(NET, [_sub("A", "J1", LEFT), _sub("B", "J2", LEFT)], AOI, method=METHOD)
    assert not _ids(r)["overlap"].passed and not r.ok


def test_cell_mostly_outside_aoi_is_error():
    outside = _rect(-123.366, 48.418, -123.364, 48.422)     # entirely east of the AOI
    subs = _clean_subs() + [_sub("C", "J1", outside)]
    r = validate_model(NET, subs, AOI, method=METHOD)
    assert not _ids(r)["aoi_containment"].passed and not r.ok


# --- warnings (do not block) --------------------------------------------------


def test_far_outlet_is_warning_not_error():
    # Tile the AOI fully but swap outlets so each cell drains to the far node (~70 m away).
    subs = [_sub("A", "J2", LEFT), _sub("B", "J1", RIGHT)]
    r = validate_model(NET, subs, AOI, method=METHOD)
    dist = _ids(r)["outlet_distance"]
    assert not dist.passed and dist.metrics["n_gt_50m"] >= 1
    assert r.ok                                              # distance is a warning, never blocks


def test_polygon_none_warns_but_topology_still_ok():
    # Cell A covers the whole AOI (no blank); cell B carries no polygon -> geometry_absent warns,
    # geometric checks skip B, topology is fine -> the model is not blocked.
    full = _rect(-123.372, 48.418, -123.368, 48.422)
    subs = [_sub("A", "J1", full), SubcatchmentIn("B", "J2", area_ha=HALF_HA, pct_imperv=50.0,
                                                  width_m=100.0, pct_slope=1.0, polygon=None)]
    r = validate_model(NET, subs, AOI, method=METHOD)
    assert not _ids(r)["geometry_absent"].passed            # the None cell is flagged (warning)
    assert r.ok                                              # ...but topology is fine -> not blocked


# --- invert consistency (issue #77) --------------------------------------------


def _j(name, invert, lon=-123.3695, lat=48.421):
    return JunctionIn(name, invert, lon, lat)


def test_descending_conduit_inverts_pass():
    net = NetworkIn(junctions=[_j("J1", 10.0, -123.371, 48.420), _j("J2", 9.0, -123.369, 48.420)],
                    outfalls=[], conduits=[ConduitIn("C1", "J1", "J2", length_m=100.0)])
    r = validate_model(net, _clean_subs(), AOI, method=METHOD)
    assert _ids(r)["invert_consistency"].passed


def test_local_pit_junction_is_flagged_by_name():
    # The Kelowna N16 shape: J2 back-filled below both neighbours -> its only exit rises.
    net = NetworkIn(
        junctions=[_j("J1", 10.0, -123.371, 48.420), _j("J2", 8.0, -123.369, 48.420), _j("J3", 9.0)],
        outfalls=[OutfallIn("OF1", 8.5, -123.367, 48.420)],   # system-complete (round-2 gate)
        conduits=[ConduitIn("C1", "J1", "J2", length_m=100.0),
                  ConduitIn("C2", "J2", "J3", length_m=100.0),
                  ConduitIn("C3", "J3", "OF1", length_m=50.0)])
    r = validate_model(net, _clean_subs(), AOI, method=METHOD)
    chk = _ids(r)["invert_consistency"]
    assert not chk.passed
    assert chk.metrics["n_adverse_conduits"] == 1 and chk.metrics["sample_conduits"] == ["C2"]
    assert chk.metrics["n_pit_junctions"] == 1 and chk.metrics["sample_pits"] == ["J2"]
    assert chk.metrics["max_rise_m"] == 1.0
    assert r.ok                                              # data-quality warning, never blocks


def test_rise_within_tolerance_passes():
    net = NetworkIn(junctions=[_j("J1", 10.0, -123.371, 48.420), _j("J2", 10.005, -123.369, 48.420)],
                    outfalls=[], conduits=[ConduitIn("C1", "J1", "J2", length_m=100.0)])
    r = validate_model(net, _clean_subs(), AOI, method=METHOD)
    assert _ids(r)["invert_consistency"].passed


def test_adverse_conduit_with_a_falling_sibling_is_not_a_pit():
    # J1 has one falling exit and one rising exit -> water can still leave: adverse, no pit.
    net = NetworkIn(
        junctions=[_j("J1", 10.0, -123.371, 48.420), _j("J2", 9.0, -123.369, 48.420), _j("J3", 11.0)],
        outfalls=[],
        conduits=[ConduitIn("C1", "J1", "J2", length_m=100.0),
                  ConduitIn("C2", "J1", "J3", length_m=100.0)])
    r = validate_model(net, _clean_subs(), AOI, method=METHOD)
    chk = _ids(r)["invert_consistency"]
    assert not chk.passed
    assert chk.metrics["n_adverse_conduits"] == 1
    assert chk.metrics["n_pit_junctions"] == 0


# --- serialisation ------------------------------------------------------------


def test_to_dict_shape():
    d = validate_model(NET, _clean_subs(), AOI, method=METHOD).to_dict()
    assert d["validation_version"] and d["subcatchment_method"] == "catchbasin_voronoi"
    assert d["ok"] is True
    assert d["summary"]["n_subcatchments"] == 2
    assert {"id", "severity", "passed", "message", "metrics"} <= set(d["checks"][0])
