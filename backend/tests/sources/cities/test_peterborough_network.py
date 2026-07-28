"""Tests for the Peterborough -> SWMM NetworkIn adapter, on real central fixtures (815
SW gravity mains, ~1 km clip, recorded 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.peterborough import (
    build_peterborough_network,
    fetch_peterborough_land,
    fetch_peterborough_sanitary,
    fetch_peterborough_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "peterborough"
BBOX = (-78.330, 44.297, -78.318, 44.306)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_peterborough_network(
        {"mains": _load("mains"), "manholes": _load("manholes"), "outfalls": _load("outfalls")})


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
    assert 180 < min(inv) < max(inv) < 240                   # central Peterborough AMSL
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MH176765" in names
    assert result.diagnostics["n_inverts_gapfilled"] < 0.25 * result.diagnostics["n_junctions"]


def test_manhole_rims_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.2 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_discharge_points_feed_outfalls(result):
    assert result.diagnostics["n_direct_outfalls"] + result.diagnostics["n_dedicated_outfalls"] > 0
    assert result.diagnostics["n_outfalls"] > 0


def test_sanitary_fixture_builds():
    res = build_peterborough_network(
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
    out = fetch_peterborough_storm(BBOX, client=client)
    assert next(p for u, p in client.calls if "/18/query" in u)["where"] == "WATERTYPE='SW'"
    assert any("/13/query" in u for u, _ in client.calls)
    assert any("/11/query" in u for u, _ in client.calls)
    assert set(out) == {"mains", "manholes", "outfalls"}

    client2 = FakeClient()
    fetch_peterborough_sanitary(BBOX, client=client2)
    assert any("/5/query" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_peterborough_land(BBOX, client=client3)
    assert any("/12/query" in u for u, _ in client3.calls)
    assert land["parcels"] == [] and land["buildings"] == []


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
