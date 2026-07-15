"""City of Kelowna storm-sewer -> SWMM NetworkIn, via the shared cities.base assembler.

Kelowna has no node ids, so topology is inferred from pipe polyline endpoints (like Ottawa).
Run against the real Kelowna sub-area fixtures in tests/fixtures/kelowna/. The build-
compatibility test proves the inferred network is genuinely SWMM-valid by round-tripping it
through build_model.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.assemble import BuildResult, build_model
from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import NetworkIn, RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.kelowna import (
    _kelowna_roughness,
    _line_ends,
    _num,
    build_kelowna_network,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "kelowna"


def _load(name):
    data = json.loads((FIX / name).read_text())
    return data["features"] if isinstance(data, dict) else data


@pytest.fixture(scope="module")
def result():
    return build_kelowna_network(
        {"pipes": _load("storm_pipes.geojson"), "outfalls": _load("outfalls.geojson")}
    )


# --- core build -----------------------------------------------------------------

def test_builds_network_with_nodes_and_links(result):
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


def test_inverts_are_monotonic_on_junction_to_junction_conduits(result):
    """Downstream invert must be <= upstream invert on every pipe between two junctions
    (flow falls). Conduits that terminate at a *direct* outfall (a pipe end that coincides
    with a layer-4 outfall point) are excluded: base.assemble_network orients those toward the
    known outfall by topology, not by invert, and the outfall invert is back-filled
    independently — so it may legitimately sit slightly above the upstream junction. Those are
    covered by test_direct_outfall_conduits_point_at_the_outfall instead."""
    net = result.network
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    outfalls = {o.name for o in net.outfalls}
    checked = 0
    for c in net.conduits:
        if c.to_node in outfalls or c.from_node in outfalls:
            continue
        checked += 1
        assert inv[c.to_node] <= inv[c.from_node] + 1e-9, (
            f"{c.name}: down {inv[c.to_node]} > up {inv[c.from_node]}"
        )
    assert checked > 0, "expected some junction-to-junction conduits to check"


def test_direct_outfall_conduits_point_at_the_outfall(result):
    """Each conduit incident to an outfall is oriented so the outfall is its to_node (the
    SWMM single-link-outfall rule), proving the topological orientation held."""
    net = result.network
    outfalls = {o.name for o in net.outfalls}
    incident = Counter()
    for c in net.conduits:
        incident[c.from_node] += 1
        incident[c.to_node] += 1
    for c in net.conduits:
        if c.from_node in outfalls or c.to_node in outfalls:
            assert c.to_node in outfalls, f"{c.name} should drain INTO outfall, not out of it"
            assert incident[c.to_node] == 1


def test_diagnostics_counts_match_network(result):
    net = result.network
    d = result.diagnostics
    assert d["city"] == "kelowna"
    assert d["n_junctions"] == len(net.junctions)
    assert d["n_outfalls"] == len(net.outfalls)
    assert d["n_conduits"] == len(net.conduits)
    assert d["n_pipes_in"] == 46           # fixture has 46 storm mains


# --- build compatibility (the real proof) ---------------------------------------

def test_network_feeds_build_model(result, tmp_path):
    """Feed the real network + a fabricated subcatchment + tiny rain into build_model;
    the .inp must exist, expose the network sections, and re-parse (proves SWMM-validity)."""
    outlet = result.network.junctions[0].name
    sub = SubcatchmentIn(
        name="S_TEST", outlet_node=outlet, area_ha=1.0, pct_imperv=50.0,
        width_m=100.0, pct_slope=1.0,
    )
    rain = RainfallSeries(
        timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1), datetime(2022, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.0],
    )
    config = BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2))

    res = build_model(network=result.network, subcatchments=[sub], rain=rain, config=config)

    assert isinstance(res, BuildResult)
    assert res.inp_path.exists()
    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS"):
        assert sec in res.sections_written
    # explicit re-parse for good measure
    from swmm_api import read_inp_file

    read_inp_file(str(res.inp_path))


# --- unit tests: string->float casting (the Kelowna-specific gotcha) -------------

def test_num_casts_string_diameter_and_length():
    """DIAMETER and LENGTH arrive as STRINGS in Kelowna's data."""
    assert _num("300") == 300.0          # DIAMETER mm as string
    assert _num("47.46") == 47.46        # LENGTH m as string
    assert _num("0750") == 750.0


def test_num_treats_empty_zero_none_as_missing():
    assert _num("") is None
    assert _num(None) is None
    assert _num("0") is None             # 0 == missing
    assert _num(0) is None
    assert _num(0.0) is None
    assert _num("abc") is None           # non-numeric -> missing
    assert _num("N/A") is None


def test_num_passes_through_real_doubles():
    assert _num(388.22) == 388.22        # INVERT_*_Z are real doubles, not strings


def test_string_diameter_casting_on_real_fixture_data():
    """Every DIAMETER/LENGTH in the real fixture is a string; the adapter must cast them so
    the resulting conduits get sane diameters (and none default unless truly missing)."""
    pipes = _load("storm_pipes.geojson")
    assert all(isinstance(f["properties"]["DIAMETER"], str) for f in pipes)
    res = build_kelowna_network({"pipes": pipes, "outfalls": _load("outfalls.geojson")})
    # at least one conduit gets a non-default diameter from a string DIAMETER like "300"
    diams = {round(c.diameter_m, 4) for c in res.network.conduits}
    assert 0.30 in diams or 0.25 in diams, f"expected mm-string diameters cast to m, got {diams}"
    assert all(c.diameter_m > 0 for c in res.network.conduits)


# --- unit tests: material -> roughness (incl. Kelowna perforated/ribbed variants) -

def test_material_roughness_core_codes():
    default = 0.013
    assert _kelowna_roughness("AC", default) == 0.011       # asbestos cement
    assert _kelowna_roughness("CMP", default) == 0.024      # corrugated metal
    assert _kelowna_roughness("PVC", default) == 0.010
    assert _kelowna_roughness("pvc", default) == 0.010      # case-insensitive


def test_material_roughness_perforated_and_ribbed_variants():
    """Kelowna's PERFPVC/RIBPVC/PERFRIBPVC are PVC pipes -> PVC roughness, not the default."""
    default = 0.013
    assert _kelowna_roughness("PERFPVC", default) == 0.010   # perforated PVC
    assert _kelowna_roughness("RIBPVC", default) == 0.010    # ribbed PVC
    assert _kelowna_roughness("PERFRIBPVC", default) == 0.010
    assert _kelowna_roughness("PERFAC", default) == 0.011    # perforated AC -> AC


def test_material_roughness_rcp_vit_aliases():
    default = 0.013
    assert _kelowna_roughness("RCP", default) == 0.013       # reinforced concrete pipe
    assert _kelowna_roughness("VIT", default) == 0.013       # vitrified clay


def test_material_roughness_unknown_and_missing():
    default = 0.013
    assert _kelowna_roughness("UNOBTANIUM", default) == default
    assert _kelowna_roughness(None, default) == default
    assert _kelowna_roughness("", default) == default


# --- unit tests: polyline endpoint extraction (incl. MultiLineString) ------------

def test_line_ends_linestring():
    geom = {"type": "LineString", "coordinates": [[-119.47, 49.89], [-119.46, 49.90]]}
    a, b = _line_ends(geom)
    assert a == (-119.47, 49.89)
    assert b == (-119.46, 49.90)


def test_line_ends_multilinestring_flattens_to_outer_ends():
    """A MultiLineString's first part's start and last part's end are the pipe ends."""
    geom = {
        "type": "MultiLineString",
        "coordinates": [
            [[-119.47, 49.89], [-119.465, 49.895]],
            [[-119.465, 49.895], [-119.46, 49.90]],
        ],
    }
    a, b = _line_ends(geom)
    assert a == (-119.47, 49.89)
    assert b == (-119.46, 49.90)


def test_line_ends_drops_z_coordinate():
    geom = {"type": "LineString", "coordinates": [[-119.47, 49.89, 300.0], [-119.46, 49.90, 299.0]]}
    a, b = _line_ends(geom)
    assert a == (-119.47, 49.89)         # only (lon, lat) kept
    assert b == (-119.46, 49.90)


def test_line_ends_empty_or_degenerate():
    assert _line_ends(None) == (None, None)
    assert _line_ends({"type": "LineString", "coordinates": []}) == (None, None)
    assert _line_ends({"type": "LineString", "coordinates": [[-119.47, 49.89]]}) == (None, None)


# --- sanitary tracer (second tagged system, ADR 0011) ------------------------------

def test_sanitary_skeleton_assembles_from_fixture():
    """The recorded Sanitary Main fixture (STATUS='A' gravity lines; force mains live on
    their own unfetched layer) must assemble into a routable skeleton: junctions/conduits
    > 0 and every endpoint resolves (per-component sinks stand in for the treatment-bound
    exits)."""
    res = build_kelowna_network({"pipes": _load("sanitary_mains.geojson")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0
    assert len(net.outfalls) >= 1                           # per-component sinks exist
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in node_names and c.to_node in node_names for c in net.conduits)
    assert all(f["properties"]["STATUS"] == "A" for f in _load("sanitary_mains.geojson"))


# --- building Ground_Z rim proxy (ADR 0021 §7; audit 2026-07-14) ---------------------

def test_building_ground_z_proxies_node_max_depths():
    """Node rims are genuinely unpublished (internal Cityworks); nearby building Ground_Z
    (<=60 m) must lift max depths off the flat 2.0 m default, inverts untouched."""
    import json
    load = lambda n: json.load(open(FIX / f"{n}.geojson"))["features"]
    with_b = build_kelowna_network({"pipes": load("storm_pipes"), "outfalls": load("outfalls"),
                                    "buildings": load("buildings")})
    without = build_kelowna_network({"pipes": load("storm_pipes"), "outfalls": load("outfalls")})
    assert with_b.diagnostics["n_ground_proxy_points"] > 0
    assert "Ground_Z proxy" in with_b.diagnostics["ground_basis"]
    assert without.diagnostics["n_ground_proxy_points"] == 0
    nd = sum(1 for j in with_b.network.junctions if j.max_depth_m != 2.0)
    assert nd > len(with_b.network.junctions) * 0.5
    # inverts identical with/without the proxy — it must never touch the vertical profile
    inv_a = sorted(j.invert_m for j in with_b.network.junctions)
    inv_b = sorted(j.invert_m for j in without.network.junctions)
    assert inv_a == inv_b
