"""Tests for the Strathcona County -> SWMM NetworkIn adapter, on real Sherwood Park
fixtures (325 gravity mains, recorded 2026-07-28). Locks in the inlet-side
discharge-point filter."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.strathcona import (
    build_strathcona_network,
    fetch_strathcona_land,
    fetch_strathcona_sanitary,
    fetch_strathcona_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "strathcona"
BBOX = (-113.330, 53.510, -113.305, 53.528)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_strathcona_network(
        {"mains": _load("mains"), "manholes": _load("manholes"), "outfalls": _load("outfalls")})


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
    assert all(inv[c.to_node] + c.outlet_offset_m <= inv[c.from_node] + c.inlet_offset_m + 1e-3 for c in net.conduits)


def test_inlet_side_discharge_points_dropped(result):
    """11 fixture discharge points sit at the HIGH end of their pipe (inlet-side rows in
    the outfall layer) — accepting them forced 0.06-1.7 m uphill flow."""
    assert result.diagnostics["n_inlet_side_dropped"] == 11


def test_real_inverts_and_rims(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 600 < min(inv) < max(inv) < 800
    depths = [j.max_depth_m for j in result.network.junctions]
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_fixture_builds():
    res = build_strathcona_network(
        {"mains": _load("sanitary_mains"), "manholes": _load("sanitary_manholes")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_strathcona_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "Storm_Gravity_Main" in u)["where"]
    assert "Collector" in where and "Catchbasin Lead" not in where
    assert any("Storm_Manhole" in u for u, _ in client.calls)
    assert any("Storm_Discharge_Point" in u for u, _ in client.calls)
    assert set(out) == {"mains", "manholes", "outfalls"}

    client2 = FakeClient()
    fetch_strathcona_sanitary(BBOX, client=client2)
    assert any("Waste_Water_Gravity_Main" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_strathcona_land(BBOX, client=client3)
    assert any("Storm_Catch_Basin" in u for u, _ in client3.calls)
    assert any("Building_Footprints" in u for u, _ in client3.calls)
    assert land["parcels"] == []


def test_build_model_roundtrip(result, tmp_path):
    from swmmcanada.build.assemble import build_model
    sub = SurfaceCatchment(name="S_TEST", outlet_node=result.network.junctions[0].name,
                         area_ha=1.0, pct_imperv=50.0, width_m=100.0, pct_slope=1.0)
    rain = RainfallSeries(
        timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1), datetime(2022, 6, 1, 2)],
        precip_mm=[0.0, 5.0, 2.0])
    res = build_model(network=result.network, subcatchments=[sub], rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2)))
    assert res.inp_path.exists()
    from swmm_api import read_inp_file
    read_inp_file(str(res.inp_path))
