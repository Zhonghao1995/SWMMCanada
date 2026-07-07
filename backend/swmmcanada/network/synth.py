"""network (spec 08, ADR 0001): SWMMCanada's OWN drainage-network synthesis.

This is the product's moat — independent of SWMMAnywhere (benchmark only). v1 is a
deliberately crude happy-path on a street graph:

  largest connected component → outlets (water-adjacent local minima when an open-water
  layer is given [ADR 0016], else the single lowest node) → multi-source shortest-path
  FOREST toward the outlets (one parent per node) → inverts propagated outward per tree
  with a minimum slope so flow strictly falls toward its outlet → one conduit per forest
  edge (constant diameter) → one nominal subcatchment per junction.

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
    streets: nx.Graph, *, aoi=None, water=None, config: NetworkConfig = NetworkConfig()
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

    # 3) Terminal junctions (the "sinks"). With an open-water layer (ADR 0016): street
    #    nodes hugging the water, thinned to lowest-first with a minimum spacing — a river
    #    reach discharges at several points, not one. Without water (or none qualify):
    #    the v1 single lowest node, unchanged.
    sinks, outlet_basis = _select_sinks(g, aoi=aoi, water=water, config=config)

    # 4) Multi-source shortest-path FOREST toward the sinks → one parent per node; every
    #    node drains to its nearest (by street distance) outlet.
    paths = nx.multi_source_dijkstra_path(g, set(sinks), weight="length")
    parent: Dict[object, object] = {n: p[-2] for n, p in paths.items() if len(p) >= 2}

    # 5) Inverts: propagate outward from each sink so every upstream node sits higher
    #    than its parent within its own tree.
    inverts: Dict[object, float] = {
        sk: g.nodes[sk]["elev"] - config.outfall_depth_m for sk in sinks}
    children = defaultdict(list)
    for node, par in parent.items():
        children[par].append(node)
    queue = deque(sinks)
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

    # 7) One dedicated single-link outfall per sink (SWMM: an outfall has exactly one link).
    outfalls: List[OutfallIn] = []
    for sk in sinks:
        sk_x, sk_y = g.nodes[sk]["x"], g.nodes[sk]["y"]
        outfall_name = f"OUT_{name[sk]}"
        outfall_inv = inverts[sk] - config.min_slope * config.outfall_link_len_m
        outfalls.append(OutfallIn(outfall_name, invert_m=outfall_inv, x=sk_x + 1e-4, y=sk_y))
        conduits.append(
            ConduitIn(
                f"C_OUT_{name[sk]}", name[sk], outfall_name,
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
            "n_outfalls": len(outfalls),
            "outfalls": [o.name for o in outfalls],
            "outlet_basis": outlet_basis,
            "terminal_junctions": [name[sk] for sk in sinks],
            "dropped_nodes": dropped,
        },
    )


def _select_sinks(g: nx.Graph, *, aoi, water, config: NetworkConfig):
    """Outlet nodes + the honest basis label (ADR 0016 §4). Water-adjacent candidates are
    thinned lowest-elevation-first with a minimum spacing; no water layer (or no node near
    the water) keeps the v1 single-lowest-node behaviour bit-for-bit."""
    if water is not None and aoi is not None:
        from swmmcanada.network.water import nodes_near_water, thin_by_spacing

        node_xy = {n: (g.nodes[n]["x"], g.nodes[n]["y"]) for n in g.nodes}
        near = nodes_near_water(node_xy, water, aoi)
        if near:
            cands = [(n, g.nodes[n]["elev"], node_xy[n]) for n in near]
            chosen = thin_by_spacing(cands, aoi)
            if chosen:
                return chosen, "water-adjacent local minima (ADR 0016)"
    return [min(g.nodes, key=lambda n: g.nodes[n]["elev"])], "lowest node (no water layer)"


def _build_subcatchments(junction_xy, aoi, config: NetworkConfig, cells=None,
                         widths=None, clip_poly=None) -> List[SubcatchmentIn]:
    """Cells → one SubcatchmentIn per junction (missing cell → nominal placeholder; %imperv
    stays a placeholder, derive overwrites). ``cells`` defaults to Voronoi delineation when
    an AOI polygon is given; the DEM delineator (delineate_dem, ADR 0010) passes its own,
    plus optional per-junction ``widths`` (area / DEM flow length) that beat the √area
    default (SWMM width is a time-of-concentration input, not a shape statistic)."""
    if cells is None:
        cells = {}
        if aoi is not None and len(junction_xy) >= 2:
            # ADR 0017: the Voronoi tiling clips to the street service corridor when one is
            # given — the municipal "nearest junction serves its half-blocks" split.
            poly = clip_poly if clip_poly is not None else (
                aoi.geometry if hasattr(aoi, "geometry") else aoi)
            cells = delineate_subcatchments(junction_xy, poly)
    subs: List[SubcatchmentIn] = []
    for jname in junction_xy:
        cell = cells.get(jname)
        if cell is not None and cell.area_m2 > 0:
            area_ha = cell.area_m2 / 10_000.0
            width = (widths or {}).get(jname) or math.sqrt(cell.area_m2)
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
