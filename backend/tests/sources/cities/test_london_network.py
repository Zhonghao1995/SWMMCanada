"""Tests for the City of London (Ontario) storm-sewer -> SWMM NetworkIn adapter.

Run against the REAL downtown-London fixtures (44 STM pipes) checked into
tests/fixtures/london/. The build-compatibility test proves the resulting network
is genuinely SWMM-valid by round-tripping it through build_model.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import NetworkIn, RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.london import (
    LONDON_CRS,
    LondonNetworkConfig,
    LondonNetworkResult,
    build_london_network,
    material_roughness,
    resolve_endpoints,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "london"


def _load(name: str) -> list:
    data = json.loads((FIXTURES / f"{name}.geojson").read_text())
    return data["features"] if isinstance(data, dict) else data


@pytest.fixture(scope="module")
def london_inputs():
    return {
        "mains": _load("mains"),
        "manholes": _load("manholes"),
        "other_nodes": _load("other_nodes"),
        "outfalls": _load("outfalls"),
    }


@pytest.fixture(scope="module")
def result(london_inputs) -> LondonNetworkResult:
    return build_london_network(**london_inputs)


# --- core build -----------------------------------------------------------------

def test_module_crs_is_utm_17n():
    assert LONDON_CRS == "EPSG:32617"


def test_builds_network_with_nodes_and_links(result):
    assert isinstance(result, LondonNetworkResult)
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


def test_real_outfalls_become_direct_outfalls(result):
    """London's outfall layer is captured (6 STM outfalls referenced by these pipes);
    each one-link outfall node should be wired as a direct outfall, not a dedicated sink."""
    assert result.diagnostics["n_direct_outfalls"] >= 1


def test_invert_gapfill_recorded(result):
    """The 1-2 pipe-ends with a missing invert (and the outfall PipeInvert=0 nodes) are
    gap-filled from neighbours; assert at least one gap-fill happened."""
    assert result.diagnostics["n_inverts_gapfilled"] >= 1


def test_shape_histogram_recorded(result):
    """Original PipeShape kept in diagnostics only (builder is circular-only). London uses
    'R'/'Round' for circular pipes; both must appear in the histogram total."""
    hist = result.diagnostics["shape_histogram"]
    assert sum(hist.values()) > 0
    assert "R" in hist                       # the dominant circular code in London data


def test_inventory_type_histogram_recorded(result):
    """Endpoint inventory types (MH/OF/CBM/TEE/...) recorded for provenance; MH dominant."""
    hist = result.diagnostics["inventory_type_histogram"]
    assert sum(hist.values()) == 2 * result.diagnostics["n_mains_in"]   # two ends per pipe
    assert hist.get("MH", 0) > 0


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
    """London's real material codes -> Manning's n. VIT/BRCK/ST are London spellings the
    shared table keys differently; the adapter normalises them to the right value."""
    cfg = LondonNetworkConfig()
    assert material_roughness("CONC", cfg) == 0.013       # concrete
    assert material_roughness("PVC", cfg) == 0.010
    assert material_roughness("VIT", cfg) == 0.013        # vitrified clay (London code)
    assert material_roughness("vit", cfg) == 0.013        # case-insensitive
    assert material_roughness("BRCK", cfg) == 0.015       # brick (London code, NOT default)
    assert material_roughness("ST", cfg) == 0.012         # steel (London code, NOT default)
    assert material_roughness("CSP", cfg) == 0.024        # corrugated steel
    assert material_roughness("AC", cfg) == 0.011         # asbestos cement
    # unknown / missing -> default
    assert material_roughness("?", cfg) == cfg.default_roughness
    assert material_roughness("UNOBTANIUM", cfg) == cfg.default_roughness
    assert material_roughness(None, cfg) == cfg.default_roughness


def test_diameter_mm_converted_to_metres():
    """Diameter is published in mm; the conduit diameter must be metres."""
    a, b = (-81.255, 42.984), (-81.254, 42.9845)
    mains = [_main("P1", "M1", "M2", [list(a), list(b)], Diameter=450,
                   UpstreamInvert=10.0, DownstreamInvert=9.0)]
    res = build_london_network(
        mains=mains, manholes=[_point("M1", a), _point("M2", b)], other_nodes=[], outfalls=[])
    p1 = next(c for c in res.network.conduits if c.name == "P1")   # ignore the synthetic outfall link
    assert p1.diameter_m == pytest.approx(0.450)


def _pt(lon, lat):
    return {"type": "Point", "coordinates": [lon, lat]}


def test_resolve_endpoints_explicit_id_topology():
    """Both ids known -> each takes its node-point coordinate (the explicit-id happy path)."""
    coords = {"M1": (-81.255, 42.984), "M2": (-81.254, 42.9845)}
    line = [[-81.255, 42.984], [-81.2545, 42.9842], [-81.254, 42.9845]]
    up, dn, n_dangling = resolve_endpoints("M1", "M2", line, coords, snap_tol=1e-6)
    assert up == (-81.255, 42.984)
    assert dn == (-81.254, 42.9845)
    assert n_dangling == 0


def test_resolve_endpoints_dangling_fallback():
    """One endpoint known, the other dangling -> dangling end snaps to the far polyline
    vertex; both-dangling -> coords[0]=upstream, coords[-1]=downstream."""
    coords = {"M1": (-81.255, 42.984)}  # only the upstream node resolves

    line = [[-81.255, 42.984], [-81.254, 42.985]]
    up, dn, n_dangling = resolve_endpoints("M1", "8G_MISSING", line, coords, snap_tol=1e-6)
    assert up == (-81.255, 42.984)
    assert dn == (-81.254, 42.985)        # taken from the far polyline end
    assert n_dangling == 1

    line2 = [[-81.260, 42.980], [-81.259, 42.981]]
    up2, dn2, n2 = resolve_endpoints("8G_A", "8G_B", line2, {}, snap_tol=1e-6)
    assert up2 == (-81.260, 42.980)
    assert dn2 == (-81.259, 42.981)
    assert n2 == 2


def test_dangling_id_resolves_through_full_build():
    """A pipe with one dangling DownstreamID still builds: the dangling end snaps to the
    pipe's far polyline vertex and is counted in diagnostics."""
    a, b, c = (-81.260, 42.980), (-81.259, 42.9805), (-81.258, 42.981)
    mains = [
        _main("P1", "M1", "M2", [list(a), list(b)], UpstreamInvert=10.0, DownstreamInvert=9.0),
        _main("P2", "M2", "MISSING", [list(b), list(c)], UpstreamInvert=9.0, DownstreamInvert=8.0),
    ]
    res = build_london_network(
        mains=mains, manholes=[_point("M1", a, 12.0), _point("M2", b, 11.0)],
        other_nodes=[], outfalls=[])
    assert res.diagnostics["n_dangling_nodes"] >= 1
    net = res.network
    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
    assert all(n and n.strip() for n in names), f"empty node name in {names}"
    conduit_names = {c.name for c in net.conduits}
    assert {"P1", "P2"} <= conduit_names    # both real pipes survive (plus a synthetic outfall link)


def test_outfall_detected_by_layer_membership_not_prefix():
    """London outfalls have no id prefix; an id is an outfall iff it is in the outfall layer.
    A pipe ending at an outfall-layer node must wire that node as a (direct) outfall."""
    a, b = (-81.260, 42.980), (-81.259, 42.9805)
    mains = [_main("P1", "M1", "OUT1", [list(a), list(b)],
                   UpstreamInvert=10.0, DownstreamInvert=9.0)]
    res = build_london_network(
        mains=mains, manholes=[_point("M1", a, 12.0)], other_nodes=[],
        outfalls=[_point("OUT1", b)])
    net = res.network
    assert any(o.name == "OUT1" for o in net.outfalls), "outfall-layer node not wired as outfall"
    assert res.diagnostics["n_direct_outfalls"] >= 1


def test_accepts_featurecollection_dict(london_inputs):
    """A FeatureCollection dict normalizes the same as a plain list of features."""
    fc = {"type": "FeatureCollection", "features": london_inputs["mains"]}
    res = build_london_network(
        mains=fc,
        manholes={"type": "FeatureCollection", "features": london_inputs["manholes"]},
        other_nodes=london_inputs["other_nodes"],
        outfalls=london_inputs["outfalls"],
    )
    assert len(res.network.conduits) > 0


def _main(key, up, dn, line, **extra):
    props = {"GIS_FeatureKey": key, "UpstreamID": up, "DownstreamID": dn,
             "Diameter": 300, "Length": 50, "Material": "CONC", "PipeShape": "R",
             "FlowType": "STM"}
    props.update(extra)
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": line}}


def _point(key, xy, lid=None):
    props = {"GIS_FeatureKey": key, "FlowType": "STM"}
    if lid is not None:
        props["LidElevation"] = lid
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": list(xy)}}


# --- sanitary tracer (second tagged system, ADR 0011) ------------------------------

def test_sanitary_skeleton_assembles_from_fixture():
    """The recorded FlowType='SAN' + ConstructedStatus='Built' fixture (with its joined
    node layers) must assemble into a routable skeleton: junctions/conduits > 0 and every
    endpoint resolves (per-component sinks stand in for the treatment-bound exits)."""
    res = build_london_network(
        _load("sanitary_mains"), _load("sanitary_manholes"),
        _load("sanitary_other_nodes"), _load("sanitary_outfalls"))
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0
    assert len(net.outfalls) >= 1
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in node_names and c.to_node in node_names for c in net.conduits)
    assert all(f["properties"]["FlowType"] == "SAN" and
               f["properties"]["ConstructedStatus"] == "Built"
               for f in _load("sanitary_mains"))
