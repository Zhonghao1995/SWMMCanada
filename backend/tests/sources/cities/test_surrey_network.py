"""City of Surrey storm-drain -> SWMM NetworkIn adapter tests.

Surrey has no node ids on mains, so topology is inferred from polyline endpoints (mirrors
Ottawa). Run against the REAL Surrey fixtures in tests/fixtures/surrey/ (35 gravity mains).
The build-compatibility test proves the inferred network is genuinely SWMM-valid by
round-tripping it through build_model.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import NetworkIn, RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.surrey import build_surrey_network

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "surrey"


def _load(name):
    data = json.loads((FIX / f"{name}.geojson").read_text())
    return data["features"] if isinstance(data, dict) else data


@pytest.fixture(scope="module")
def result():
    return build_surrey_network(
        {"pipes": _load("storm_pipes"), "outfalls": _load("outfalls")}
    )


# --- core network from fixtures -------------------------------------------------

def test_builds_network_with_nodes_and_links(result):
    assert isinstance(result, base.NetworkResult)
    net = result.network
    assert isinstance(net, NetworkIn)
    assert len(net.junctions) > 0
    assert len(net.outfalls) >= 1
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
    assert all(n and str(n).strip() for n in names), "no empty node names"
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


def test_inverts_are_monotonic_down_each_conduit(result):
    """Downstream invert must be <= upstream invert on each pipe (flow falls)."""
    net = result.network
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    for c in net.conduits:
        assert inv[c.to_node] <= inv[c.from_node] + 1e-9, (
            f"{c.name}: down {inv[c.to_node]} > up {inv[c.from_node]}"
        )


def test_a_real_outfall_resolves_from_the_drainage_devices_layer(result):
    """At least one captured 'Outlet' device coincides with a pipe endpoint, so the network
    must carry a direct outfall there (not only synthesized component sinks)."""
    direct = [o for o in result.network.outfalls if not o.name.startswith("OUT_")]
    assert direct, "expected at least one direct outfall from the Outlet devices"


def test_diagnostics_counts_match_network(result):
    net = result.network
    d = result.diagnostics
    assert d["city"] == "surrey"
    assert d["n_junctions"] == len(net.junctions)
    assert d["n_outfalls"] == len(net.outfalls)
    assert d["n_conduits"] == len(net.conduits)
    assert d["n_pipes_in"] == 35


def test_invert_gapfill_recorded(result):
    """One fixture main has a missing invert; assert it was gap-filled (count >= 1)."""
    assert result.diagnostics["n_inverts_gapfilled"] >= 1


def test_shape_histogram_recorded(result):
    """Original MAIN_SHAPE kept in diagnostics only (builder is circular-only)."""
    hist = result.diagnostics["shape_histogram"]
    assert sum(hist.values()) == 35


# --- build compatibility (the real proof) ---------------------------------------

def test_network_feeds_build_model(result, tmp_path):
    """Feed the real network + a fabricated subcatchment + tiny rain into build_model;
    the .inp must exist, contain the core sections, and re-parse (proves SWMM-validity)."""
    from swmmcanada.build.assemble import BuildResult, build_model

    net = result.network
    sub = SubcatchmentIn(
        name="S_TEST", outlet_node=net.junctions[0].name, area_ha=1.0,
        pct_imperv=50.0, width_m=100.0, pct_slope=1.0,
    )
    rain = RainfallSeries(
        timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1), datetime(2022, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.0],
    )
    config = BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2))

    res = build_model(network=net, subcatchments=[sub], rain=rain, config=config)

    assert isinstance(res, BuildResult)
    assert res.inp_path.exists()
    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS"):
        assert sec in res.sections_written
    from swmm_api import read_inp_file
    read_inp_file(str(res.inp_path))


def test_accepts_featurecollection_dict():
    """A FeatureCollection dict for pipes normalizes the same as a plain list of features."""
    pipes_fc = {"type": "FeatureCollection", "features": _load("storm_pipes")}
    res = build_surrey_network({"pipes": pipes_fc, "outfalls": _load("outfalls")})
    assert len(res.network.conduits) > 0


# --- unit tests -----------------------------------------------------------------

def test_material_roughness_pvc_and_cp():
    """PVC -> 0.010; Surrey's CP (concrete pipe) is not in the table -> concrete default 0.013."""
    assert base.material_roughness("PVC") == 0.010
    assert base.material_roughness("CP") == 0.013          # falls to default (concrete)
    assert base.material_roughness("CMP") == 0.024         # corrugated metal
    assert base.material_roughness("PE") == 0.011
    assert base.material_roughness(None) == 0.013
    assert base.material_roughness("") == 0.013


def test_main_size_mm_converts_to_metre_diameter():
    """MAIN_SIZE is mm; the adapter must divide by 1000 to get the SWMM diameter (m)."""
    pipe = {
        "type": "Feature",
        "properties": {"OBJECTID": 1, "FACILITYID": "DM1", "MAIN_TYPE2": "Gravity",
                       "MAIN_SIZE": 600, "MATERIAL": "PVC", "MAIN_SHAPE": "Circular",
                       "UP_ELEVATION": 20.0, "DOWN_ELEVATION": 18.0, "SHAPE.LEN": 30.0},
        "geometry": {"type": "LineString",
                     "coordinates": [[-122.82, 49.12], [-122.821, 49.121]]},
    }
    res = build_surrey_network({"pipes": [pipe], "outfalls": []})
    diam = [c.diameter_m for c in res.network.conduits if not c.name.startswith("C_OUT")]
    assert any(abs(d - 0.6) < 1e-9 for d in diam), f"expected 0.6 m diameter, got {diam}"


def test_multilinestring_endpoint_extraction():
    """A MultiLineString main (multi-part geometry) must yield first-part start and last-part
    end as the two endpoints — Surrey mains can come back multi-part."""
    from swmmcanada.sources.cities.surrey import _line_ends

    geom = {"type": "MultiLineString", "coordinates": [
        [[-122.820, 49.120], [-122.821, 49.121]],
        [[-122.821, 49.121], [-122.823, 49.123]],
    ]}
    a, b = _line_ends(geom)
    assert a == (-122.820, 49.120)
    assert b == (-122.823, 49.123)

    # a plain LineString with interior vertices: ends are first and last vertex
    line = {"type": "LineString",
            "coordinates": [[-122.80, 49.10], [-122.805, 49.105], [-122.81, 49.11]]}
    a2, b2 = _line_ends(line)
    assert a2 == (-122.80, 49.10)
    assert b2 == (-122.81, 49.11)

    # degenerate / empty geometry -> (None, None)
    assert _line_ends({"type": "LineString", "coordinates": []}) == (None, None)
    assert _line_ends(None) == (None, None)


def test_zero_elevation_is_kept_not_treated_as_missing():
    """Unlike Ottawa (0 == missing), 0 m is a real Surrey elevation (sea level) and must be
    preserved as an invert."""
    from swmmcanada.sources.cities.surrey import _num

    assert _num(0) == 0.0
    assert _num("0") == 0.0
    assert _num(None) is None
    assert _num("") is None
    assert _num("nope") is None
