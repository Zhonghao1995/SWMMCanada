"""Tests for the Toronto -> SWMM NetworkIn adapter, on real King West fixtures (307
storm+combined gravity mains, ~700 m clip, recorded 2026-07-28 from Toronto Water's
official Ext View services). Locks in: the WATERTYPE split (Storm+Combined vs SAN;
CSO/relief out), MH-id labels, UPELEV/DOWNELEV inverts, RIMELEV depths."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.toronto import (
    build_toronto_network,
    fetch_toronto_land,
    fetch_toronto_sanitary,
    fetch_toronto_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "toronto"
BBOX = (-79.394, 43.647, -79.386, 43.653)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


@pytest.fixture(scope="module")
def result():
    return build_toronto_network(
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


def test_combined_mains_join_the_storm_system(result):
    """ADR 0021: downtown Toronto is heavily combined (194 Combined / 113 Storm in the
    fixture) — Combined counts in and is visible in diagnostics."""
    hist = result.diagnostics["watertype_histogram"]
    assert hist.get("Combined", 0) > 100 and hist.get("Storm", 0) > 50
    assert "SAN" not in hist
    assert result.diagnostics["n_combined_included"] == hist["Combined"]


def test_real_inverts_and_mh_labels(result):
    inv = [j.invert_m for j in result.network.junctions]
    assert 70 < min(inv) < max(inv) < 100                    # King West sits ~74-91 m AMSL
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MH3460913669" in names                           # pinned FROMMH label
    assert result.diagnostics["n_inverts_gapfilled"] < 0.25 * result.diagnostics["n_junctions"]


def test_known_main_keeps_its_published_inverts(result):
    """SL1477613: UP 91.309 / DN 90.139, 600 mm CP, WATERTYPE Storm."""
    c = next(c for c in result.network.conduits if c.name == "SL1477613")
    inv = {j.name: j.invert_m for j in result.network.junctions}
    inv.update({o.name: o.invert_m for o in result.network.outfalls})
    assert inv[c.from_node] == pytest.approx(91.309, abs=0.51)
    assert c.diameter_m == pytest.approx(0.6)
    assert c.roughness_n == pytest.approx(0.013)             # CP -> concrete pipe


def test_manhole_rims_drive_max_depths(result):
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_fixture_is_san_only():
    res = build_toronto_network({"mains": _load("sanitary_mains"), "manholes": _load("manholes")})
    assert set(res.diagnostics["watertype_histogram"]) == {"SAN"}
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_routing_and_watertype_filters():
    client = FakeClient()
    out = fetch_toronto_storm(BBOX, client=client)
    where = next(p for u, p in client.calls if "Gravity_Main" in u)["where"]
    assert "'Storm'" in where and "'Combined'" in where and "CSO" not in where
    assert any("Manhole_Ext_View" in u for u, _ in client.calls)
    assert any("Discharge_Point_Ext_View" in u for u, _ in client.calls)
    assert set(out) == {"mains", "manholes", "outfalls"}

    client2 = FakeClient()
    fetch_toronto_sanitary(BBOX, client=client2)
    assert next(p for u, p in client2.calls if "Gravity_Main" in u)["where"] == "WATERTYPE='SAN'"

    client3 = FakeClient()
    land = fetch_toronto_land(BBOX, client=client3)
    assert any("Inlet_Ext_View" in u for u, _ in client3.calls)
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
