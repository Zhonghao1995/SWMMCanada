"""Tests for the Kitchener / Region of Waterloo storm-drain -> SWMM NetworkIn adapter.

Run against the REAL central-Kitchener fixtures (55 Storm_Pipes) in
tests/fixtures/kitchener/. The build-compatibility test proves the resulting network is
genuinely SWMM-valid by round-tripping it through build_model.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import NetworkIn, RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.kitchener import (
    KITCHENER_CRS,
    KitchenerNetworkConfig,
    KitchenerNetworkResult,
    build_kitchener_network,
    material_roughness,
    resolve_endpoints,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "kitchener"


def _load(name: str) -> list:
    data = json.loads((FIXTURES / f"{name}.geojson").read_text())
    return data["features"] if isinstance(data, dict) else data


@pytest.fixture(scope="module")
def kitchener_inputs():
    return {
        "pipes": _load("pipes"),
        "manholes": _load("manholes"),
        "outlets": _load("outlets"),
    }


@pytest.fixture(scope="module")
def result(kitchener_inputs) -> KitchenerNetworkResult:
    return build_kitchener_network(**kitchener_inputs)


# --- core build -----------------------------------------------------------------

def test_builds_network_with_nodes_and_links(result):
    assert isinstance(result, KitchenerNetworkResult)
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


def test_no_duplicate_node_names(result):
    net = result.network
    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
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


def test_inverts_are_monotonic_on_every_conduit(result):
    """Downstream invert must be <= upstream invert on each pipe (flow falls)."""
    net = result.network
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    for c in net.conduits:
        assert inv[c.to_node] <= inv[c.from_node] + 1e-9, (
            f"{c.name}: down {inv[c.to_node]} > up {inv[c.from_node]}"
        )


def test_diagnostics_counts_match_network(result):
    net = result.network
    d = result.diagnostics
    assert d["n_junctions"] == len(net.junctions)
    assert d["n_outfalls"] == len(net.outfalls)
    assert d["n_conduits"] == len(net.conduits)


def test_real_outlets_produce_at_least_one_direct_outfall(result):
    """The captured Storm_Outlets coincide with pipe endpoints, so the assembler must wire at
    least one of them as a DIRECT outfall (not only dedicated component sinks)."""
    assert result.diagnostics["n_direct_outfalls"] >= 1


def test_dangling_endpoints_were_handled(result):
    """Pipes whose DN/UP manhole id is -1 (or absent) fall back to polyline vertices; the
    fixtures contain such pipes, so the count must be > 0."""
    assert result.diagnostics["n_dangling_nodes"] > 0


def test_no_inverts_synthesized_from_fixtures(result):
    """Every fixture pipe has real UP/DN inverts, so NO node invert should be gap-filled."""
    assert result.diagnostics["n_inverts_gapfilled"] == 0


def test_shape_histogram_recorded(result):
    """Original PIPE_SHAPE kept in diagnostics only (builder is circular-only)."""
    hist = result.diagnostics["shape_histogram"]
    assert sum(hist.values()) == result.diagnostics["n_pipes_in"]
    assert "ROUND" in hist


# --- build compatibility (the real proof) ---------------------------------------

def test_network_feeds_build_model(result, tmp_path):
    """Feed the real network + a fabricated subcatchment + tiny rain into build_model;
    the .inp must exist and re-parse (proves SWMM-validity)."""
    from swmmcanada.build.assemble import BuildResult, build_model

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
    # explicit second re-parse for good measure
    from swmm_api import read_inp_file

    read_inp_file(str(res.inp_path))


# --- unit tests -----------------------------------------------------------------

def test_material_roughness_mapping():
    cfg = KitchenerNetworkConfig()
    assert material_roughness("PVC", cfg) == 0.010
    assert material_roughness("CSP", cfg) == 0.024        # corrugated steel pipe
    assert material_roughness("HDPE", cfg) == 0.011
    assert material_roughness("AC", cfg) == 0.011         # asbestos cement
    # concrete codes used in this feed resolve to the concrete value (0.013, via the default)
    assert material_roughness("CP", cfg) == 0.013
    assert material_roughness("RCP", cfg) == 0.013
    # the dominant "unknown" placeholder must fall back to the default, not blow up
    assert material_roughness("XXX", cfg) == cfg.default_roughness
    assert material_roughness("X", cfg) == cfg.default_roughness
    assert material_roughness(None, cfg) == cfg.default_roughness
    assert material_roughness("", cfg) == cfg.default_roughness


def test_diameter_from_width_height_mm():
    """WIDTH/HEIGHT are millimetres; a circular pipe (WIDTH==HEIGHT) gives that diameter in
    metres, and a non-square section uses the larger dimension."""
    coords = {1: (-80.49, 43.44), 2: (-80.489, 43.4405)}
    line = [[-80.49, 43.44], [-80.489, 43.4405]]
    pipe = {
        "type": "Feature",
        "properties": {"STMPIPEID": 1, "UP_STMMANHOLEID": 1, "DN_STMMANHOLEID": 2,
                       "UP_INVERT": 100.0, "DN_INVERT": 99.0, "WIDTH": 450, "HEIGHT": 600,
                       "PIPE_SHAPE": "BOX", "MATERIAL": "CP", "LENGTH": 50.0},
        "geometry": {"type": "LineString", "coordinates": line},
    }
    mh = [
        {"type": "Feature", "properties": {"STMMANHOLEID": 1, "COVER_ELEVATION": 103.0},
         "geometry": {"type": "Point", "coordinates": list(coords[1])}},
        {"type": "Feature", "properties": {"STMMANHOLEID": 2, "COVER_ELEVATION": 102.0},
         "geometry": {"type": "Point", "coordinates": list(coords[2])}},
    ]
    res = build_kitchener_network(pipes=[pipe], manholes=mh, outlets=[])
    diam = {c.diameter_m for c in res.network.conduits}
    assert max(diam) == pytest.approx(0.600)              # 600 mm -> 0.6 m (larger of W/H)


def test_resolve_endpoints_integer_ids_and_dangling():
    """Known integer ids take their manhole coord; a -1 / absent id (dangling) takes the
    matching polyline vertex; both-dangling -> line[0]=up, line[-1]=down."""
    coords = {406542: (-80.4798, 43.4470)}   # only the upstream manhole is present

    line = [[-80.4798, 43.4470], [-80.4799, 43.44706]]
    # upstream known, downstream is the -1 sentinel
    up, dn, n = resolve_endpoints(406542, -1, line, coords)
    assert up == (-80.4798, 43.4470)
    assert dn == (-80.4799, 43.44706)        # from the far polyline vertex
    assert n == 1

    # downstream known, upstream absent from the manhole layer (dangling, not a sentinel)
    coords2 = {405859: (-80.4799, 43.44706)}
    up2, dn2, n2 = resolve_endpoints(999999, 405859, line, coords2)
    assert up2 == (-80.4798, 43.4470)        # from line[0]
    assert dn2 == (-80.4799, 43.44706)
    assert n2 == 1

    # both dangling -> positional fallback
    line3 = [[-80.50, 43.44], [-80.49, 43.45]]
    up3, dn3, n3 = resolve_endpoints(-1, -1, line3, {})
    assert up3 == (-80.50, 43.44)
    assert dn3 == (-80.49, 43.45)
    assert n3 == 2


def test_dangling_pipe_survives_full_build():
    """A pipe with a -1 downstream id must survive assembly: its downstream end becomes a node
    (a dedicated outfall is wired since the component has no outlet), so the conduit is kept."""
    a, b = (-80.490, 43.441), (-80.489, 43.4415)
    pipe = {
        "type": "Feature",
        "properties": {"STMPIPEID": 7, "UP_STMMANHOLEID": 50, "DN_STMMANHOLEID": -1,
                       "UP_INVERT": 100.0, "DN_INVERT": 99.0, "WIDTH": 300, "HEIGHT": 300,
                       "PIPE_SHAPE": "ROUND", "MATERIAL": "PVC", "LENGTH": 60.0},
        "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]},
    }
    mh = [{"type": "Feature", "properties": {"STMMANHOLEID": 50, "COVER_ELEVATION": 103.0},
           "geometry": {"type": "Point", "coordinates": list(a)}}]
    res = build_kitchener_network(pipes=[pipe], manholes=mh, outlets=[])
    net = res.network
    assert len(net.conduits) >= 1                          # original pipe survives
    assert len(net.outfalls) >= 1                          # component drains somewhere
    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
    assert all(n and str(n).strip() for n in names)
    assert res.diagnostics["n_dangling_nodes"] == 1


def test_accepts_featurecollection_dict(kitchener_inputs):
    """A FeatureCollection dict normalizes the same as a plain list of features."""
    res = build_kitchener_network(
        pipes={"type": "FeatureCollection", "features": kitchener_inputs["pipes"]},
        manholes={"type": "FeatureCollection", "features": kitchener_inputs["manholes"]},
        outlets=kitchener_inputs["outlets"],
    )
    assert len(res.network.conduits) > 0


def test_crs_is_utm17n():
    assert KITCHENER_CRS == "EPSG:32617"
