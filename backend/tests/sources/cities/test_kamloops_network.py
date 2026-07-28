"""Tests for the Kamloops -> SWMM NetworkIn adapter, on real Sahali fixtures (1064
gravity mains, recorded 2026-07-28). Locks in the 9999 missing sentinel."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.kamloops import (
    _elev,
    build_kamloops_network,
    fetch_kamloops_land,
    fetch_kamloops_sanitary,
    fetch_kamloops_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "kamloops"
BBOX = (-120.345, 50.665, -120.325, 50.680)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_kamloops_network(
        {"mains": _load("mains"), "manholes": _load("manholes"), "outfalls": _load("outlets")})


def test_core_invariants(result):
    net = result.network
    assert len(net.junctions) > 0 and len(net.outfalls) > 0 and len(net.conduits) > 0
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in names and c.to_node in names for c in net.conduits)
    counted = Counter([j.name for j in net.junctions] + [o.name for o in net.outfalls])
    assert [n for n, c in counted.items() if c > 1] == []
    cn = Counter(c.name for c in net.conduits)
    assert [n for n, k in cn.items() if k > 1] == []
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    assert all(inv[c.to_node] <= inv[c.from_node] + 1e-9 for c in net.conduits)


def test_9999_sentinel():
    assert _elev(9999) is None and _elev(0) is None and _elev(9998.9) is None
    assert _elev(357.17) == pytest.approx(357.17)


def test_real_inverts_and_rims(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 300 < min(inv) < max(inv) < 900
    assert max(inv) - min(inv) > 30.0                        # Sahali slope
    depths = [j.max_depth_m for j in result.network.junctions]
    assert all(0 < d <= 15.0 for d in depths)


def test_outlet_layer_feeds_outfalls(result):
    assert result.diagnostics["n_outfalls"] > 0


def test_sanitary_fixture_builds():
    res = build_kamloops_network(
        {"mains": _load("sanitary_mains"), "manholes": _load("sanitary_manholes")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing():
    client = FakeClient()
    out = fetch_kamloops_storm(BBOX, client=client)
    urls = [u for u, _ in client.calls]
    assert any("OpenDataDrainEmerGeo/MapServer/12" in u for u in urls)
    assert any("OpenDataDrainEmerGeo/MapServer/14" in u for u in urls)
    assert any("OpenDataDrainEmerGeo/MapServer/16" in u for u in urls)
    assert set(out) == {"mains", "manholes", "outfalls"}

    client2 = FakeClient()
    fetch_kamloops_sanitary(BBOX, client=client2)
    assert any("OpenDataSanitaryTel/MapServer/12" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_kamloops_land(BBOX, client=client3)
    assert any("OpenDataDrainEmerGeo/MapServer/2" in u for u, _ in client3.calls)
    assert any("OpenDataPlanimetric/MapServer/39" in u for u, _ in client3.calls)
    assert land["parcels"] == []


def test_build_model_roundtrip(result, tmp_path):
    from swmmcanada.build.assemble import build_model
    sub = SubcatchmentIn(name="S_TEST", outlet_node=result.network.junctions[0].name,
                         area_ha=1.0, pct_imperv=50.0, width_m=100.0, pct_slope=1.0)
    rain = RainfallSeries(
        timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1), datetime(2022, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.0])
    res = build_model(network=result.network, subcatchments=[sub], rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2)))
    assert res.inp_path.exists()
    from swmm_api import read_inp_file
    read_inp_file(str(res.inp_path))
