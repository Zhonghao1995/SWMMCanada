"""Tests for the District of North Vancouver -> SWMM NetworkIn adapter (download-and-cache
SHP dumps), on Lynn Valley fixtures (2289 storm mains, clipped 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.northvandistrict import (
    _elev,
    build_northvandistrict_network,
    fetch_northvandistrict_land,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "northvandistrict"


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_northvandistrict_network({"mains": _load("mains")})


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


def test_string_elevations_and_minus99():
    assert _elev("-99") is None and _elev(None) is None
    assert _elev("124.66") == pytest.approx(124.66)


def test_mountain_relief(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 100.0                       # Lynn Valley climbs hard
    assert all(0 < j.max_depth_m <= 15.0 for j in result.network.junctions)


def test_sanitary_fixture_builds():
    res = build_northvandistrict_network({"mains": _load("sanitary_mains")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


def test_land_is_empty_first_cut():
    land = fetch_northvandistrict_land((-123.04, 49.33, -123.02, 49.35))
    assert land == {"catchbasins": [], "parcels": [], "buildings": []}


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
