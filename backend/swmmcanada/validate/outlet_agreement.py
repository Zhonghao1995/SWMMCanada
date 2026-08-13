"""Official-outlet agreement (ADR 0029 Q2, #129).

Municipal catchment polygons supply **boundaries**. Their outlet field lives in the city's
own id space, and half the fleet infers topology geometrically, so it cannot be joined —
which is why we resolve outlets ourselves. The declaration is not thrown away though: it
becomes the yardstick.

    for each unit we produced:
        trace downstream to the outfall it actually reaches
        find the official polygon covering it, and the outfall that polygon declares
        agree?

The resulting rate sits alongside topology agreement as a standing acceptance number, and
it is the runtime half of the Level 1 decision: a candidate is promoted or demoted on it
(ADR 0030 — Level 1 is two beats and revocable).

**What is deliberately not scored.** Three situations are excluded rather than counted as
disagreement, because folding them in would depress a real measurement with things that are
not routing errors: a unit outside every official polygon (the yardstick does not reach
there), a polygon naming an outfall absent from our extract (an AOI edge effect), and a unit
whose network reaches no outfall at all (a connectivity fault the validator already reports
in its own right).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


def _terminal_outfall(start: str, network) -> Optional[str]:
    """The outfall reached by following conduits downstream from ``start``.

    Follows from_node -> to_node, which is the direction the assembler has already oriented.
    A cycle would loop forever, so visited nodes end the walk — a cycle is a topology fault,
    reported elsewhere, and must not hang a metric.
    """
    outfalls = {o.name for o in network.outfalls}
    if start in outfalls:
        return start
    downstream: Dict[str, List[str]] = {}
    for c in network.conduits:
        downstream.setdefault(c.from_node, []).append(c.to_node)

    seen, stack = {start}, [start]
    while stack:
        node = stack.pop()
        for nxt in downstream.get(node, ()):
            if nxt in outfalls:
                return nxt
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return None


def official_outlet_agreement(
    subcatchments: Sequence, network, official_polygons: Sequence[dict], *,
    outlet_field: str = "OUTLET",
) -> Tuple[Optional[float], Dict]:
    """``(rate, diagnostics)``. ``rate`` is ``None`` when nothing was comparable — an
    absent yardstick must never read as a perfect score."""
    from shapely.geometry import Point, shape

    if not official_polygons:
        return None, {"reason": "the city publishes no official catchment layer",
                      "n_comparable": 0}

    declared = []
    for f in official_polygons:
        geom = (f or {}).get("geometry")
        props = (f or {}).get("properties") or {}
        if not geom or not props.get(outlet_field):
            continue
        try:
            declared.append((shape(geom), str(props[outlet_field])))
        except Exception:  # noqa: BLE001 — an unreadable polygon is not a disagreement
            continue

    known_outfalls = {o.name for o in network.outfalls}
    # Outfalls the assembler invented because the real one lies outside this extract. A unit
    # ending at one has not been routed wrongly — it has not been routed all the way, and
    # comparing it against a declared destination scores the clip, not the model.
    invented = {o.name for o in network.outfalls if getattr(o, "synthesised", False)}
    n_agree = n_outside = n_no_geom = n_unknown = n_no_outfall = n_left = 0
    disagreements: List[Dict] = []

    for sub in subcatchments:
        ring = getattr(sub, "polygon", None)
        if not ring or len(ring) < 4:
            n_no_geom += 1
            continue
        rep = Point(sum(x for x, _ in ring) / len(ring),
                    sum(y for _, y in ring) / len(ring))
        match = next((out for poly, out in declared if poly.contains(rep)), None)
        if match is None:
            n_outside += 1
            continue
        # Order matters: establish what OUR model says before asking about the yardstick.
        # When a network has no outfall at all, both "we reach nothing" and "we do not know
        # that name" are true, and the first is the more actionable finding — it is a fault
        # in the model, not a limit of the comparison.
        ours = _terminal_outfall(sub.outlet_node, network)
        if ours is None:
            n_no_outfall += 1
            continue
        if ours in invented:
            n_left += 1
            continue
        if match not in known_outfalls:
            n_unknown += 1
            continue
        if ours == match:
            n_agree += 1
        else:
            disagreements.append({"unit": sub.name, "ours": ours, "declared": match})

    n_comparable = n_agree + len(disagreements)
    rate = (n_agree / n_comparable) if n_comparable else None
    n_units = len(subcatchments)
    return rate, {
        "n_comparable": n_comparable,
        # A rate computed on a fraction of the model must carry that fraction with it,
        # or a high number on three units reads like a high number on the whole city.
        "pct_of_units_scored": (round(100.0 * n_comparable / n_units, 1) if n_units else 0.0),
        "n_agree": n_agree,
        "n_disagree": len(disagreements),
        "disagreements": disagreements[:20],
        # Excluded, with the reason each was excluded — a bare rate hides how much of the
        # model the yardstick actually covered.
        "n_outside_official": n_outside,
        "n_no_geometry": n_no_geom,
        "n_declared_outlet_unknown": n_unknown,
        "n_reaches_no_outfall": n_no_outfall,
        "n_left_extract": n_left,
        "outlet_field": outlet_field,
        "reason": ("" if n_comparable else
                   "no unit could be compared: the official polygons cover none of them, "
                   "the outfalls they name are outside this extract, or every unit drains "
                   "to a boundary the assembler invented because its real destination lies "
                   "outside the AOI"),
    }
