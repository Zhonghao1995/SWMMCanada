"""Tests for the Whitby -> SWMM NetworkIn adapter, on real central fixtures (731 storm
lines, recorded 2026-07-28). Locks in: sparse-invert gap-fill, prefix-typed node labels,
CB-endpoint seed extraction."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.whitby import (
    build_whitby_network,
    fetch_whitby_land,
    fetch_whitby_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "whitby"
BBOX = (-78.960, 43.870, -78.935, 43.888)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_whitby_network({"mains": _load("mains")})


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


def test_sparse_inverts_gapfill_and_prefix_labels(result):
    """31% invert coverage: the gap-fill must carry the rest, and node ids keep their
    typed prefixes (ST/CB/JX; dots survive: 'CB25-078.2')."""
    inv = [j.invert_m for j in result.network.junctions]
    assert 70 < min(inv) < max(inv) < 120
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "ST14-011" in names
    assert sum(1 for n in names if n.startswith("CB")) > 10


def test_cb_endpoints_become_seeds():
    class FakeClient:
        def __init__(self):
            self.n = 0

        def get_json(self, url, params):
            self.n += 1
            return {"features": _load("mains")} if self.n == 1 else {"features": []}

    land = fetch_whitby_land(BBOX, client=FakeClient())
    assert len(land["catchbasins"]) > 30
    assert all(f["properties"]["NODE_ID"].startswith("CB") for f in land["catchbasins"])
    assert land["parcels"] == [] and land["buildings"] == []


class FakeClient2:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing():
    client = FakeClient2()
    out = fetch_whitby_storm(BBOX, client=client)
    assert any("WhitbyStormLines" in u for u, _ in client.calls)
    assert set(out) == {"mains"}


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
