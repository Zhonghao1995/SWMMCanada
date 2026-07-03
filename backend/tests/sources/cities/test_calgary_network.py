"""City of Calgary storm-sewer -> SWMM NetworkIn adapter (geometry-inferred topology).

Calgary publishes no node ids, so topology is inferred from pipe polyline endpoints by the
shared cities.base assembler (coordinate snapping). Run against the REAL downtown-Calgary
fixtures in tests/fixtures/calgary/ (38 STORM_PIPE polylines + 4 Bow River outfalls). The
build-compatibility test proves the inferred network is genuinely SWMM-valid by round-tripping
it through build_model.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import NetworkIn, RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.calgary import (
    CALGARY_CRS,
    build_calgary_network,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "calgary"


def _load(name):
    return json.loads((FIX / name).read_text())["features"]


@pytest.fixture(scope="module")
def storm():
    return {"pipes": _load("storm_pipes.geojson"), "outfalls": _load("outfalls.geojson")}


@pytest.fixture(scope="module")
def result(storm):
    return build_calgary_network(storm)


# --- core build (from real fixtures) --------------------------------------------

def test_builds_network_with_nodes_and_links(result):
    net = result.network
    assert isinstance(net, NetworkIn)
    assert len(net.junctions) > 0
    assert len(net.outfalls) > 0
    assert len(net.conduits) > 0


def test_every_conduit_endpoint_resolves_to_a_node(result):
    net = result.network
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    for c in net.conduits:
        assert c.from_node in node_names, f"{c.name} from_node {c.from_node} missing"
        assert c.to_node in node_names, f"{c.name} to_node {c.to_node} missing"
        assert c.from_node != c.to_node, f"{c.name} is a self-loop"


def test_no_duplicate_node_names(result):
    net = result.network
    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
    assert all(n and str(n).strip() for n in names), "empty node name"
    dupes = [n for n, c in Counter(names).items() if c > 1]
    assert dupes == [], f"duplicate node names: {dupes}"


def test_every_outfall_has_exactly_one_incident_link(result):
    net = result.network
    incident = Counter()
    for c in net.conduits:
        incident[c.from_node] += 1
        incident[c.to_node] += 1
    for o in net.outfalls:
        assert incident[o.name] == 1, f"outfall {o.name} has {incident[o.name]} links (need 1)"


def test_real_outfalls_resolve_to_direct_outfalls(result):
    """All 4 captured Bow River outfall points snap onto a pipe endpoint, so they must become
    direct (single-link) outfalls — not just dedicated component sinks."""
    assert result.diagnostics["n_outfall_points"] == 4
    assert result.diagnostics["n_direct_outfalls"] >= 1


def test_inverts_are_monotonic_on_every_conduit(result):
    """Downstream invert must be <= upstream invert on each pipe (flow falls). A Calgary 0
    invert is a missing-data sentinel and must NOT leak through as a bogus 0 m elevation."""
    net = result.network
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    for c in net.conduits:
        assert inv[c.to_node] <= inv[c.from_node] + 1e-9, (
            f"{c.name}: down {inv[c.to_node]} > up {inv[c.from_node]}"
        )


def test_no_bogus_zero_invert_nodes(result):
    """Calgary inverts are metres AMSL (~1040+). A node sitting at ~0 m would mean a 0-sentinel
    invert leaked in instead of being gap-filled."""
    net = result.network
    low = [j.name for j in net.junctions if j.invert_m < 100.0]
    assert low == [], f"nodes with implausibly low (sentinel-leaked) invert: {low}"


def test_invert_gapfill_recorded(result):
    """The fixture has pipes with a 0 / missing invert; assert they were gap-filled (count > 0)."""
    assert result.diagnostics["n_inverts_gapfilled"] >= 1


def test_diagnostics_counts_and_city(result):
    net = result.network
    d = result.diagnostics
    assert d["city"] == "calgary"
    assert d["n_junctions"] == len(net.junctions)
    assert d["n_outfalls"] == len(net.outfalls)
    assert d["n_conduits"] == len(net.conduits)
    assert d["n_pipes_in"] == 38


# --- build compatibility (the real proof) ---------------------------------------

def test_network_feeds_build_model(result, tmp_path):
    """Feed the real network + a fabricated subcatchment + tiny rain into build_model; the .inp
    must exist and re-parse (proves SWMM-validity), and carry JUNCTIONS/OUTFALLS/CONDUITS."""
    from swmm_api import read_inp_file

    from swmmcanada.build.assemble import BuildResult, build_model

    net = result.network
    sub = SubcatchmentIn(name="S_TEST", outlet_node=net.junctions[0].name, area_ha=1.0,
                         pct_imperv=50.0, width_m=100.0, pct_slope=1.0)
    rain = RainfallSeries(
        timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1), datetime(2022, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.0])
    config = BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2),
                         coordinate_crs=CALGARY_CRS)

    res = build_model(network=net, subcatchments=[sub], rain=rain, config=config)
    assert isinstance(res, BuildResult)
    assert res.inp_path.exists()
    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS"):
        assert sec in res.sections_written
    read_inp_file(str(res.inp_path))      # explicit second re-parse


def test_accepts_featurecollection_and_plain_list(storm):
    """A FeatureCollection dict and a plain feature list normalise the same; passing just the
    pipes list (no outfalls) still builds (components get dedicated sinks)."""
    fc = {"pipes": {"type": "FeatureCollection", "features": storm["pipes"]},
          "outfalls": storm["outfalls"]}
    res_fc = build_calgary_network(fc)
    res_list = build_calgary_network(storm["pipes"])      # bare list, no outfalls key
    assert len(res_fc.network.conduits) > 0
    assert len(res_list.network.conduits) > 0
    assert len(res_list.network.outfalls) >= 1            # dedicated sinks for each component


# --- unit tests -----------------------------------------------------------------

def test_material_roughness_mapping():
    """Calgary materials map through the shared table (CON/CONC -> 0.013, PVC -> 0.010, ...)."""
    assert base.material_roughness("CON") == 0.013      # concrete (Calgary's code)
    assert base.material_roughness("CONC") == 0.013
    assert base.material_roughness("PVC") == 0.010
    assert base.material_roughness("pvc") == 0.010       # case-insensitive
    assert base.material_roughness("CMP") == 0.024       # corrugated metal
    assert base.material_roughness("HDPE") == 0.011
    assert base.material_roughness("UNOBTANIUM") == 0.013   # unknown -> default
    assert base.material_roughness(None) == 0.013


def test_width_mm_to_diameter_m_and_circular_handling():
    from swmmcanada.sources.cities.calgary import _diameter_m

    assert _diameter_m({"HEIGHT": 200, "WIDTH": 200}) == 0.200   # circular: equal H/W -> WIDTH/1000
    assert _diameter_m({"HEIGHT": 600, "WIDTH": 450}) == 0.450   # non-circular -> equiv via WIDTH
    assert _diameter_m({"WIDTH": 0}) is None                      # 0 == missing
    assert _diameter_m({"WIDTH": None}) is None
    assert _diameter_m({}) is None


def test_zero_invert_is_treated_as_missing():
    from swmmcanada.sources.cities.calgary import _num

    assert _num(0) is None              # Calgary's missing-data sentinel
    assert _num("0") is None
    assert _num(1044.353) == 1044.353
    assert _num(None) is None
    assert _num("") is None
    assert _num("not-a-number") is None


def test_line_ends_handles_linestring_and_multilinestring():
    from swmmcanada.sources.cities.calgary import _line_ends

    ls = {"type": "LineString", "coordinates": [[-114.08, 51.05], [-114.07, 51.06]]}
    a, b = _line_ends(ls)
    assert a == (-114.08, 51.05) and b == (-114.07, 51.06)

    # MultiLineString -> flattened: first point of first part, last point of last part
    mls = {"type": "MultiLineString", "coordinates": [
        [[-114.08, 51.05], [-114.078, 51.052]],
        [[-114.078, 51.052], [-114.07, 51.06]]]}
    a2, b2 = _line_ends(mls)
    assert a2 == (-114.08, 51.05) and b2 == (-114.07, 51.06)

    assert _line_ends({"type": "LineString", "coordinates": []}) == (None, None)
    assert _line_ends({"type": "LineString", "coordinates": [[-114.08, 51.05]]}) == (None, None)


# --- manhole rims -> real node max-depths ------------------------------------------

def test_manhole_rims_set_real_max_depths(storm):
    """With the manholes layer, junctions coinciding with a manhole get
    max_depth = RIM_ELEV - invert instead of the 2 m assembler default."""
    with_rims = build_calgary_network({**storm, "manholes": _load("manholes.geojson")})
    without = build_calgary_network(storm)
    assert with_rims.diagnostics["n_ground_points"] == 20   # all 20 fixture rims plausible
    assert without.diagnostics["n_ground_points"] == 0

    depth_with = {j.name: j.max_depth_m for j in with_rims.network.junctions}
    depth_without = {j.name: j.max_depth_m for j in without.network.junctions}
    changed = [n for n in depth_with if depth_with[n] != depth_without[n]]
    assert changed, "no junction picked up a rim-based depth"
    for n in changed:
        assert 0 < depth_with[n] < 15.0                     # physically plausible depths
    # Junctions with no matching manhole keep the assembler default.
    assert any(depth_with[n] == depth_without[n] for n in depth_with)


def test_implausible_rims_are_screened():
    """A placeholder rim (0 sentinel, a dropped-digit typo) must be dropped by the
    plausibility band (~975-1300 m Calgary terrain), not turned into a bogus depth."""
    from swmmcanada.sources.cities.calgary import _rim

    assert _rim(1046.2) == 1046.2
    assert _rim(0) is None
    assert _rim(1.0) is None
    assert _rim(104.6) is None            # dropped-digit style typo
    assert _rim(None) is None


# --- sanitary tracer (second tagged system, ADR 0011) ------------------------------

def test_sanitary_skeleton_assembles_from_fixture():
    """The recorded SANITARY_PIPE fixture (ACTIVE gravity MAIN/TL lines) must assemble into
    a routable skeleton: junctions/conduits > 0 and every endpoint resolves (per-component
    sinks stand in for the treatment-bound exits)."""
    res = build_calgary_network({"pipes": _load("sanitary_pipes.geojson")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0
    assert len(net.outfalls) >= 1                           # per-component sinks exist
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in node_names and c.to_node in node_names for c in net.conduits)
