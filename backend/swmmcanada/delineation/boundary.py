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
    """Trim each cell to the published basin its seed sits in.

    ``cells`` need only expose ``polygon`` (metric) and ``seed`` ``(x, y)``; the same objects
    come back with ``polygon`` replaced where a clip applied. ``basins`` are dicts with a
    metric ``geometry`` and the ``outlet`` the city declares for it.
    """
    from shapely.geometry import Point

    if not basins:
        return list(cells), {"applied": False, "reason": "no official catchment layer",
                             "n_clipped": 0, "n_outside_official": 0,
                             "n_declined_too_small": 0, "area_removed_m2": 0.0}

    out: List = []
    n_clipped = n_outside = n_declined = 0
    area_removed = 0.0

    for cell in cells:
        poly = getattr(cell, "polygon", None)
        seed = getattr(cell, "seed", None)
        if poly is None or seed is None or poly.is_empty:
            out.append(cell)
            continue
        p = Point(*seed)
        home = next((b for b in basins if b["geometry"].contains(p)), None)
        if home is None:
            n_outside += 1
            out.append(cell)
            continue
        clipped = poly.intersection(home["geometry"])
        if clipped.is_empty or poly.area <= 0:
            n_declined += 1
            out.append(cell)
            continue
        if clipped.area / poly.area < min_retained_frac:
            n_declined += 1
            out.append(cell)
            continue
        if clipped.area >= poly.area - 1e-9:
            out.append(cell)
            continue
        area_removed += poly.area - clipped.area
        n_clipped += 1
        try:
            out.append(_replace(cell, polygon=clipped))
        except TypeError:            # a plain object rather than a dataclass
            cell.polygon = clipped
            out.append(cell)

    return out, {
        "applied": True,
        "n_clipped": n_clipped,
        "n_outside_official": n_outside,
        "n_declined_too_small": n_declined,
        "area_removed_m2": round(area_removed, 1),
        "min_retained_frac": min_retained_frac,
    }
