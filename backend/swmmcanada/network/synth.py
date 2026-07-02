"""network (spec 08, ADR 0001): SWMMCanada's OWN drainage-network synthesis.

This is the product's moat — independent of SWMMAnywhere (benchmark only). v1 is a
deliberately crude happy-path on a street graph:

  largest connected component → outfall = its lowest node → shortest-path tree toward
  the outfall (one parent per node) → inverts propagated outward with a minimum slope so
  flow strictly falls toward the outfall → one conduit per tree edge (constant diameter)
  → one nominal subcatchment per junction.

The OSM fetch (osmnx) is an injectable concern; this core takes a networkx graph with
node attrs (x, y, elev) so it is fully offline-testable. Output reuses the build model
vocabulary so a synthesised network feeds straight into `build` (derive will later sit
between to refine subcatchment parameters).
"""
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

from swmmcanada.build.models import (
    ConduitIn,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    SubcatchmentIn,
)
from swmmcanada.network.errors import NetworkError
from swmmcanada.network.subcatchments import delineate_subcatchments


@dataclass(frozen=True)
class NetworkConfig:
    min_slope: float = 0.005          # imposed minimum pipe slope (m/m)
    diameter_m: float = 0.30          # constant pipe diameter (v1)
    roughness_n: float = 0.013
    outfall_depth_m: float = 1.0      # sink (terminal junction) invert = ground - this
    outfall_link_len_m: float = 10.0  # length of the single sink→outfall link
    min_node_depth_m: float = 1.5     # minimum junction depth
    sub_area_ha: float = 0.5          # nominal subcatchment area (placeholder)
    sub_slope_pct: float = 1.0        # placeholder subcatchment slope
    placeholder_imperv: float = 50.0  # OVERWRITTEN by derive later


@dataclass(frozen=True)
class SynthesisedNetwork:
    network: NetworkIn
    subcatchments: List[SubcatchmentIn]
    diagnostics: dict = field(default_factory=dict)


def synthesise_network(
    streets: nx.Graph, *, aoi=None, config: NetworkConfig = NetworkConfig()
) -> SynthesisedNetwork:
    if streets.number_of_nodes() < 2:
        raise NetworkError("Need at least 2 street nodes to synthesise a network.")

    # 1) Work on the largest connected component (drop disconnected stragglers).
    components = list(nx.connected_components(streets))
    main = max(components, key=len)
    dropped = streets.number_of_nodes() - len(main)
    g = streets.subgraph(main).copy()
    if g.number_of_nodes() < 2:
        raise NetworkError("Largest connected component has < 2 nodes.")

    # 2) Edge lengths (Euclidean from node coords) where missing.
    for u, v, d in g.edges(data=True):
        if "length" not in d or d["length"] is None:
            d["length"] = _dist(g.nodes[u], g.nodes[v])

    # 3) Lowest node = terminal junction (the "sink"). A dedicated outfall hangs off it
    #    with a SINGLE link — SWMM requires an outfall to have exactly one connecting link.
    sink = min(g.nodes, key=lambda n: g.nodes[n]["elev"])

    # 4) Shortest-path tree toward the sink → one parent per node.
    paths = nx.single_source_dijkstra_path(g, sink, weight="length")
    parent: Dict[object, object] = {n: p[-2] for n, p in paths.items() if len(p) >= 2}

    # 5) Inverts: propagate outward from the sink so each upstream node sits higher.
    inverts: Dict[object, float] = {sink: g.nodes[sink]["elev"] - config.outfall_depth_m}
    children = defaultdict(list)
    for node, par in parent.items():
        children[par].append(node)
    queue = deque([sink])
    while queue:
        node = queue.popleft()
        for child in children[node]:
            length = _edge_length(g, child, node)
            inverts[child] = inverts[node] + length * config.min_slope
            queue.append(child)

    # 6) Emit: every street node is a junction (incl. the sink); subcatchments per junction.
    name = {n: str(n) for n in g.nodes}
    junctions: List[JunctionIn] = []
    junction_xy: Dict[str, Tuple[float, float]] = {}
    for n in g.nodes:
        x, y, ground = g.nodes[n]["x"], g.nodes[n]["y"], g.nodes[n]["elev"]
        inv = inverts[n]
        depth = max(config.min_node_depth_m, ground - inv)
        junctions.append(JunctionIn(name[n], invert_m=inv, x=x, y=y, max_depth_m=depth))
        junction_xy[name[n]] = (x, y)

    subs = _build_subcatchments(junction_xy, aoi, config)

    conduits: List[ConduitIn] = []
    for i, (child, par) in enumerate(parent.items(), start=1):
        conduits.append(
            ConduitIn(
                f"C{i}",
                name[child],
                name[par],
                length_m=_edge_length(g, child, par),
                diameter_m=config.diameter_m,
                roughness_n=config.roughness_n,
            )
        )

    # 7) Dedicated single-link outfall just downstream of the sink (lower invert).
    sink_x, sink_y = g.nodes[sink]["x"], g.nodes[sink]["y"]
    outfall_name = f"OUT_{name[sink]}"
    outfall_inv = inverts[sink] - config.min_slope * config.outfall_link_len_m
    outfalls = [OutfallIn(outfall_name, invert_m=outfall_inv, x=sink_x + 1e-4, y=sink_y)]
    conduits.append(
        ConduitIn(
            "C_OUT", name[sink], outfall_name,
            length_m=config.outfall_link_len_m,
            diameter_m=config.diameter_m, roughness_n=config.roughness_n,
        )
    )

    return SynthesisedNetwork(
        network=NetworkIn(junctions=junctions, outfalls=outfalls, conduits=conduits),
        subcatchments=subs,
        diagnostics={
            "n_nodes": g.number_of_nodes(),
            "n_conduits": len(conduits),
            "outfall": outfall_name,
            "terminal_junction": name[sink],
            "dropped_nodes": dropped,
        },
    )


def _build_subcatchments(junction_xy, aoi, config: NetworkConfig, cells=None) -> List[SubcatchmentIn]:
    """Cells → one SubcatchmentIn per junction (missing cell → nominal placeholder; %imperv
    stays a placeholder, derive overwrites). ``cells`` defaults to Voronoi delineation when
    an AOI polygon is given; the DEM delineator (delineate_dem, ADR 0010) passes its own."""
    if cells is None:
        cells = {}
        if aoi is not None and len(junction_xy) >= 2:
            poly = aoi.geometry if hasattr(aoi, "geometry") else aoi
            cells = delineate_subcatchments(junction_xy, poly)
    subs: List[SubcatchmentIn] = []
    for jname in junction_xy:
        cell = cells.get(jname)
        if cell is not None and cell.area_m2 > 0:
            area_ha = cell.area_m2 / 10_000.0
            width = math.sqrt(cell.area_m2)
            polygon = cell.exterior
        else:
            area_ha = config.sub_area_ha
            width = math.sqrt(config.sub_area_ha * 10_000.0)
            polygon = None
        subs.append(
            SubcatchmentIn(
                f"S_{jname}",
                outlet_node=jname,
                area_ha=area_ha,
                pct_imperv=config.placeholder_imperv,
                width_m=width,
                pct_slope=config.sub_slope_pct,
                polygon=polygon,
            )
        )
    return subs


def _dist(a: dict, b: dict) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _edge_length(g: nx.Graph, u, v) -> float:
    length = g.edges[u, v].get("length")
    return length if length else _dist(g.nodes[u], g.nodes[v])
