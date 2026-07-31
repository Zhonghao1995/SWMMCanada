"""Tests for the White Rock -> SWMM NetworkIn adapter, on real central fixtures (616
piped storm lines, ~1 km clip, recorded 2026-07-28). Locks in: rims-on-row, the
Line_Type filters, SmallInteger end-id labels."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.whiterock import (
    build_whiterock_network,
    fetch_whiterock_land,
    fetch_whiterock_sanitary,
    fetch_whiterock_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "whiterock"
BBOX = (-122.815, 49.018, -122.800, 49.028)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_whiterock_network({"mains": _load("mains")})


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


def test_real_inverts_and_labels(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 50.0                        # waterfront to upland
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MH4072" in names


def test_rims_on_row_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.4 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)
    assert result.diagnostics["n_rims_in"] > 600


def test_known_main_keeps_its_published_inverts(result):
    """Storm_Id 5056: US 68.47 / DS 60.72, 450 mm CO."""
    c = next(c for c in result.network.conduits if c.name == "5056")
    inv = {j.name: j.invert_m for j in result.network.junctions}
    inv.update({o.name: o.invert_m for o in result.network.outfalls})
    assert inv[c.from_node] == pytest.approx(68.47, abs=0.51)
    assert c.diameter_m == pytest.approx(0.45)
    assert c.roughness_n == pytest.approx(0.013)             # CO -> concrete


def test_sanitary_fixture_builds():
    res = build_whiterock_network({"mains": _load("sanitary_mains")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_filters():
    client = FakeClient()
    out = fetch_whiterock_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "Storm_Lines" in u)["where"]
    assert "'Pipe'" in where and "Dtch" not in where
    assert set(out) == {"mains"}

    client2 = FakeClient()
    fetch_whiterock_sanitary(BBOX, client=client2)
    assert next(p for u, p in client2.calls if "Sanitary_Lines" in u)["where"] == "Line_Type='Gravity'"

    client3 = FakeClient()
    land = fetch_whiterock_land(BBOX, client=client3)
    assert any("Storm_Manholes" in u for u, _ in client3.calls)   # manholes stand in as seeds
    assert any("/Parcel/MapServer" in u for u, _ in client3.calls)
    assert any("Building_Outlines" in u for u, _ in client3.calls)


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
