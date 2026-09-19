"""Tests for the Waterloo -> SWMM NetworkIn adapter.

Run against REAL Uptown Waterloo fixtures (194 collector mains, ~650 m clip, recorded
2026-09-18 through the adapter's layer/where parameters) in tests/fixtures/waterloo/.
Waterloo publishes per-end inverts behind TWO missing sentinels (0 and 111.111), manhole
rims and structure ids, a dedicated outlet layer, and non-circular sections.
"""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.waterloo import (
    WATERLOO_BOUNDARY, _elev, _roughness, build_waterloo_network,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "waterloo"


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_waterloo_network({"mains": _load("mains"), "manholes": _load("manholes"),
                                   "outfalls": _load("outfalls")})


def _inverts(net):
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    return inv


# --- core build -----------------------------------------------------------------

def test_builds_network_with_nodes_and_links(result):
    net = result.network
    assert len(net.junctions) > 0 and len(net.outfalls) > 0 and len(net.conduits) > 0


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
    inv = _inverts(result.network)
    for c in result.network.conduits:
        assert inv[c.to_node] + c.outlet_offset_m <= inv[c.from_node] + c.inlet_offset_m + 1e-3, c.name


# --- the Waterloo specifics -------------------------------------------------------

def test_both_missing_sentinels_are_screened():
    """0 and 111.111 are the feed's two "not recorded" values (2,926 and 2,734 storm lines
    city-wide); the city's real ground runs ~305-410 m."""
    assert _elev(0) is None and _elev(0.0) is None
    assert _elev(111.111) is None
    assert _elev(None) is None and _elev("") is None
    assert _elev(999.0) is None                      # junk above the highest ground
    assert _elev(328.834) == pytest.approx(328.834)


def test_no_sentinel_reaches_a_node_invert(result):
    """34 fixture pipe ends carry a sentinel. Read as elevations they would put nodes 200+ m
    below their neighbours; every node must sit in Uptown's real ~318-332 m range."""
    assert result.diagnostics["n_inverts_screened"] == 34
    inv = _inverts(result.network)
    assert all(315.0 < v < 335.0 for v in inv.values()), {n: v for n, v in inv.items()
                                                          if not 315.0 < v < 335.0}


def test_real_per_end_inverts_carry_the_vertical(result):
    """Collectors publish both inverts on ~87% of lines city-wide — node inverts must be
    real data, not the gap-fill fallback. (25 of 226 here: the recorded AOI's tier-B share.)"""
    d = result.diagnostics
    assert d["n_inverts_gapfilled"] < 0.15 * d["n_junctions"]
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 5.0


def test_known_main_keeps_its_published_values(result):
    """10221102: UP 328.834 / DN 328.645, 525 mm Concrete, L55L-6 -> L55L-5 (recorded)."""
    c = next(c for c in result.network.conduits if c.name == "10221102")
    assert (c.from_node, c.to_node) == ("L55L-6", "L55L-5")      # line[0] is the upstream end
    inv = _inverts(result.network)
    assert inv[c.from_node] + c.inlet_offset_m == pytest.approx(328.834)
    assert inv[c.to_node] + c.outlet_offset_m == pytest.approx(328.645)
    assert c.diameter_m == pytest.approx(0.525)
    assert c.roughness_n == pytest.approx(0.013)


def test_manhole_ids_label_the_nodes(result):
    """The city's own structure ids (ENTNAME, e.g. L55L-6) name the nodes, not generated N#."""
    labelled = [j.name for j in result.network.junctions if not j.name.startswith("N")]
    assert len(labelled) > 0.4 * len(result.network.junctions)
    assert {"L55L-6", "L55L-5"} <= set(labelled)


def test_manhole_rims_drive_max_depths(result):
    """128/138 fixture manholes carry an in-band TOPOFMH — rim-derived depths, all inside
    the #157 plausibility band (a 111.111 rim read as real would be 200 m BELOW its invert)."""
    depths = [j.max_depth_m for j in result.network.junctions]
    assert len([d for d in depths if d != 2.0]) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)
    assert result.diagnostics["n_rims_in"] == 128


def test_elliptical_section_survives(result):
    """10053878 is published Elliptical 1350 x 850 mm — a real section, not a flattened circle."""
    c = next(c for c in result.network.conduits if c.name == "10053878")
    assert c.shape == "HORIZ_ELLIPSE"
    assert (c.height_m, c.width_m) == (pytest.approx(1.35), pytest.approx(0.85))
    assert result.diagnostics["n_noncircular"] == 5


def test_sanitary_fixture_builds_with_its_own_field_names():
    """Sanitary mains carry DIAMETER (not HEIGHT/WIDTH) and no manholes are published —
    the same builder must still produce a routable skeleton with real inverts."""
    res = build_waterloo_network({"mains": _load("sanitary_mains")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0 and len(net.outfalls) >= 1
    inv = [j.invert_m for j in net.junctions]
    assert max(inv) - min(inv) > 1.0
    assert len({c.diameter_m for c in net.conduits}) >= 3           # real DIAMETER values
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in names and c.to_node in names for c in net.conduits)


def test_material_aliases_resolve():
    assert _roughness("Concrete", 0.999) == pytest.approx(0.013)
    assert _roughness("Polyvinyl Chloride", 0.999) == pytest.approx(0.010)
    assert _roughness("Corrugated Steel Pipe", 0.999) == pytest.approx(0.024)
    assert _roughness("Asbestos Cement", 0.999) == pytest.approx(0.011)
    assert _roughness("Vitified Clay", 0.999) == pytest.approx(0.013)   # misspelt at source
    assert _roughness("Unknown", 0.999) == pytest.approx(0.999)         # unknown keeps default


def test_diagnostics_counts_match_network(result):
    d = result.diagnostics
    assert d["city"] == "waterloo"
    assert d["n_junctions"] == len(result.network.junctions)
    assert d["n_outfalls"] == len(result.network.outfalls)
    assert d["n_conduits"] == len(result.network.conduits)
    assert d["n_mains_in"] == 194


def test_boundary_is_a_closed_ring_around_the_fixture():
    assert WATERLOO_BOUNDARY[0] == WATERLOO_BOUNDARY[-1] and len(WATERLOO_BOUNDARY) > 20
    from swmmcanada.sources.cities.registry import _in_ring
    assert _in_ring(-80.523, 43.4652, WATERLOO_BOUNDARY)              # the fixture's centre
    assert not _in_ring(-80.4925, 43.4516, WATERLOO_BOUNDARY)         # downtown Kitchener


# --- build compatibility (the real proof) -----------------------------------------

def test_network_feeds_build_model(result, tmp_path):
    from swmmcanada.build.assemble import BuildResult, build_model

    outlet = result.network.junctions[0].name
    sub = SurfaceCatchment(name="S_TEST", outlet_node=outlet, area_ha=1.0, pct_imperv=50.0,
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
