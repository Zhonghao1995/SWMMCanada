"""Tests for the Moncton -> SWMM NetworkIn adapter (first Atlantic-Canada city), on real
central fixtures (330 STM + 448 COMB mains, recorded 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.moncton import (
    build_moncton_network,
    fetch_moncton_land,
    fetch_moncton_sanitary,
    fetch_moncton_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "moncton"
BBOX = (-64.815, 46.085, -64.795, 46.100)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_moncton_network(
        {"mains": _load("storm_mains") + _load("combined_mains"), "manholes": _load("manholes")})


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


def test_combined_joins_storm(result):
    hist = result.diagnostics["unittype_histogram"]
    assert hist.get("COMB", 0) == 448 and hist.get("STM", 0) == 330
    assert "SANI" not in hist
    assert result.diagnostics["n_combined_included"] == 448


def test_real_inverts_labels_and_rims(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 15.0                        # tidal bank to the ridge
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MH41571" in names
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_fixture_is_sani_only():
    res = build_moncton_network({"mains": _load("sanitary_mains"), "manholes": _load("manholes")})
    assert set(res.diagnostics["unittype_histogram"]) == {"SANI"}
    assert len(res.network.junctions) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_moncton_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "/4/query" in u)["where"]
    assert "'STM'" in where and "'COMB'" in where and "SANI" not in where
    assert any("/3/query" in u for u, _ in client.calls)
    assert set(out) == {"mains", "manholes"}

    client2 = FakeClient()
    fetch_moncton_sanitary(BBOX, client=client2)
    assert next(p for u, p in client2.calls if "/4/query" in u)["where"] == "UNITTYPE='SANI'"

    client3 = FakeClient()
    land = fetch_moncton_land(BBOX, client=client3)
    assert any("/1/query" in u for u, _ in client3.calls)          # inlets
    assert any("Parcels/FeatureServer/0" in u for u, _ in client3.calls)
    assert any("Buildings/FeatureServer/0" in u for u, _ in client3.calls)


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
