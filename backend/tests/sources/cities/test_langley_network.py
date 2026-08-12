"""Tests for the Township of Langley -> SWMM NetworkIn adapter, on real Willoughby
fixtures (479 live pipes, recorded 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.langley import (
    build_langley_network,
    fetch_langley_land,
    fetch_langley_sanitary,
    fetch_langley_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "langley"
BBOX = (-122.660, 49.128, -122.640, 49.143)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_langley_network({"mains": _load("mains"), "manholes": _load("manholes")})


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


def test_real_inverts_and_string_diameters(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 30.0                        # Willoughby slope
    diam = {c.diameter_m for c in result.network.conduits}
    assert 0.25 in {round(d, 2) for d in diam if d}          # "250" parsed to 0.25 m


def test_manhole_rims_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.2 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_fixture_builds():
    res = build_langley_network(
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
    out = fetch_langley_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "Drainage_Pipes" in u)["where"]
    assert "Asbuilt" in where and "Decommissioned" not in where
    assert any("Drainage_Manholes" in u for u, _ in client.calls)
    assert set(out) == {"mains", "manholes"}

    client2 = FakeClient()
    fetch_langley_sanitary(BBOX, client=client2)
    assert any("Sanitary_Pipes" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_langley_land(BBOX, client=client3)
    assert any("Drainage_Sources" in u for u, _ in client3.calls)
    assert any("/Parcels/FeatureServer" in u for u, _ in client3.calls)
    assert land["buildings"] == []


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
