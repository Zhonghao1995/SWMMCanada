"""City of Regina storm-sewer -> SWMM NetworkIn, via the shared cities.base assembler.

Regina has no node ids, so topology is inferred from pipe polyline endpoints (like Ottawa/
Calgary/Kelowna). Run against the real downtown-Regina fixtures in tests/fixtures/regina/.
The build-compatibility test proves the inferred network is genuinely SWMM-valid by
round-tripping it through build_model.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.assemble import BuildResult, build_model
from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import NetworkIn, RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.regina import (
    _invert,
    _line_ends,
    _num,
    _regina_roughness,
    build_regina_network,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "regina"


def _load(name):
    data = json.loads((FIX / name).read_text())
    return data["features"] if isinstance(data, dict) else data


@pytest.fixture(scope="module")
def result():
    return build_regina_network(
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
    known outfall by topology, not by invert — covered by
    test_direct_outfall_conduits_point_at_the_outfall instead."""
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


def test_dirty_source_inverts_do_not_leak_into_nodes(result):
    """The fixture really contains a placeholder invert (GISID 1244, both ends 1.0 m — Regina
    sits at ~570 m AMSL). The adapter's plausibility band must turn it into a missing value
    that gap-fills from neighbours, so every node invert stays in Regina's terrain range."""
    net = result.network
    for n in list(net.junctions) + list(net.outfalls):
        assert 500.0 < n.invert_m < 700.0, f"{n.name} invert {n.invert_m} outside plausible band"


def test_diagnostics_counts_match_network(result):
    net = result.network
    d = result.diagnostics
    assert d["city"] == "regina"
    assert d["n_junctions"] == len(net.junctions)
    assert d["n_outfalls"] == len(net.outfalls)
    assert d["n_conduits"] == len(net.conduits)
    assert d["n_pipes_in"] == 178          # fixture has 178 active gravity storm lines
    assert d["n_outfall_points"] == 15     # fixture has 15 Wascana-side outfall points


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


# --- unit tests: numeric casting + the invert plausibility band -------------------

def test_num_casts_and_treats_zero_as_missing():
    assert _num(250) == 250.0            # DIAMETER integer mm
    assert _num(57.912) == 57.912        # SURVEYLENGTH double m
    assert _num("") is None
    assert _num(None) is None
    assert _num(0) is None               # 0 == missing sentinel
    assert _num("abc") is None


def test_invert_accepts_plausible_regina_elevations():
    assert _invert(573.451) == 573.451
    assert _invert(567.373) == 567.373


def test_invert_rejects_placeholder_and_typo_values():
    """Real dirty values seen city-wide: '1.0' placeholders and a '57.23' dropped-digit typo
    must be treated as missing, not taken as ~sea-level inverts on the prairie."""
    assert _invert(1.0) is None
    assert _invert(57.23) is None
    assert _invert(0) is None
    assert _invert(None) is None


def test_dirty_fixture_pipe_has_its_placeholder_inverts_dropped():
    pipes = _load("storm_pipes.geojson")
    dirty = [f for f in pipes if f["properties"].get("GISID") == 1244]
    assert dirty, "fixture must keep the real dirty pipe as regression material"
    p = dirty[0]["properties"]
    assert p["STARTELEVATION"] == 1.0 and p["ENDELEVATION"] == 1.0
    assert _invert(p["STARTELEVATION"]) is None


# --- unit tests: material -> roughness (incl. Regina variants) --------------------

def test_material_roughness_core_codes():
    default = 0.013
    assert _regina_roughness("CONC", default) == 0.013
    assert _regina_roughness("CSP", default) == 0.024      # corrugated steel pipe
    assert _regina_roughness("PVC", default) == 0.010
    assert _regina_roughness("pvc", default) == 0.010      # case-insensitive
    assert _regina_roughness("AC", default) == 0.011       # asbestos cement
    assert _regina_roughness("STEEL", default) == 0.012
    assert _regina_roughness("CI", default) == 0.013       # cast iron


def test_material_roughness_pvc_variants():
    """Regina's "PVC RIBBED"/"PVC SDR35"/"PVC FLEXLOC"/"PVC PERMALOC" are PVC pipes."""
    default = 0.013
    assert _regina_roughness("PVC RIBBED", default) == 0.010
    assert _regina_roughness("PVC SDR35", default) == 0.010
    assert _regina_roughness("PVC FLEXLOC", default) == 0.010
    assert _regina_roughness("PVC PERMALOC", default) == 0.010


def test_material_roughness_regina_aliases():
    default = 0.013
    assert _regina_roughness("RCP", default) == 0.013                            # reinforced concrete
    assert _regina_roughness("VCT", default) == 0.013                            # vitrified clay tile
    assert _regina_roughness("TILE", default) == 0.013                           # clay tile
    assert _regina_roughness("PRELOAD", default) == 0.013                        # preload concrete
    assert _regina_roughness("CORREGATED GALVANIZED STEEL", default) == 0.024    # sp. as published
    assert _regina_roughness("CORREGATED ALUMINUM STEEL", default) == 0.024
    assert _regina_roughness("POLY", default) == 0.011                           # polyethylene
    assert _regina_roughness("PERFORATED POLY", default) == 0.011


def test_material_roughness_unknown_and_missing():
    default = 0.013
    assert _regina_roughness("UNKNOWN", default) == default
    assert _regina_roughness("WOOD", default) == default
    assert _regina_roughness(None, default) == default
    assert _regina_roughness("", default) == default


# --- unit tests: polyline endpoint extraction (incl. MultiLineString) ------------

def test_line_ends_linestring():
    geom = {"type": "LineString", "coordinates": [[-104.618, 50.445], [-104.617, 50.446]]}
    a, b = _line_ends(geom)
    assert a == (-104.618, 50.445)
    assert b == (-104.617, 50.446)


def test_line_ends_multilinestring_flattens_to_outer_ends():
    """A MultiLineString's first part's start and last part's end are the pipe ends."""
    geom = {
        "type": "MultiLineString",
        "coordinates": [
            [[-104.618, 50.445], [-104.6175, 50.4455]],
            [[-104.6175, 50.4455], [-104.617, 50.446]],
        ],
    }
    a, b = _line_ends(geom)
    assert a == (-104.618, 50.445)
    assert b == (-104.617, 50.446)


def test_line_ends_drops_z_coordinate():
    geom = {"type": "LineString", "coordinates": [[-104.618, 50.445, 570.0], [-104.617, 50.446, 569.5]]}
    a, b = _line_ends(geom)
    assert a == (-104.618, 50.445)       # only (lon, lat) kept
    assert b == (-104.617, 50.446)


def test_line_ends_empty_or_degenerate():
    assert _line_ends(None) == (None, None)
    assert _line_ends({"type": "LineString", "coordinates": []}) == (None, None)
    assert _line_ends({"type": "LineString", "coordinates": [[-104.618, 50.445]]}) == (None, None)


# --- manhole rims -> real node max-depths (refs #53) ------------------------------

def test_manhole_rims_set_real_max_depths():
    """With the manholes layer, junctions coinciding with a manhole get
    max_depth = RIMELEVATION - invert instead of the 2 m assembler default."""
    with_rims = build_regina_network({
        "pipes": _load("storm_pipes.geojson"), "outfalls": _load("outfalls.geojson"),
        "manholes": _load("manholes.geojson"),
    })
    without = build_regina_network({
        "pipes": _load("storm_pipes.geojson"), "outfalls": _load("outfalls.geojson"),
    })
    assert with_rims.diagnostics["n_ground_points"] > 100      # 113 plausible rims in the bbox
    assert without.diagnostics["n_ground_points"] == 0

    depth_with = {j.name: j.max_depth_m for j in with_rims.network.junctions}
    depth_without = {j.name: j.max_depth_m for j in without.network.junctions}
    changed = [n for n in depth_with if depth_with[n] != depth_without[n]]
    assert changed, "no junction picked up a rim-based depth"
    for n in changed:
        assert 0 < depth_with[n] < 15.0                        # physically plausible depths
    # Junctions with no matching manhole keep the assembler default.
    assert any(depth_with[n] == depth_without[n] for n in depth_with)


def test_implausible_rims_are_screened():
    """A placeholder rim (e.g. 1.0) must be dropped by the plausibility band, not turned
    into a bogus depth."""
    assert _invert(1.0) is None and _invert(57.23) is None and _invert(571.0) == 571.0
