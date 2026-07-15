"""Tests for the Vancouver -> SWMM NetworkIn adapter (ADR 0020).

Run against REAL downtown-Vancouver fixtures (137 storm+combined mains, ~600 m clip,
recorded 2026-07-10 through the adapter's own fetch functions) in
tests/fixtures/vancouver/. Vancouver is the first city with ZERO published inverts, so the
rim-anchored vertical (rim - default node depth) gets locked here.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.vancouver import (
    VancouverNetworkConfig,
    VancouverNetworkResult,
    build_vancouver_network,
    material_roughness,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "vancouver"


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def storm_inputs():
    return {"mains": _load("mains"), "manholes": _load("manholes")}


@pytest.fixture(scope="module")
def result(storm_inputs) -> VancouverNetworkResult:
    return build_vancouver_network(storm_inputs)


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
    dupes = [n for n, c in Counter(names).items() if c > 1]
    assert dupes == []


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


# --- the Vancouver specifics (ADR 0020) ------------------------------------------

def test_manhole_facilityids_name_the_nodes(result, storm_inputs):
    """Explicit topology: junction names come from VanMap facilityids, not generated N#."""
    facility_ids = {str((f.get("properties") or {}).get("facilityid"))
                    for f in storm_inputs["manholes"]}
    named = [j.name for j in result.network.junctions if j.name in facility_ids]
    assert len(named) > len(result.network.junctions) * 0.5


def test_combined_mains_join_the_storm_system(result):
    """ADR 0020 §2 (author decision): Combined counts in, and the count is visible."""
    hist = result.diagnostics["effluent_histogram"]
    assert hist.get("Combined", 0) >= 1
    assert "Sanitary" not in hist                       # tracer stays separate
    assert result.diagnostics["n_combined_included"] == hist["Combined"]


def test_rim_anchored_vertical_is_not_flat(result):
    """Zero published inverts, yet the network must carry a real terrain gradient
    (rim - default depth), not the all-equal fallback of an invert-less build."""
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 5.0                    # downtown slopes to False Creek
    assert result.diagnostics["n_rim_anchored_ends"] > 0
    assert "rim minus" in result.diagnostics["vertical_basis"]


def test_real_diameters_survive(result):
    """VanMap diameters (mm) must land as metres — not collapse to the 0.30 default."""
    diam = [c.diameter_m for c in result.network.conduits]
    assert len(set(diam)) >= 5
    assert min(diam) < 0.30 < max(diam)


def test_sanitary_fixture_is_sanitary_only():
    san = build_vancouver_network(
        {"mains": _load("sanitary_mains"), "manholes": _load("sanitary_manholes")})
    hist = san.diagnostics["effluent_histogram"]
    assert set(hist) == {"Sanitary"}


def test_diagnostics_counts_match_network(result):
    d = result.diagnostics
    assert d["n_junctions"] == len(result.network.junctions)
    assert d["n_outfalls"] == len(result.network.outfalls)
    assert d["n_conduits"] == len(result.network.conduits)
    assert d["n_mains_in"] == sum(d["effluent_histogram"].values())


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


# --- unit tests --------------------------------------------------------------------

def test_material_roughness_full_words():
    cfg = VancouverNetworkConfig()
    assert material_roughness("Vitrified Clay", cfg) == 0.013
    assert material_roughness("PVC", cfg) == 0.010
    assert material_roughness("Corrugated Metal", cfg) == 0.024
    assert material_roughness("Unobtainium", cfg) == cfg.default_roughness
