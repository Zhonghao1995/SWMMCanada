"""Tests for the Penticton -> SWMM NetworkIn adapter, on real central fixtures (187
in-service gravity pipes, recorded 2026-07-28). Locks in: text-with-units diameter
parsing, trailing-code material parsing, us/ds_feat labels, Outlet-layer outfalls."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.penticton import (
    _diameter_m,
    _roughness,
    build_penticton_network,
    fetch_penticton_land,
    fetch_penticton_sanitary,
    fetch_penticton_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "penticton"
BBOX = (-119.605, 49.480, -119.585, 49.495)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_penticton_network({"mains": _load("mains"), "outfalls": _load("outlets")})


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


def test_text_diameter_and_material_code_parse():
    assert _diameter_m("300 mm") == pytest.approx(0.3)
    assert _diameter_m("1050mm") == pytest.approx(1.05)
    assert _diameter_m(None) is None and _diameter_m("Unknown") is None
    assert _roughness("Concrete (Non-Reinforced) - CP", 0.999) == pytest.approx(0.013)
    assert _roughness("Polyvinyl Chloride - PVC", 0.999) == pytest.approx(0.010)
    assert _roughness("weird", 0.999) == pytest.approx(0.999)


def test_real_inverts_and_feat_labels(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 330 < min(inv) < max(inv) < 400                   # Okanagan bench AMSL
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "SWMH-75" in names
    c = next(c for c in result.network.conduits if c.name == "SWGM-625-75")
    assert c.diameter_m == pytest.approx(0.25)


def test_outlet_layer_feeds_outfalls(result):
    assert result.diagnostics["n_outfall_candidates"] >= 5
    assert result.diagnostics["n_outfalls"] > 0


def test_sanitary_fixture_builds():
    res = build_penticton_network({"mains": _load("sanitary_mains")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_penticton_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "/415/query" in u)["where"]
    assert "In Service" in where and "Gravity Pipe" in where
    assert any("/410/query" in u for u, _ in client.calls)
    assert set(out) == {"mains", "outfalls"}

    client2 = FakeClient()
    fetch_penticton_sanitary(BBOX, client=client2)
    w2 = next(p for u, p in client2.calls if "/316/query" in u)["where"]
    assert "'Main'" in w2 and "'Trunk'" in w2

    client3 = FakeClient()
    land = fetch_penticton_land(BBOX, client=client3)
    assert any("/408/query" in u for u, _ in client3.calls)
    assert land["parcels"] == [] and land["buildings"] == []


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
