"""Terminal outlets for wastewater systems (ADR 0029 Q4).

A combined sewer has two genuine destinations, and a model needs both: dry weather leaves
through an interceptor to the treatment plant, storm weather overflows to a watercourse
through a CSO. Build only the plant route and the overflow discharge is identically zero —
and overflow discharge is the one output a combined system exists to be asked about. Build
only the overflow and the dry-weather flow has nowhere to go at all.

Phase 0 measured what the fleet actually publishes: **no supported city publishes CSO
structures** and two publish interceptors. So in practice almost every build reaches this
module with nothing to attach to, and the synthetic boundary below is what it gets. That
makes honesty the whole design constraint — the boundary is a node we invented, it is
labelled as one, and it never quietly reuses something real.

**A wastewater system is never given a storm outfall.** Ottawa publishes 13 outfalls and not
one of them takes combined flow; borrowing one would fabricate a destination the city does
not have, and would make the model answer questions about it.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Set, Tuple

from swmmcanada.build.models import ConduitIn, NetworkIn, OutfallIn

#: How far below its terminal node a synthetic boundary sits. Enough to keep the last link
#: draining without inventing a drop that would distort the hydraulic grade.
BOUNDARY_DROP_M = 0.5

WASTEWATER_SYSTEMS = ("sanitary", "combined")


def _components(nodes: Set[str], conduits: Sequence[ConduitIn]) -> List[Set[str]]:
    adj: Dict[str, Set[str]] = {n: set() for n in nodes}
    for c in conduits:
        if c.from_node in adj and c.to_node in adj:
            adj[c.from_node].add(c.to_node)
            adj[c.to_node].add(c.from_node)
    seen, out = set(), []
    for start in sorted(adj):
        if start in seen:
            continue
        stack, comp = [start], set()
        seen.add(start)
        while stack:
            n = stack.pop()
            comp.add(n)
            for m in adj[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        out.append(comp)
    return out


def ensure_wastewater_outlet(network: NetworkIn, *, system: str = "sanitary"
                             ) -> Tuple[NetworkIn, Dict]:
    """Give every stranded component of ``system`` a destination of its own.

    Returns ``(network, diagnostics)``. A component that already reaches an outfall **of its
    own system** is left alone; one that does not gets a synthetic interceptor/WWTP boundary
    outfall hung off its lowest node.

    Two disconnected basins are two destinations, not one — a single shared boundary would
    merge flows the ground keeps separate.
    """
    own_nodes = {j.name: j for j in network.junctions if j.system == system}
    own_outfalls = {o.name for o in network.outfalls if o.system == system}
    if not own_nodes:
        return network, {"n_added": 0, "reason": f"no {system} nodes in this model"}

    members = set(own_nodes) | own_outfalls
    relevant = [c for c in network.conduits
                if c.from_node in members and c.to_node in members]

    added_outfalls: List[OutfallIn] = []
    added_links: List[ConduitIn] = []
    for i, comp in enumerate(_components(members, relevant), start=1):
        if comp & own_outfalls:
            continue  # already reaches a destination of its own system
        junctions = [own_nodes[n] for n in comp if n in own_nodes]
        if not junctions:
            continue
        # Lowest invert is the downstream end of a gravity chain; ties break on name so the
        # choice is deterministic across runs and platforms.
        terminal = min(junctions, key=lambda j: (j.invert_m, j.name))
        name = f"{system.upper()}_WWTP_BOUNDARY_{i}"
        added_outfalls.append(OutfallIn(
            name, terminal.invert_m - BOUNDARY_DROP_M, terminal.x, terminal.y,
            system=system))
        added_links.append(ConduitIn(
            f"{name}_LINK", terminal.name, name, BOUNDARY_DROP_M * 10, system=system))

    if not added_outfalls:
        return network, {"n_added": 0,
                         "reason": f"every {system} component already reaches an outfall "
                                   f"of its own system"}

    return (
        NetworkIn(junctions=list(network.junctions),
                  outfalls=list(network.outfalls) + added_outfalls,
                  conduits=list(network.conduits) + added_links),
        {
            "n_added": len(added_outfalls),
            "names": [o.name for o in added_outfalls],
            "provenance": "synthetic",
            "reason": (
                "no published CSO or interceptor asset to attach to, so the dry-weather "
                "destination is represented by a synthetic interceptor / treatment-plant "
                "boundary outfall. It is a modelling boundary, not a real structure, and "
                "carries no overflow behaviour"),
            "drop_m": BOUNDARY_DROP_M,
        },
    )
