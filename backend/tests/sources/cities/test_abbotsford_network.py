"""Tests for the Abbotsford -> SWMM NetworkIn adapter, on real central-Abbotsford fixtures
(214 active drainage mains, ~1 km clip, recorded 2026-07-28). Locks in: the 0 AND -1
missing-sentinel handling, coded-domain material decoding, 'N/A' link labels, and the
monolithic-FeatureServer layer routing."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.abbotsford import (
    _invert,
    _roughness,
    build_abbotsford_network,
    fetch_abbotsford_land,
    fetch_abbotsford_sanitary,
    fetch_abbotsford_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "abbotsford"
BBOX = (-122.315, 49.030, -122.303, 49.040)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_abbotsford_network(
        {"mains": _load("mains"), "manholes": _load("manholes"), "outfalls": _load("outlets")})


def test_core_invariants(result):
    net = result.network
    assert len(net.junctions) > 0 and len(net.outfalls) > 0 and len(net.conduits) > 0
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in names and c.to_node in names for c in net.conduits)
    counted = Counter([j.name for j in net.junctions] + [o.name for o in net.outfalls])
    assert [n for n, c in counted.items() if c > 1] == []
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    assert all(inv[c.to_node] <= inv[c.from_node] + 1e-9 for c in net.conduits)


def test_zero_and_minus_one_are_missing_sentinels():
    assert _invert(0) is None and _invert(-1) is None and _invert("0") is None
    assert _invert(66.13) == pytest.approx(66.13)


def test_real_inverts_and_labels(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 10.0                       # real relief (~48 -> ~68 m)
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "1059C8" in names                                # pinned UPLINK label survives
    assert result.diagnostics["n_inverts_gapfilled"] < 0.3 * result.diagnostics["n_junctions"]


def test_known_main_keeps_its_published_inverts(result):
    """LINKID 6C8: UP 66.13 / DN 65.93, 900 mm, MATERIAL code 1 = Concrete."""
    c = next(c for c in result.network.conduits if c.name == "6C8")
    inv = {j.name: j.invert_m for j in result.network.junctions}
    inv.update({o.name: o.invert_m for o in result.network.outfalls})
    assert inv[c.from_node] == pytest.approx(66.13, abs=0.51)
    assert c.diameter_m == pytest.approx(0.9)
    assert c.roughness_n == pytest.approx(0.013)


def test_material_domain_codes_decode():
    assert _roughness(0, 0.999) == pytest.approx(0.010)     # PVC
    assert _roughness(1, 0.999) == pytest.approx(0.013)     # Concrete
    assert _roughness(5, 0.999) == pytest.approx(0.024)     # Corrugated Steel
    assert _roughness(99, 0.999) == pytest.approx(0.999)    # Unknown -> default
    assert _roughness(None, 0.999) == pytest.approx(0.999)


def test_manhole_rims_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_fixture_builds():
    res = build_abbotsford_network(
        {"mains": _load("sanitary_mains"), "manholes": _load("sanitary_manholes")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0 and len(net.outfalls) >= 1
    inv = [j.invert_m for j in net.junctions]
    assert max(inv) - min(inv) > 3.0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_layer_routing_and_filters():
    client = FakeClient()
    out = fetch_abbotsford_storm(BBOX, client=client)
    urls = [u for u, _ in client.calls]
    assert any("/207/query" in u for u in urls) and any("/204/query" in u for u in urls)
    assert any("/198/query" in u for u in urls)
    assert next(p for u, p in client.calls if "/207/" in u)["where"] == "LIFECYCLE_STATUS=0"
    assert set(out) == {"mains", "manholes", "outfalls"}

    client2 = FakeClient()
    fetch_abbotsford_sanitary(BBOX, client=client2)
    assert any("/214/query" in u for u, _ in client2.calls)
    assert any("/212/query" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_abbotsford_land(BBOX, client=client3)
    assert any("/205/query" in u for u, _ in client3.calls)
    assert any("Parcel_Layers_External_Feature/FeatureServer/0/query" in u for u, _ in client3.calls)
    assert land["buildings"] == []                          # none published


def test_diagnostics_and_build_model(result, tmp_path):
    d = result.diagnostics
    assert d["city"] == "abbotsford" and d["n_mains_in"] == 214
    assert d["n_junctions"] == len(result.network.junctions)

    from swmmcanada.build.assemble import BuildResult, build_model
    sub = SubcatchmentIn(name="S_TEST", outlet_node=result.network.junctions[0].name,
                         area_ha=1.0, pct_imperv=50.0, width_m=100.0, pct_slope=1.0)
    rain = RainfallSeries(
        timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1), datetime(2022, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.0])
    res = build_model(network=result.network, subcatchments=[sub], rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2)))
    assert isinstance(res, BuildResult) and res.inp_path.exists()
    from swmm_api import read_inp_file
    read_inp_file(str(res.inp_path))
