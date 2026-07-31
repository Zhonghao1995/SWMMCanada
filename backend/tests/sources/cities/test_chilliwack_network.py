"""Tests for the Chilliwack -> SWMM NetworkIn adapter, on real fixtures (973 storm pipes,
recorded 2026-07-28). Locks in: SYM_TYPE-split rims/seeds from one symbol layer."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.chilliwack import (
    build_chilliwack_network,
    fetch_chilliwack_land,
    fetch_chilliwack_sanitary,
    fetch_chilliwack_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "chilliwack"
BBOX = (-121.970, 49.155, -121.945, 49.172)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_chilliwack_network({"mains": _load("mains"), "symbols": _load("symbols")})


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


def test_valley_floor_inverts_and_symbol_rims(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 0.5 < min(inv) < max(inv) < 20                    # the Fraser valley floor
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.2 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)
    assert result.diagnostics["n_rims_in"] > 500


def test_sanitary_fixture_builds():
    res = build_chilliwack_network({"mains": _load("sanitary_mains")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


def test_seed_extraction_from_symbols():
    class FakeClient:
        def __init__(self):
            self.n = 0

        def get_json(self, url, params):
            self.n += 1
            if "Dynamic_Utility_Feature" in url and self.n <= 2:
                return {"features": _load("symbols")}
            return {"features": []}

    land = fetch_chilliwack_land(BBOX, client=FakeClient())
    assert len(land["catchbasins"]) > 500
    assert all(str(f["properties"].get("SYM_TYPE")).upper() in
               ("CATCHBASIN", "CB/MH", "LAWN BASIN") for f in land["catchbasins"])
    assert land["parcels"] == [] and land["buildings"] == []


class FakeClient2:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing():
    client = FakeClient2()
    out = fetch_chilliwack_storm(BBOX, client=client)
    urls = [u for u, _ in client.calls]
    assert any("Dynamic_Utility/MapServer/8" in u for u in urls)
    assert any("Dynamic_Utility_Feature/MapServer/5" in u for u in urls)
    assert set(out) == {"mains", "symbols"}

    client2 = FakeClient2()
    fetch_chilliwack_sanitary(BBOX, client=client2)
    assert any("Dynamic_Utility/MapServer/4" in u for u, _ in client2.calls)


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
