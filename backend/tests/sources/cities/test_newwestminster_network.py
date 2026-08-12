"""Tests for the New Westminster -> SWMM NetworkIn adapter, on real Uptown fixtures
(163 storm + 276 combined mains, ~900 m clip, recorded 2026-07-28). Locks in: combined
joins storm (ADR 0021), the manhole-INVERT lift for missing pipe ends, RIMELEV depths."""
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SurfaceCatchment
from swmmcanada.sources.cities.newwestminster import (
    build_newwestminster_network,
    fetch_newwestminster_land,
    fetch_newwestminster_sanitary,
    fetch_newwestminster_storm,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "newwestminster"
BBOX = (-122.935, 49.203, -122.923, 49.212)


def _load(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.geojson").read_text())["features"]


def _tag(feats, system):
    for f in feats:
        f["properties"]["_SYSTEM"] = system
    return feats


@pytest.fixture(scope="module")
def result():
    return build_newwestminster_network({
        "mains": _tag(_load("storm_mains"), "Storm") + _tag(_load("combined_mains"), "Combined"),
        "manholes": _load("storm_manholes") + _load("combined_manholes")})


def test_core_invariants(result):
    net = result.network
    assert len(net.junctions) > 0 and len(net.outfalls) > 0 and len(net.conduits) > 0
    names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in names and c.to_node in names for c in net.conduits)
    counted = Counter([j.name for j in net.junctions] + [o.name for o in net.outfalls])
    assert [n for n, c in counted.items() if c > 1] == []
    # JDE_FEATURE_ID repeats across the storm/combined services — conduit names must
    # still come out unique (repeats get an OBJECTID suffix)
    cn = Counter(c.name for c in net.conduits)
    assert [n for n, k in cn.items() if k > 1] == []
    inv = {j.name: j.invert_m for j in net.junctions}
    inv.update({o.name: o.invert_m for o in net.outfalls})
    assert all(inv[c.to_node] + c.outlet_offset_m <= inv[c.from_node] + c.inlet_offset_m + 1e-3 for c in net.conduits)


def test_combined_joins_storm_and_manhole_lift_carries_it(result):
    """The combined system has almost no pipe inverts (12/276 in the fixture) — the
    manhole-INVERT id-join lift must carry hundreds of ends."""
    assert result.diagnostics["n_combined_included"] == 276
    assert result.diagnostics["n_mh_lifted_ends"] > 300
    inv = [j.invert_m for j in result.network.junctions]
    assert max(inv) - min(inv) > 50.0                        # Fraser bank to Massey crest


def test_mh_labels_and_rim_depths(result):
    names = {j.name for j in result.network.junctions} | {o.name for o in result.network.outfalls}
    assert "MH6600" in names
    depths = [j.max_depth_m for j in result.network.junctions]
    assert sum(1 for d in depths if d != 2.0) > 0.3 * len(depths)
    assert all(0 < d <= 15.0 for d in depths)


def test_sanitary_fixture_builds():
    res = build_newwestminster_network(
        {"mains": _load("sanitary_mains"), "manholes": _load("sanitary_manholes")})
    assert len(res.network.junctions) > 0 and len(res.network.conduits) > 0


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params):
        self.calls.append((url, params))
        return {"features": []}


def test_fetch_merges_storm_and_combined():
    client = FakeClient()
    out = fetch_newwestminster_storm(BBOX, client=client)
    urls = [u for u, _ in client.calls]
    assert any("Sewer_Stormwater_Gravity_Main" in u for u in urls)
    assert any("Sewer_Combined_Gravity_Main" in u for u in urls)
    assert any("Sewer_Stormwater_Manhole" in u for u in urls)
    assert any("Sewer_Combined_Manhole" in u for u in urls)
    assert set(out) == {"mains", "manholes"}

    client2 = FakeClient()
    fetch_newwestminster_sanitary(BBOX, client=client2)
    assert any("Sewer_Sanitary_Gravity_Main" in u for u, _ in client2.calls)

    client3 = FakeClient()
    land = fetch_newwestminster_land(BBOX, client=client3)
    assert any("Sewer_Stormwater_Inlets" in u for u, _ in client3.calls)
    assert any("Legal_Parcel" in u for u, _ in client3.calls)
    assert any("Building_Footprints2" in u for u, _ in client3.calls)
    assert set(land) == {"catchbasins", "parcels", "buildings"}


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
