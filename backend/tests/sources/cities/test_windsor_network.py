"""Tests for the Windsor -> SWMM NetworkIn adapter (first download-and-cache city), on
fixtures clipped from the live ZIP dump (1514 storm+combined pipes, 2026-07-28)."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.windsor import (
    _elev,
    build_windsor_network,
    fetch_windsor_land,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "windsor"


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_windsor_network({"mains": _load("mains")})


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


def test_combined_joins_storm(result):
    hist = result.diagnostics["sewer_type_histogram"]
    assert hist.get("COMBINED", 0) == 1060 and hist.get("STORM", 0) == 454
    assert "SANITARY" not in hist


def test_band_and_labels(result):
    assert _elev(0) is None and _elev(100.0) is None
    assert _elev(177.204) == pytest.approx(177.204)
    inv = [j.invert_m for j in result.network.junctions]
    assert 150 < min(inv) < max(inv) < 220
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "3C72" in names


def test_sanitary_fixture_is_sanitary_only():
    res = build_windsor_network({"mains": _load("sanitary_mains")})
    assert set(res.diagnostics["sewer_type_histogram"]) == {"SANITARY"}
    assert len(res.network.junctions) > 0


def test_land_is_empty_first_cut():
    land = fetch_windsor_land((-83.04, 42.30, -83.02, 42.32))
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
