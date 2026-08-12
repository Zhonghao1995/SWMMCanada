"""Official basins as a hard edge (规划书 §4, ADR 0029 Q2).

Phase 0 measured every published catchment layer in the fleet as macro — one per outfall or
pump station, tens of hectares each. Too coarse to be model units, but they carry a fact we
cannot derive from pipes and terrain: **the city knows which land drains to which outfall.**

So they bound rather than become. A cell may sit inside a basin; it may not straddle two,
because crossing that line routes land to an outfall the city says it does not reach.

Two refusals keep this from doing harm. A cell whose seed falls in no published basin is
left alone — outside the coverage the yardstick does not reach, and cutting to a basin the
seed is not in would invent a routing decision. And a cut that would leave only a sliver is
declined: that means the seed and the basins disagree, and quietly shrinking a cell to
nothing loses land from the model.

The cut half is **handed to a cell that does serve that basin**, never deleted. A boundary
says which node land belongs to; it does not say the land stopped existing. Deleting it cost
6.1 ha on a live downtown — coverage read 100% before this step and 93% after, and the
delineation feeding it was complete. Where no cell can take the piece, or the join would not
survive as a single clean polygon, the clip is declined outright: a cell crossing a line the
city drew is a smaller error than land that no longer rains anywhere.
"""
from __future__ import annotations

from dataclasses import replace as _replace
from typing import Dict, List, Sequence, Tuple

#: A clip that leaves less than this share of a cell is refused. Below it the seed and the
#: published basins are telling different stories, and the honest response is to keep the
#: cell whole and say so, not to shrink it away.
DEFAULT_MIN_RETAINED_FRAC = 0.2


def clip_to_official_basins(
    cells: Sequence, basins: Sequence[Dict], *,
    min_retained_frac: float = DEFAULT_MIN_RETAINED_FRAC,
) -> Tuple[List, Dict]:
    """Trim each cell to the published basin its seed sits in, handing the cut land on.

    ``cells`` need only expose ``polygon`` (metric) and ``seed`` ``(x, y)``; the same objects
    come back with ``polygon`` replaced where a clip applied. ``basins`` are dicts with a
    metric ``geometry`` and the ``outlet`` the city declares for it.
    """
    from shapely.geometry import Point
    from shapely.ops import unary_union

    empty = {"applied": False, "reason": "no official catchment layer", "n_clipped": 0,
             "n_outside_official": 0, "n_declined_too_small": 0,
             "n_declined_nowhere_to_send": 0, "n_declined_unmergeable": 0,
             "area_removed_m2": 0.0, "area_reassigned_m2": 0.0}
    if not basins:
        return list(cells), empty

    homes = []                                          # (cell, its basin or None)
    for cell in cells:
        poly, seed = getattr(cell, "polygon", None), getattr(cell, "seed", None)
        if poly is None or seed is None or poly.is_empty:
            homes.append((cell, None))
            continue
        p = Point(*seed)
        homes.append((cell, next((b for b in basins if b["geometry"].contains(p)), None)))

    geoms = {id(c): getattr(c, "polygon", None) for c, _ in homes}
    servers: Dict[int, List] = {}                       # basin -> cells seeded in it
    for cell, home in homes:
        if home is not None:
            servers.setdefault(id(home), []).append(cell)

    def receiver_for(piece, giver, home):
        """The cell that should take ``piece``: seeded in the piece's basin, and touching it.

        Touching matters — a subcatchment is one polygon downstream, so a receiver merely
        nearby would become a disjoint pair the writers cannot express.
        """
        where = next((b for b in basins
                      if b["geometry"].intersects(piece.representative_point())), None)
        if where is None or where is home:
            return None
        best, best_shared = None, 0.0
        for cand in servers.get(id(where), ()):
            g = geoms.get(id(cand))
            if cand is giver or g is None or not g.intersects(piece):
                continue
            shared = g.intersection(piece).length
            if shared > best_shared:
                best, best_shared = cand, shared
        return best

    n_clipped = n_outside = n_small = n_nowhere = n_unmergeable = 0
    area_removed = area_reassigned = 0.0
    proposals = []                                      # (giver, clipped, [(piece, receiver)])
    givers = set()

    for cell, home in homes:
        poly = geoms.get(id(cell))
        if poly is None or poly.is_empty:
            continue
        if home is None:
            n_outside += 1
            continue
        clipped = poly.intersection(home["geometry"])
        # A clip that shatters the cell into fragments is not a clip we can express: a
        # subcatchment is one polygon downstream. Keep it whole and say so.
        if (clipped.is_empty or poly.area <= 0 or clipped.geom_type != "Polygon"
                or not clipped.is_valid
                or clipped.area / poly.area < min_retained_frac):
            n_small += 1
            continue
        if clipped.area >= poly.area - 1e-9:
            continue
        cut = poly.difference(home["geometry"])
        pieces = [g for g in (cut.geoms if hasattr(cut, "geoms") else [cut])
                  if getattr(g, "area", 0.0) > 1e-9]
        placed = [(g, receiver_for(g, cell, home)) for g in pieces]
        if any(r is None for _g, r in placed):
            n_nowhere += 1                              # nowhere to send it, so do not cut
            continue
        proposals.append((cell, clipped, placed))
        givers.add(id(cell))

    # A giver that is also somebody's receiver would chain: its own shape changes under it
    # while it is being merged into. Decline those rather than reason about the order.
    incoming: Dict[int, List] = {}
    for giver, _clipped, placed in proposals:
        for piece, receiver in placed:
            incoming.setdefault(id(receiver), []).append((piece, giver))

    accepted = []
    for giver, clipped, placed in proposals:
        if any(id(r) in givers for _g, r in placed):
            n_unmergeable += 1
            continue
        accepted.append((giver, clipped, placed))

    taken = {id(g) for g, _c, _p in accepted}
    merged_for: Dict[int, object] = {}
    for cell, _home in homes:
        gained = [(pc, gv) for pc, gv in incoming.get(id(cell), ()) if id(gv) in taken]
        if not gained:
            continue
        base = geoms[id(cell)]
        merged = unary_union([base, *(pc for pc, _gv in gained)])
        if not merged.is_valid or merged.geom_type != "Polygon":
            merged = merged.buffer(0)
        want = base.area + sum(pc.area for pc, _gv in gained)
        if (merged.geom_type == "Polygon" and merged.is_valid
                and merged.area >= want - 1.0 and _survives_reprojection(merged)):
            merged_for[id(cell)] = merged
        else:
            n_unmergeable += 1
            for _pc, gv in gained:                      # the giver keeps what nobody caught
                taken.discard(id(gv))

    for giver, clipped, placed in accepted:
        if id(giver) not in taken:
            continue
        geoms[id(giver)] = clipped
        area_removed += geoms_area_lost(giver, clipped, homes)
        area_reassigned += sum(pc.area for pc, _r in placed)
        n_clipped += 1
    for cid, merged in merged_for.items():
        if any(id(gv) in taken for _pc, gv in incoming.get(cid, ())):
            geoms[cid] = merged

    out: List = []
    for cell, _home in homes:
        poly = geoms.get(id(cell))
        if poly is None or poly is getattr(cell, "polygon", None):
            out.append(cell)
            continue
        try:
            out.append(_replace(cell, polygon=poly))
        except TypeError:                               # a plain object rather than a dataclass
            cell.polygon = poly
            out.append(cell)

    return out, {
        "applied": True,
        "n_clipped": n_clipped,
        "n_outside_official": n_outside,
        "n_declined_too_small": n_small,
        "n_declined_nowhere_to_send": n_nowhere,
        "n_declined_unmergeable": n_unmergeable,
        "area_removed_m2": round(area_removed, 1),
        "area_reassigned_m2": round(area_reassigned, 1),
        "min_retained_frac": min_retained_frac,
    }


#: A join thinner than this is a hairline neck, not a shape. It reads as valid in the metric
#: CRS and self-intersects once the polygon has been through lon/lat, which is how the model
#: actually stores it — so validity is judged in both, as everywhere else in this repo.
MIN_NECK_M = 0.05


def _survives_reprojection(poly) -> bool:
    eroded = poly.buffer(-MIN_NECK_M)
    return (not eroded.is_empty) and eroded.geom_type == "Polygon"


def geoms_area_lost(giver, clipped, homes) -> float:
    original = next((getattr(c, "polygon", None) for c, _h in homes if c is giver), None)
    return 0.0 if original is None else max(0.0, original.area - clipped.area)
