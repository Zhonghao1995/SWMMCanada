"""Tests for the Sarnia -> SWMM NetworkIn adapter, on real downtown fixtures (429 active
storm sewers, recorded 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.sarnia import (
    _diameter_m,
    _elev,
    build_sarnia_network,
    fetch_sarnia_land,
    fetch_sarnia_sanitary,
    fetch_sarnia_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sarnia"
BBOX = (-82.415, 42.960, -82.390, 42.978)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_sarnia_network({"mains": _load("mains")})


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


def test_invert_band_and_text_diameter():
    assert _elev(108.0) is None and _elev(0) is None          # junk + missing sentinels
    assert _elev(179.166) == pytest.approx(179.166)
    assert _diameter_m("1200") == pytest.approx(1.2)
    assert _diameter_m(None) is None


def test_real_inverts_and_mh_labels(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 150 < min(inv) < max(inv) < 250
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MH2708" in names


def test_sanitary_fixture_builds():
    res = build_sarnia_network({"mains": _load("sanitary_mains")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_sarnia_storm(BBOX, client=client)
    assert next(p for u, p in client.calls if "Storm_Sewers" in u)["where"] == \
        "Lifecycle_Status='Active'"
    assert set(out) == {"mains"}

    client2 = FakeClient()
    fetch_sarnia_sanitary(BBOX, client=client2)
    assert any("Sanitary_Sewers" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_sarnia_land(BBOX, client=client3)
    assert any("Catch_Basins" in u for u, _ in client3.calls)
    assert any("Buildings_Open_Data" in u for u, _ in client3.calls)
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
