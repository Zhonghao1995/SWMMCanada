"""TDD for network.synthesise_network (spec 08 §6): invariants on a tiny street graph,
plus the network→build composition (the tracer-bullet link)."""
from datetime import date, datetime

import networkx as nx

from swmmcanada.build import BuildConfig, RainfallSeries, build_model
from swmmcanada.network import synthesise_network


def _street_graph():
    """O is lowest (the natural outfall); A branches to B and C. Elevations rise away from O."""
    g = nx.Graph()
    coords = {"O": (0, 0, 90.0), "A": (100, 0, 95.0), "B": (200, 0, 100.0), "C": (100, 100, 98.0)}
    for n, (x, y, e) in coords.items():
        g.add_node(n, x=x, y=y, elev=e)
    g.add_edge("O", "A")
    g.add_edge("A", "B")
    g.add_edge("A", "C")
    return g


def _digraph(net):
    dg = nx.DiGraph()
    for o in net.outfalls:
        dg.add_node(o.name)
    for j in net.junctions:
        dg.add_node(j.name)
    for c in net.conduits:
        dg.add_edge(c.from_node, c.to_node)
    return dg


def test_synthesis_invariants():
    sn = synthesise_network(_street_graph())
    net = sn.network

    assert len(net.outfalls) == 1
    outfall = net.outfalls[0].name
    assert outfall == "OUT_O"  # dedicated outfall hung off the lowest node (O)

    # SWMM requires an outfall to have EXACTLY one connecting link (ERROR 141 otherwise).
    assert sum(1 for c in net.conduits if c.to_node == outfall) == 1
    assert sum(1 for c in net.conduits if c.from_node == outfall) == 0

    dg = _digraph(net)
    assert nx.is_directed_acyclic_graph(dg)
    for j in net.junctions:
        assert nx.has_path(dg, j.name, outfall)  # every junction drains to the outfall
    assert not list(nx.isolates(dg))             # no orphan nodes

    # Flow strictly falls toward the outfall.
    inv = {j.name: j.invert_m for j in net.junctions}
    inv[outfall] = net.outfalls[0].invert_m
    for c in net.conduits:
        assert inv[c.from_node] > inv[c.to_node]

    # One subcatchment per junction; each drains to a real node.
    nodes = set(inv)
    assert len(sn.subcatchments) == len(net.junctions)
    for s in sn.subcatchments:
        assert s.outlet_node in nodes


def test_disconnected_nodes_dropped():
    g = _street_graph()
    g.add_node("Z", x=999, y=999, elev=80.0)  # isolated + lower than O, must NOT become outfall
    sn = synthesise_network(g)
    assert sn.network.outfalls[0].name == "OUT_O"   # outfall hangs off lowest in-component node
    assert sn.diagnostics["dropped_nodes"] == 1
    assert "Z" not in {o.name for o in sn.network.outfalls}


_BOX = {
    "type": "Polygon",
    "coordinates": [
        [[-75.70, 45.41], [-75.68, 45.41], [-75.68, 45.42], [-75.70, 45.42], [-75.70, 45.41]]
    ],
}


def _lonlat_graph():
    g = nx.Graph()
    pts = {
        "O": (-75.695, 45.412, 90.0), "A": (-75.685, 45.412, 95.0),
        "B": (-75.685, 45.418, 100.0), "C": (-75.695, 45.418, 98.0),
    }
    for n, (x, y, e) in pts.items():
        g.add_node(n, x=x, y=y, elev=e)
    g.add_edge("O", "A"); g.add_edge("A", "B"); g.add_edge("B", "C"); g.add_edge("O", "C")
    return g


def test_aoi_gives_real_subcatchment_polygons(tmp_path):
    import math

    from swmmcanada.geo import aoi_from_geojson

    aoi = aoi_from_geojson(_BOX)
    sn = synthesise_network(_lonlat_graph(), aoi=aoi)

    assert all(s.polygon for s in sn.subcatchments)              # real polygons, not nominal
    total_ha = sum(s.area_ha for s in sn.subcatchments)
    assert math.isclose(total_ha, aoi.area_km2 * 100.0, rel_tol=0.05)  # cells partition the AOI

    # build writes a [POLYGONS] section when subcatchments carry polygons.
    rain = RainfallSeries([datetime(2020, 6, 1, h) for h in range(3)], [1.0, 2.0, 0.0])
    res = build_model(
        network=sn.network, subcatchments=sn.subcatchments, rain=rain,
        config=BuildConfig(out_dir=tmp_path, start=date(2020, 6, 1), end=date(2020, 6, 2)),
    )
    assert "POLYGONS" in res.sections_written


def test_network_feeds_build(tmp_path):
    """The headline composition: a synthesised network builds a round-trippable .inp."""
    sn = synthesise_network(_street_graph())
    rain = RainfallSeries([datetime(2020, 6, 1, h) for h in range(3)], [1.0, 2.0, 0.0])
    res = build_model(
        network=sn.network,
        subcatchments=sn.subcatchments,
        rain=rain,
        config=BuildConfig(out_dir=tmp_path, start=date(2020, 6, 1), end=date(2020, 6, 2)),
    )
    assert res.inp_path.exists()  # build's own round-trip validation already passed
    assert "SUBCATCHMENTS" in res.sections_written
