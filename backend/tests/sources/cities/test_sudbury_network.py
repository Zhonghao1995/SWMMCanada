"""Tests for the Greater Sudbury -> SWMM NetworkIn adapter, on real downtown fixtures
(1584 gravity mains, recorded 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.sudbury import (
    _elev,
    build_sudbury_network,
    fetch_sudbury_land,
    fetch_sudbury_sanitary,
    fetch_sudbury_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sudbury"
BBOX = (-81.005, 46.485, -80.985, 46.500)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_sudbury_network(
        {"mains": _load("mains"), "manholes": _load("manholes"), "outfalls": _load("outfalls")})


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
    # 0.02 m tolerance: the recorded fixture really contains one conduit running 1 cm
    # "uphill" into a discharge point — survey noise at the forced-outfall orientation
    assert all(inv[c.to_node] + c.outlet_offset_m <= inv[c.from_node] + c.inlet_offset_m + 0.02 for c in net.conduits)


def test_band_screens_junk_inverts():
    assert _elev(958.94) is None and _elev(0) is None        # the live 958 m junk row
    assert _elev(264.12) == pytest.approx(264.12)


def test_real_inverts_and_rims(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 200 < min(inv) < max(inv) < 420
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.2 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_discharge_layer_feeds_outfalls(result):
    assert result.diagnostics["n_outfalls"] > 0


def test_sanitary_fixture_builds():
    res = build_sudbury_network(
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
    out = fetch_sudbury_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "Drainage_view/FeatureServer/9" in u)["where"]
    assert "Storm sewer" in where and "Ditch" not in where
    assert any("Drainage_view/FeatureServer/6" in u for u, _ in client.calls)
    assert any("Drainage_view/FeatureServer/4" in u for u, _ in client.calls)
    assert set(out) == {"mains", "manholes", "outfalls"}

    client2 = FakeClient()
    fetch_sudbury_sanitary(BBOX, client=client2)
    assert any("wastewater_open_data/FeatureServer/10" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_sudbury_land(BBOX, client=client3)
    assert any("Drainage_view/FeatureServer/0" in u for u, _ in client3.calls)
    assert any("Address_and_Building_Roofline/FeatureServer/3" in u for u, _ in client3.calls)
    assert any("Land_Use_and_Boundaries_view/FeatureServer/6" in u for u, _ in client3.calls)


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
