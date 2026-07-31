"""Tests for the Kingston -> SWMM NetworkIn adapter, on real Cataraqui West fixtures (187
constructed storm pipes, ~800 m clip, recorded 2026-07-28). Locks in: the bimodal invert
sentinel band (real 60-200 m vs <=2 m placeholders), three-family node-id labels, and
DOWNSTREAM_OUTLET_ID endpoints as outfall candidates."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.kingston import (
    _invert,
    build_kingston_network,
    fetch_kingston_land,
    fetch_kingston_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "kingston"
BBOX = (-76.570, 44.255, -76.560, 44.263)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_kingston_network({"mains": _load("mains")})


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


def test_bimodal_sentinel_band():
    """Kingston publishes literal 0/1 m inverts as placeholders against ~90 m terrain."""
    assert _invert(1.0) is None and _invert(0) is None and _invert(2.0) is None
    assert _invert(96.35) == pytest.approx(96.35)
    assert _invert(250.0) is None                            # above the city's relief


def test_real_inverts_inside_band(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 80 < min(inv) < max(inv) < 120                    # Cataraqui West AMSL
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MHS-3251" in names                               # manhole-id label survives


def test_outlet_ids_mark_outfall_candidates(result):
    assert result.diagnostics["n_outfall_candidates"] >= 5   # 9 DN outlet ends in fixture
    assert result.diagnostics["n_outfalls"] > 0


def test_depths_default_without_rims(result):
    """Storm Manhole publishes no elevations — depths stay at the assembler default."""
    assert all(0 < j.max_depth_m <= 15.0 for j in result.network.junctions)


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_kingston_storm(BBOX, client=client)
    assert next(p for u, p in client.calls if "Storm_Pipe" in u)["where"] == \
        "CONSTRUCTION_STATUS='Constructed'"
    assert set(out) == {"mains"}

    client2 = FakeClient()
    land = fetch_kingston_land(BBOX, client=client2)
    assert any("Storm_Inlet" in u for u, _ in client2.calls)
    assert any("Buildings" in u for u, _ in client2.calls)
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
