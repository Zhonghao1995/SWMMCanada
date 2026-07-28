"""Tests for the Barrie -> SWMM NetworkIn adapter.

Run against REAL downtown-Barrie fixtures (295 active piped storm linears, ~1 km clip,
recorded 2026-07-28 with the adapter's layer/where parameters) in tests/fixtures/barrie/.
Barrie locks in: per-end inverts, FROM/TO_ID labels, device TOPELEV rims, TYPE-family
outfall candidates, and real non-circular sections (CLOSED_RECT/ARCH via #130).
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.barrie import _roughness, build_barrie_network

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "barrie"


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def storm_inputs():
    return {"mains": _load("mains"), "devices": _load("devices")}


@pytest.fixture(scope="module")
def result(storm_inputs):
    return build_barrie_network(storm_inputs)


# --- core build -----------------------------------------------------------------

def test_builds_network_with_nodes_and_links(result):
    net = result.network
    assert len(net.junctions) > 0 and len(net.outfalls) > 0 and len(net.conduits) > 0


def test_every_conduit_endpoint_resolves_to_a_node(result):
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    for c in result.network.conduits:
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


# --- the Barrie specifics ---------------------------------------------------------

def test_real_per_end_inverts_carry_the_vertical(result):
    """249/295 fixture linears publish INV_UP/DN_ELV — downtown Barrie slopes from ~257 m
    down to Kempenfelt Bay at ~217 m."""
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 20.0
    assert result.diagnostics["n_inverts_gapfilled"] < 0.3 * result.diagnostics["n_junctions"]


def test_asset_ids_label_the_nodes(result):
    """FROM/TO_ID become node names; ambiguous ids drop to generated names."""
    labelled = [j.name for j in result.network.junctions if len(j.name) >= 9 and j.name.isdigit()]
    assert len(labelled) > 0.5 * len(result.network.junctions)
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "100102311" in names                                  # pinned fixture id


def test_known_main_keeps_its_published_inverts(result):
    """101302358: LOCAL 375 mm CONCRETE, INV 224.65 -> 223.45 (recorded fixture values)."""
    c = next(c for c in result.network.conduits if c.name == "101302358")
    inv = {j.name: j.invert_m for j in result.network.junctions}
    inv.update({o.name: o.invert_m for o in result.network.outfalls})
    assert inv[c.from_node] == pytest.approx(224.65, abs=0.51)
    assert inv[c.to_node] <= 223.45 + 1e-9
    assert c.diameter_m == pytest.approx(0.375)
    assert c.roughness_n == pytest.approx(0.013)                 # CONCRETE -> CONC


def test_noncircular_sections_survive(result):
    """36 CLOSED_RECT fixture linears must map to RECT_CLOSED with real dims (#130)."""
    rect = [c for c in result.network.conduits if c.shape == "RECT_CLOSED"]
    assert len(rect) >= 10
    box = next(c for c in result.network.conduits if c.name == "101300057")
    assert box.shape == "RECT_CLOSED"
    assert box.height_m == pytest.approx(1.2) and box.width_m == pytest.approx(2.4)
    assert result.diagnostics["n_noncircular"] >= 10


def test_device_rims_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_outfall_family_devices_become_outfall_candidates(result):
    """OUTFALL/OUTLET-family devices feed candidates; HEADWALL is deliberately excluded
    (a culvert's single-link INLET headwall accepted as an outfall forces flow uphill —
    the fixture really contains one)."""
    assert result.diagnostics["n_outfall_candidates"] >= 1
    assert result.diagnostics["n_outfalls"] > 0


def test_sanitary_fixture_builds_with_same_schema():
    res = build_barrie_network(
        {"mains": _load("sanitary_mains"), "devices": _load("sanitary_devices")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0 and len(net.outfalls) >= 1
    inv = [j.invert_m for j in net.junctions]
    assert max(inv) - min(inv) > 5.0
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in names and c.to_node in names for c in net.conduits)


def test_material_aliases_resolve():
    assert _roughness("CONCRETE", 0.999) == pytest.approx(0.013)
    assert _roughness("CORRUGATED STEEL", 0.999) == pytest.approx(0.024)
    assert _roughness("PVC", 0.999) == pytest.approx(0.010)
    assert _roughness("something odd", 0.999) == pytest.approx(0.999)


def test_diagnostics_counts_match_network(result):
    d = result.diagnostics
    assert d["city"] == "barrie"
    assert d["n_junctions"] == len(result.network.junctions)
    assert d["n_outfalls"] == len(result.network.outfalls)
    assert d["n_conduits"] == len(result.network.conduits)
    assert d["n_mains_in"] == 295


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
    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS", "XSECTIONS"):
        assert sec in res.sections_written
    from swmm_api import read_inp_file

    read_inp_file(str(res.inp_path))
