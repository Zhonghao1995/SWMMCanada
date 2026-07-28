"""Tests for the Saskatoon -> SWMM NetworkIn adapter, on real downtown fixtures (162 active
storm mains, ~800 m clip, recorded 2026-07-28). Locks in: STATUS/PIPETYPE filters, the
FROMMH/TOMH numeric-id labelling (MH prefix), UPELEV/DOWNELEV inverts, RIMELEV depths."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.saskatoon import (
    build_saskatoon_network,
    fetch_saskatoon_land,
    fetch_saskatoon_sanitary,
    fetch_saskatoon_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "saskatoon"
BBOX = (-106.670, 52.123, -106.660, 52.131)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_saskatoon_network({"mains": _load("mains"), "manholes": _load("manholes")})


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


def test_real_inverts_and_mh_labels(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 470 < min(inv) < max(inv) < 490                  # real downtown Saskatoon AMSL
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MH1784" in names                                # numeric FROMMH labelled with MH prefix
    assert result.diagnostics["n_inverts_gapfilled"] < 0.2 * result.diagnostics["n_junctions"]


def test_known_main_keeps_its_published_inverts(result):
    """FACILITYID 631: UP 480.83 / DN 480.66, 450 mm CP (concrete pipe)."""
    c = next(c for c in result.network.conduits if c.name == "631")
    inv = {j.name: j.invert_m for j in result.network.junctions}
    inv.update({o.name: o.invert_m for o in result.network.outfalls})
    assert inv[c.from_node] == pytest.approx(480.83, abs=0.51)
    assert c.diameter_m == pytest.approx(0.45)


def test_manhole_rims_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_fixture_builds():
    res = build_saskatoon_network(
        {"mains": _load("sanitary_mains"), "manholes": _load("sanitary_manholes")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0 and len(net.outfalls) >= 1


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_saskatoon_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "/5/query" in u)["where"]
    assert "STATUS='A1'" in where and "'Main'" in where and "Catch Basin Lead" not in where
    assert set(out) == {"mains", "manholes"}

    client2 = FakeClient()
    fetch_saskatoon_sanitary(BBOX, client=client2)
    assert any("/1/query" in u for u, _ in client2.calls)
    assert any("/2/query" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_saskatoon_land(BBOX, client=client3)
    assert any("/7/query" in u for u, _ in client3.calls)                       # catchbasins
    assert any("arcgisod/rest/services/OD/LandSurface/MapServer/1" in u
               for u, _ in client3.calls)                                       # official OD parcels
    assert land["buildings"] == []


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
