"""Tests for the City of Victoria storm-drain -> SWMM NetworkIn adapter.

Run against the REAL downtown-Victoria fixtures (50 STM mains) checked into
tests/fixtures/victoria/. The build-compatibility test proves the resulting network
is genuinely SWMM-valid by round-tripping it through build_model.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import NetworkIn, RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.victoria import (
    VictoriaNetworkConfig,
    VictoriaNetworkResult,
    build_victoria_network,
    material_roughness,
    resolve_endpoints,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "victoria"


def _load(name: str) -> list:
    data = json.loads((FIXTURES / f"{name}.geojson").read_text())
    return data["features"] if isinstance(data, dict) else data


@pytest.fixture(scope="module")
def victoria_inputs():
    return {
        "mains": _load("mains"),
        "manholes": _load("manholes"),
        "fittings": _load("fittings"),
        "outfalls": _load("outfalls"),
    }


@pytest.fixture(scope="module")
def result(victoria_inputs) -> VictoriaNetworkResult:
    return build_victoria_network(**victoria_inputs)


# --- core build -----------------------------------------------------------------

def test_builds_network_with_nodes_and_links(result):
    assert isinstance(result, VictoriaNetworkResult)
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


def test_dangling_nodes_were_handled(result):
    """~10% of endpoint refs dangle; assert they were resolved (count > 0)."""
    assert result.diagnostics["n_dangling_nodes"] > 0


def test_invert_gapfill_recorded(result):
    """At least the one main with a None DownstreamInvert is gap-filled."""
    assert result.diagnostics["n_inverts_gapfilled"] >= 1


def test_shape_histogram_recorded(result):
    """Original CrossSectionShape kept in diagnostics only (builder is circular-only)."""
    hist = result.diagnostics["shape_histogram"]
    assert sum(hist.values()) > 0
    assert "CIR" in hist


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
    cfg = VictoriaNetworkConfig()
    assert material_roughness("PVC", cfg) == 0.010
    assert material_roughness("VITC", cfg) == 0.013      # vitrified clay
    assert material_roughness("vt", cfg) == 0.013
    assert material_roughness("RC", cfg) == 0.013        # concrete
    assert material_roughness("CONC", cfg) == 0.013
    assert material_roughness("AC", cfg) == 0.011        # asbestos cement
    assert material_roughness("PE", cfg) == 0.011
    assert material_roughness("HDPE", cfg) == 0.011
    assert material_roughness("CMP", cfg) == 0.024       # corrugated metal
    assert material_roughness("DI", cfg) == 0.013        # iron
    assert material_roughness("BRK", cfg) == 0.015       # brick
    assert material_roughness("STL", cfg) == 0.012
    # unknown / missing -> default
    assert material_roughness("UNOBTANIUM", cfg) == cfg.default_roughness
    assert material_roughness(None, cfg) == cfg.default_roughness


def _pt(lon, lat):
    return {"type": "Point", "coordinates": [lon, lat]}


def test_resolve_endpoints_dangling_fallback():
    """One endpoint known, the other dangling -> dangling end snaps to the far
    polyline vertex; both-dangling -> coords[0]=upstream, coords[-1]=downstream."""
    coords = {"DMH1": (-123.0, 48.0)}  # only the upstream node is in the point layers

    # main 1: upstream known (DMH1 sits on coords[0]), downstream dangling
    line = [[-123.0, 48.0], [-123.001, 48.001]]
    up, dn, n_dangling = resolve_endpoints("DMH1", "DFG_X", line, coords, snap_tol=1e-6)
    assert up == (-123.0, 48.0)
    assert dn == (-123.001, 48.001)      # taken from the far polyline end
    assert n_dangling == 1

    # main 2: both endpoints dangling -> positional fallback
    line2 = [[-123.5, 48.5], [-123.4, 48.4]]
    up2, dn2, n2 = resolve_endpoints("DFG_A", "DFG_B", line2, {}, snap_tol=1e-6)
    assert up2 == (-123.5, 48.5)
    assert dn2 == (-123.4, 48.4)
    assert n2 == 2


def test_accepts_featurecollection_dict(victoria_inputs):
    """A FeatureCollection dict normalizes the same as a plain list of features."""
    fc = {"type": "FeatureCollection", "features": victoria_inputs["mains"]}
    res = build_victoria_network(
        mains=fc,
        manholes={"type": "FeatureCollection", "features": victoria_inputs["manholes"]},
        fittings=victoria_inputs["fittings"],
        outfalls=victoria_inputs["outfalls"],
    )
    assert len(res.network.conduits) > 0


def _main(aid, up, dn, line, **extra):
    props = {"AssetID": aid, "UpstreamNodeID": up, "DownstreamNodeID": dn,
             "Diameter": 300, "Length_2D": 50, "Material": "PVC", "CrossSectionShape": "CIR"}
    props.update(extra)
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": line}}


def _point(aid, xy, elev=None):
    props = {"AssetID": aid}
    if elev is not None:
        props["Elevation"] = elev
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": list(xy)}}


def test_blank_node_ids_never_produce_empty_names():
    """Real Victoria mains sometimes have a BLANK ("") NodeID (not None). These must not
    create a node named "" or an empty conduit endpoint — that caused SWMM ERROR 203/209.
    Chain: DMH1 -(blank node)- DOF1; the two blank-id ends at the same point must share
    one synthesized node, and both pipes must survive."""
    a, b, c = (-123.370, 48.420), (-123.369, 48.4205), (-123.368, 48.421)
    mains = [
        _main("DGM1", "DMH1", "", [list(a), list(b)], UpstreamInvert=10.0, DownstreamInvert=9.0),
        _main("DGM2", "", "DOF1", [list(b), list(c)], UpstreamInvert=9.0, DownstreamInvert=8.0),
    ]
    res = build_victoria_network(
        mains=mains, manholes=[_point("DMH1", a, 12.0)], fittings=[], outfalls=[_point("DOF1", c, 2.0)]
    )
    net = res.network
    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
    assert all(n and n.strip() for n in names), f"empty node name in {names}"
    for cd in net.conduits:
        assert cd.from_node.strip() and cd.to_node.strip(), f"empty endpoint in conduit {cd.name}"
    assert len(net.conduits) == 2          # both blank-id pipes survive
    assert len(net.junctions) == 2         # DMH1 + one shared synthesized node
    assert any(o.name == "DOF1" for o in net.outfalls)


# --- sanitary tracer (second tagged system, ADR 0011) ------------------------------

def test_sanitary_skeleton_assembles_from_fixture():
    """The recorded Sewer Gravity Mains fixture (WaterType SEW, LifecycleStatus ACT) must
    assemble into a routable skeleton with NO node layers: every endpoint takes the
    polyline-vertex fallback, junctions/conduits > 0, every endpoint resolves, and
    per-component sinks stand in for the treatment-bound exits."""
    res = build_victoria_network(_load("sanitary_mains"), [], [], [])
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0
    assert len(net.outfalls) >= 1                           # per-component sinks exist
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in node_names and c.to_node in node_names for c in net.conduits)
    assert all(f["properties"]["WaterType"] == "SEW" for f in _load("sanitary_mains"))


def test_sanitary_nodes_resolve_via_the_smh_join():
    """Audit 2026-07-14: the sewer AssetID join WORKS (the old 'different id scheme' note
    was wrong). With the recorded sewer node fixtures, sanitary junctions must be named by
    SMH/SFG AssetIDs and carry real manhole Elevations instead of the all-fallback build."""
    res = build_victoria_network(
        _load("sanitary_mains"), _load("sanitary_manholes"),
        _load("sanitary_fittings"), _load("sanitary_outfalls"))
    net = res.network
    named = [j for j in net.junctions if j.name.startswith(("SMH", "SFG"))]
    assert len(named) > len(net.junctions) * 0.8
    # real rims rode the join -> most max depths differ from the 2.0 m default
    non_default = [j for j in named if j.max_depth_m != 2.0]
    assert len(non_default) > len(named) * 0.5
