"""Tests for the Esquimalt -> SWMM NetworkIn adapter, on real central fixtures (347 drain
mains, recorded 2026-07-28). Locks in: the compass-wall invert pick by pipe bearing, the
DMH id join, sanitary's on-row elevations."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.esquimalt import (
    build_esquimalt_network,
    fetch_esquimalt_land,
    fetch_esquimalt_sanitary,
    fetch_esquimalt_storm,
    pick_directional_invert,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "esquimalt"
BBOX = (-123.416, 48.428, -123.401, 48.439)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_esquimalt_network(
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
    assert all(inv[c.to_node] + c.outlet_offset_m <= inv[c.from_node] + c.inlet_offset_m + 1e-3 for c in net.conduits)


def test_directional_pick_unit():
    mh = {"NORTH_INVERT": 10.0, "SOUTH_INVERT": 9.0, "EAST_INVERT": 8.0,
          "WEST_INVERT": 7.0, "CENTER_INVERT": 6.0}
    assert pick_directional_invert(mh, 10.0) == pytest.approx(10.0)     # ~north
    assert pick_directional_invert(mh, 92.0) == pytest.approx(8.0)      # ~east
    assert pick_directional_invert(mh, 181.0) == pytest.approx(9.0)     # ~south
    assert pick_directional_invert(mh, 269.0) == pytest.approx(7.0)     # ~west
    assert pick_directional_invert({"CENTER_INVERT": 5.0}, 10.0) == pytest.approx(5.0)
    assert pick_directional_invert({"SOUTH_INVERT": 4.0}, 10.0) == pytest.approx(4.0)  # lowest wall
    assert pick_directional_invert({}, 10.0) is None
    assert pick_directional_invert({"NORTH_INVERT": 0}, 10.0) is None   # 0 = missing


def test_directional_lift_carries_the_vertical(result):
    """Drain mains publish nothing — the compass-wall join must carry hundreds of ends."""
    assert result.diagnostics["n_directional_invert_ends"] > 300
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 20.0


def test_rim_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.2 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_rows_carry_their_own_elevations():
    res = build_esquimalt_network({"mains": _load("sanitary_mains")})
    inv = [j.invert_m for j in res.network.junctions]
    assert len(inv) > 0 and max(inv) - min(inv) > 20.0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing():
    client = FakeClient()
    out = fetch_esquimalt_storm(BBOX, client=client)
    urls = [u for u, _ in client.calls]
    assert any("Drain/MapServer/4" in u for u in urls)
    assert any("Drain/MapServer/2" in u for u in urls)
    assert any("Outfalls/MapServer/0" in u for u in urls)
    assert set(out) == {"mains", "manholes", "outfalls"}

    client2 = FakeClient()
    fetch_esquimalt_sanitary(BBOX, client=client2)
    assert any("Sewer/MapServer/5" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_esquimalt_land(BBOX, client=client3)
    assert any("Drain/MapServer/0" in u for u, _ in client3.calls)
    assert any("Cadastre/MapServer/0" in u for u, _ in client3.calls)
    assert any("Buildings_EOC/MapServer/0" in u for u, _ in client3.calls)
    assert set(land) == {"catchbasins", "parcels", "buildings"}


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
