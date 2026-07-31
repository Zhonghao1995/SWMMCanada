"""Tests for the Burnaby -> SWMM NetworkIn adapter, on real Metrotown-north fixtures (215
live storm mains, ~900 m clip, recorded 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.burnaby import (
    build_burnaby_network,
    fetch_burnaby_land,
    fetch_burnaby_sanitary,
    fetch_burnaby_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "burnaby"
BBOX = (-123.005, 49.222, -122.993, 49.231)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_burnaby_network({"mains": _load("mains")})


def test_core_invariants(result):
    net = result.network
    assert len(net.junctions) > 0 and len(net.outfalls) > 0 and len(net.conduits) > 0
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in names and c.to_node in names for c in net.conduits)
    counted = Counter([j.name for j in net.junctions] + [o.name for o in net.outfalls])
    assert [n for n, c in counted.items() if c > 1] == []
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    assert all(inv[c.to_node] + c.outlet_offset_m <= inv[c.from_node] + c.inlet_offset_m + 1e-3 for c in net.conduits)


def test_real_inverts_and_unitid_labels(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 20.0                       # slopes off Metrotown ridge
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "DM020133" in names
    assert result.diagnostics["n_inverts_gapfilled"] < 0.3 * result.diagnostics["n_junctions"]


def test_depths_default_without_rims(result):
    """Burnaby fittings publish depth only, no rim — depths stay at the default band."""
    assert all(0 < j.max_depth_m <= 15.0 for j in result.network.junctions)


def test_sanitary_fixture_builds():
    res = build_burnaby_network({"mains": _load("sanitary_mains")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_burnaby_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "OpenData2/MapServer/18" in u)["where"]
    assert "SERVSTAT='I'" in where and "IS NULL" in where
    assert set(out) == {"mains"}

    client2 = FakeClient()
    fetch_burnaby_sanitary(BBOX, client=client2)
    assert any("OpenData2/MapServer/10" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_burnaby_land(BBOX, client=client3)
    assert any("OpenData2/MapServer/19" in u for u, _ in client3.calls)
    assert any("OpenData4/MapServer/7" in u for u, _ in client3.calls)
    assert any("OpenData4/MapServer/18" in u for u, _ in client3.calls)
    assert set(land) == {"catchbasins", "parcels", "buildings"}


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
