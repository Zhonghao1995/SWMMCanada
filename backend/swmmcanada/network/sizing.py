"""First-pass hydraulic pipe sizing for synthesized networks (issue #56, ADR 0001's
"real pipe dimensioning" upgrade).

Rational method per conduit — Q = C·i·A over the accumulated upstream subcatchments —
then Manning full-flow diameter on the conduit's own slope, rounded UP a commercial
ladder, with no downstream shrinkage. The design intensity comes from an injected
``intensity_fn(tc_min) -> mm/h`` (ECCC IDF nearest-station in production, a documented
constant when IDF is unavailable) so this module stays pure and offline-testable.

Everything here is a FIRST-PASS design estimate (documented in ASSUMPTIONS.md) — it makes
the synthesized network hydraulically plausible; it is not a certified sizing.
"""
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Tuple

from swmmcanada.build.models import NetworkIn, SubcatchmentIn

# Commercial ladder (m): standard storm-sewer diameters, 300 mm floor.
COMMERCIAL_DIAMETERS_M = (
    0.30, 0.375, 0.45, 0.525, 0.60, 0.675, 0.75, 0.90,
    1.05, 1.20, 1.35, 1.50, 1.80, 2.10, 2.40, 3.00,
)


@dataclass(frozen=True)
class SizingConfig:
    return_period_yr: int = 5      # Canadian minor-system convention (#56 decision)
    inlet_time_min: float = 10.0   # tc floor: overland/inlet time
    travel_velocity_ms: float = 1.0  # assumed full-flow velocity for travel-time estimates
    c_impervious: float = 0.9      # rational-method runoff coefficients
    c_pervious: float = 0.2
    min_slope: float = 0.001       # slope floor for Manning (flat data defence)


def size_conduits(
    network: NetworkIn,
    subcatchments: List[SubcatchmentIn],
    intensity_fn: Callable[[float], float],
    config: SizingConfig = SizingConfig(),
) -> Tuple[NetworkIn, dict]:
    """A copy of ``network`` with conduit diameters sized by the rational method.

    ``intensity_fn(tc_min)`` returns the design intensity (mm/h) at the given time of
    concentration for the configured return period. Junction inverts give each conduit
    its slope; subcatchment areas/imperviousness give the accumulated C·A.
    """
    inverts = {j.name: j.invert_m for j in network.junctions}
    for o in network.outfalls:
        inverts[o.name] = o.invert_m

    # C·A (ha) delivered at each node by its own subcatchments.
    ca_local: Dict[str, float] = defaultdict(float)
    for s in subcatchments:
        imp = min(max(s.pct_imperv / 100.0, 0.0), 1.0)
        c = config.c_impervious * imp + config.c_pervious * (1.0 - imp)
        ca_local[s.outlet_node] += c * s.area_ha

    # Tree walk from the leaves toward the outfall(s): accumulate C·A and the longest
    # upstream flow path (for tc) through each conduit.
    downstream = {c.from_node: c for c in network.conduits}     # one parent per node (tree)
    indegree: Dict[str, int] = defaultdict(int)
    for c in network.conduits:
        indegree[c.to_node] += 1

    ca_acc: Dict[str, float] = dict(ca_local)                   # node -> accumulated C·A
    path_len: Dict[str, float] = defaultdict(float)             # node -> longest upstream path (m)
    conduit_ca: Dict[str, float] = {}
    conduit_path: Dict[str, float] = {}

    queue = deque(n for n in list(inverts) if indegree[n] == 0)
    seen_edges = 0
    while queue:
        node = queue.popleft()
        edge = downstream.get(node)
        if edge is None:
            continue
        conduit_ca[edge.name] = ca_acc.get(node, 0.0)
        conduit_path[edge.name] = path_len[node] + edge.length_m
        ca_acc[edge.to_node] = ca_acc.get(edge.to_node, 0.0) + ca_acc.get(node, 0.0)
        path_len[edge.to_node] = max(path_len[edge.to_node], path_len[node] + edge.length_m)
        seen_edges += 1
        indegree[edge.to_node] -= 1
        if indegree[edge.to_node] == 0:
            queue.append(edge.to_node)

    sized: Dict[str, float] = {}
    max_hit = 0
    for c in network.conduits:
        ca = conduit_ca.get(c.name, 0.0)
        if ca <= 0:                                             # nothing drains through it
            sized[c.name] = COMMERCIAL_DIAMETERS_M[0]
            continue
        tc_min = config.inlet_time_min + (
            conduit_path.get(c.name, 0.0) / config.travel_velocity_ms) / 60.0
        i_mm_h = float(intensity_fn(tc_min))
        q = ca * i_mm_h / 360.0                                 # Q = C·i·A/360 (ha, mm/h → m³/s)
        drop = inverts.get(c.from_node, 0.0) - inverts.get(c.to_node, 0.0)
        slope = max(drop / c.length_m if c.length_m > 0 else 0.0, config.min_slope)
        d_req = (q * c.roughness_n / (0.3117 * slope ** 0.5)) ** 0.375
        d = next((step for step in COMMERCIAL_DIAMETERS_M if step >= d_req),
                 COMMERCIAL_DIAMETERS_M[-1])
        if d_req > COMMERCIAL_DIAMETERS_M[-1]:
            max_hit += 1
        sized[c.name] = d

    # No downstream shrinkage: walk the same topological order again.
    largest_up: Dict[str, float] = defaultdict(float)
    indegree2: Dict[str, int] = defaultdict(int)
    for c in network.conduits:
        indegree2[c.to_node] += 1
    queue = deque(n for n in list(inverts) if indegree2[n] == 0)
    while queue:
        node = queue.popleft()
        edge = downstream.get(node)
        if edge is None:
            continue
        sized[edge.name] = max(sized[edge.name], largest_up[node])
        largest_up[edge.to_node] = max(largest_up[edge.to_node], sized[edge.name])
        indegree2[edge.to_node] -= 1
        if indegree2[edge.to_node] == 0:
            queue.append(edge.to_node)

    conduits = [replace(c, diameter_m=sized[c.name]) for c in network.conduits]
    diag = {
        "method": "rational+manning",
        "return_period_yr": config.return_period_yr,
        "n_conduits_sized": seen_edges,
        "n_above_ladder": max_hit,
        "max_diameter_m": max(sized.values()) if sized else None,
    }
    return NetworkIn(junctions=network.junctions, outfalls=network.outfalls,
                     conduits=conduits), diag
