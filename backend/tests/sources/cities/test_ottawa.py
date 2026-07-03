"""Ottawa storm-sewer -> SWMM NetworkIn, via the shared cities.base assembler.

Ottawa has no node ids, so topology is inferred from pipe polyline endpoints. Run against
the real downtown-Ottawa fixtures in tests/fixtures/ottawa/.
"""
import json
from datetime import date, datetime
from pathlib import Path

from swmmcanada.build.assemble import build_model
from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import RainfallSeries, SubcatchmentIn
from swmmcanada.sources.cities.ottawa import build_ottawa_network

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "ottawa"


def _load(name):
    return json.load(open(FIX / name))["features"]


def test_ottawa_network_from_fixtures(tmp_path):
    res = build_ottawa_network({"pipes": _load("storm_pipes.geojson"), "outfalls": _load("outfalls.geojson")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0 and len(net.outfalls) >= 1

    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
    assert all(n and str(n).strip() for n in names), "no empty node names"
    assert len(names) == len(set(names)), "unique node names"
    node_set = set(names)
    for c in net.conduits:
        assert c.from_node in node_set and c.to_node in node_set
    assert all(c.from_node != c.to_node for c in net.conduits)   # no self-loops

    # build-compatibility: the inferred network is genuinely SWMM-valid
    sub = SubcatchmentIn(name="S1", outlet_node=net.junctions[0].name, area_ha=1.0,
                         pct_imperv=50.0, width_m=100.0, pct_slope=1.0)
    rain = RainfallSeries(timestamps=[datetime(2022, 6, 1), datetime(2022, 6, 2)], precip_mm=[5.0, 0.0])
    out = build_model(network=net, subcatchments=[sub], rain=rain,
                      config=BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1), end=date(2022, 6, 2)))
    assert out.inp_path.exists()
    assert res.diagnostics["city"] == "ottawa"


# --- sanitary tracer (second tagged system, ADR 0011) ------------------------------

def test_sanitary_skeleton_assembles_from_fixture():
    """The recorded Sanitary Pipes fixture (layer 7, IN_SERVICE; same schema as storm) must
    assemble into a routable skeleton via the unchanged builder: junctions/conduits > 0 and
    every endpoint resolves (per-component sinks stand in for the treatment-bound exits)."""
    res = build_ottawa_network({"pipes": _load("sanitary_pipes.geojson")})
    net = res.network
    assert len(net.junctions) > 0 and len(net.conduits) > 0
    assert len(net.outfalls) >= 1                           # per-component sinks exist
    node_names = {j.name for j in net.junctions} | {o.name for o in net.outfalls}
    assert all(c.from_node in node_names and c.to_node in node_names for c in net.conduits)
    assert all(f["properties"]["LIFE_CYCLE_STATUS"] == "IN_SERVICE"
               for f in _load("sanitary_pipes.geojson"))
