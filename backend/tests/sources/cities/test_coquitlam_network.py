"""Tests for the Coquitlam -> SWMM NetworkIn adapter.

Run against REAL Town Centre fixtures (137 operating storm mains, ~900 m clip, recorded
2026-07-28 through the adapter's layer/where parameters) in tests/fixtures/coquitlam/.
Coquitlam is the wave-2 template city: per-end inverts on the mains (95% city-wide),
termination ids as node labels, manhole rims for max depths, a dedicated outfall layer.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.coquitlam import _roughness, build_coquitlam_network

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "coquitlam"


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def storm_inputs():
    return {"mains": _load("mains"), "manholes": _load("manholes"),
            "outfalls": _load("outfalls")}


@pytest.fixture(scope="module")
def result(storm_inputs):
    return build_coquitlam_network(storm_inputs)


# --- core build -----------------------------------------------------------------

def test_builds_network_with_nodes_and_links(result):
    net = result.network
    assert len(net.junctions) > 0
    assert len(net.outfalls) > 0
    assert len(net.conduits) > 0


def test_every_conduit_endpoint_resolves_to_a_node(result):
    net = result.network
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    for c in net.conduits:
        assert c.from_node in names and c.to_node in names, c.name


def test_no_duplicate_node_names(result):
    names = [j.name for j in result.network.junctions] + [o.name for o in result.network.outfalls]
    assert [n for n, c in Counter(names).items() if c > 1] == []


def test_every_outfall_has_exactly_one_incident_link(result):
    incident = Counter()
    for c in result.network.conduits:
        incident[c.from_node] += 1
        incident[c.to_node] += 1
    for o in result.network.outfalls:
        assert incident[o.name] == 1


def test_inverts_are_monotonic_on_every_conduit(result):
    inv = {j.name: j.invert_m for j in result.network.junctions}
    inv.update({o.name: o.invert_m for o in result.network.outfalls})
    for c in result.network.conduits:
        assert inv[c.to_node] <= inv[c.from_node] + 1e-9, c.name


# --- the Coquitlam specifics ------------------------------------------------------

def test_real_per_end_inverts_carry_the_vertical(result):
    """126/137 fixture mains publish UP/DN_ELEVATION — node inverts must be real data,
    not the gap-fill fallback, and Town Centre has real relief."""
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 3.0
    assert result.diagnostics["n_inverts_gapfilled"] < 0.25 * result.diagnostics["n_junctions"]


def test_termination_ids_label_the_nodes(result):
    """UP/DN_TERM_ID become node names (STMH.../STPI...), not generated N#; an id the city
    stamped on two far-apart endpoints (STMH19683 sits at two coords ~80 m apart in the
    recorded fixture) is dropped by the shared label safety rather than colliding."""
    labelled = [j.name for j in result.network.junctions if j.name.startswith("ST")]
    assert len(labelled) > 0.5 * len(result.network.junctions)
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "STMH12122" in names                                  # fixture-unique id survives
    assert "STMH19683" not in names                              # ambiguous id dropped
    assert result.diagnostics["n_labels_dropped_nonunique"] > 0


def test_known_main_keeps_its_published_inverts(result):
    """STPI20321: UP 12.84 / DN 12.31, 600 mm Concrete (recorded fixture values)."""
    c = next(c for c in result.network.conduits if c.name == "STPI20321")
    inv = {j.name: j.invert_m for j in result.network.junctions}
    inv.update({o.name: o.invert_m for o in result.network.outfalls})
    assert inv[c.from_node] == pytest.approx(12.84, abs=0.51)   # min-of-ends at the node
    assert inv[c.to_node] <= 12.31 + 1e-9
    assert c.diameter_m == pytest.approx(0.6)
    assert c.roughness_n == pytest.approx(0.013)                # Concrete -> CONC


def test_manhole_rims_drive_max_depths(result):
    """131/131 fixture manholes carry RIM_ELEVATION — most junction max depths must be
    rim-derived (!= the 2.0 default), inside the #157 plausibility band."""
    depths = [j.max_depth_m for j in result.network.junctions]
    non_default = [d for d in depths if d != 2.0]
    assert len(non_default) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)
    assert result.diagnostics["n_rims_in"] > 100


def test_real_diameters_survive(result):
    diam = [c.diameter_m for c in result.network.conduits]
    assert len(set(diam)) >= 5
    assert min(diam) < 0.30 < max(diam)


def test_sanitary_fixture_builds_with_its_own_field_names():
    """Sanitary mains use UP_ELEV/DN_ELEV + DIAMETER and RIM_ELEV manholes — the field
    auto-detection must produce a routable skeleton with real inverts."""
    res = build_coquitlam_network(
        {"mains": _load("sanitary_mains"), "manholes": _load("sanitary_manholes")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0
    assert len(net.outfalls) >= 1                       # per-component sinks
    inv = [j.invert_m for j in net.junctions]
    assert max(inv) - min(inv) > 1.0                    # real UP/DN_ELEV, not flat fallback
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in names and c.to_node in names for c in net.conduits)


def test_material_aliases_resolve():
    assert _roughness("Concrete", 0.999) == pytest.approx(0.013)
    assert _roughness("Reinforced Concrete", 0.999) == pytest.approx(0.013)
    assert _roughness("PVC", 0.999) == pytest.approx(0.010)
    assert _roughness("HDPE", 0.999) == pytest.approx(0.011)
    assert _roughness("Corrugated Metal", 0.999) == pytest.approx(0.024)
    assert _roughness("UNK", 0.999) == pytest.approx(0.999)     # unknown keeps default


def test_diagnostics_counts_match_network(result):
    d = result.diagnostics
    assert d["city"] == "coquitlam"
    assert d["n_junctions"] == len(result.network.junctions)
    assert d["n_outfalls"] == len(result.network.outfalls)
    assert d["n_conduits"] == len(result.network.conduits)
    assert d["n_mains_in"] == 137


# --- build compatibility (the real proof) -----------------------------------------

def test_network_feeds_build_model(result, tmp_path):
    from swmmcanada.build.assemble import BuildResult, build_model

    outlet = result.network.junctions[0].name
    sub = SubcatchmentIn(name="S_TEST", outlet_node=outlet, area_ha=1.0, pct_imperv=50.0,
                         width_m=100.0, pct_slope=1.0)
    rain = RainfallSeries(
        timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1), datetime(2022, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.0])
    config = BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2))

    res = build_model(network=result.network, subcatchments=[sub], rain=rain, config=config)

    assert isinstance(res, BuildResult)
    assert res.inp_path.exists()
    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS"):
        assert sec in res.sections_written
    from swmm_api import read_inp_file

    read_inp_file(str(res.inp_path))
