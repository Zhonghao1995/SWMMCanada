"""Tests for the Delta -> SWMM NetworkIn adapter, on real North Delta fixtures (674
drainage mains, recorded 2026-07-28). Locks in: the -99 sentinel with legal negative
inverts, grounds-on-row depths, sanitary field auto-detection."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.delta import (
    _elev,
    build_delta_network,
    fetch_delta_land,
    fetch_delta_sanitary,
    fetch_delta_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "delta"
BBOX = (-122.920, 49.140, -122.900, 49.155)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_delta_network({"mains": _load("mains")})


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


def test_minus99_sentinel_but_negatives_legal():
    assert _elev(-99) is None and _elev(None) is None
    assert _elev(-3.65) == pytest.approx(-3.65)              # genuine sea-level invert
    assert _elev(41.744) == pytest.approx(41.744)


def test_grounds_on_row_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)
    assert result.diagnostics["n_grounds_in"] > 1000


def test_sanitary_field_autodetect():
    res = build_delta_network({"mains": _load("sanitary_mains")})
    inv = [j.invert_m for j in res.network.junctions]
    assert len(inv) > 0 and max(inv) - min(inv) > 10.0       # START/END_INVELEV read


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing():
    client = FakeClient()
    out = fetch_delta_storm(BBOX, client=client)
    assert any("Drainage_Mains" in u for u, _ in client.calls)
    assert set(out) == {"mains"}

    client2 = FakeClient()
    fetch_delta_sanitary(BBOX, client=client2)
    assert any("Sanitary_Gravity_Mains" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_delta_land(BBOX, client=client3)
    assert any("Property_Parcels" in u for u, _ in client3.calls)
    assert land["catchbasins"] == [] and land["buildings"] == []


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
