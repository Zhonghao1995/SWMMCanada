"""Urban DEM conditioning: kerbs are walls with gates (规划书 §4, priority 2).

A bare DEM does not know that a 150 mm kerb decides where street runoff goes. At 1 m LiDAR
posting the kerb is often invisible — one pixel of a smooth cross-slope — so D8 sends water
across it into the front garden when in reality it runs along the gutter to the nearest
inlet. Every subcatchment boundary downstream inherits that error.

Conditioning states the three facts the terrain omits:

* a **kerb** is a barrier — raised so flow will not cross it;
* a **kerb drop or inlet** is the one place through it — the barrier is not raised there,
  so D8 finds the gate on its own rather than being told about it;
* a **building** is not crossed at all — raised far higher than any kerb.

This is a surface edit, not a hydrological claim. It encodes assets a city surveyed and
published; where a city publishes none of them the DEM is returned untouched and the caller
falls back to a coarser method (规划书 §4, priority 3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class UrbanConditioningConfig:
    #: Kerb face height. Canadian municipal standard barrier kerb is 150 mm; the value used
    #: here is deliberately larger than any plausible DEM noise at LiDAR posting, because
    #: the point is to make the barrier decisive rather than to model its exact height.
    kerb_height_m: float = 0.30
    #: Buildings are raised well above kerbs so no combination of kerb and slope lets flow
    #: route through one.
    building_height_m: float = 10.0
    #: How wide a gate a kerb drop or inlet opens, in metres. A real depressed kerb is about
    #: 1.5–3 m; the gate must be at least a pixel wide at the DEM's posting to exist at all.
    opening_radius_m: float = 2.0


def _rasterise(geoms, shape, transform, *, all_touched=True):
    from rasterio import features

    if not geoms:
        return np.zeros(shape, dtype=bool)
    return features.geometry_mask(list(geoms), out_shape=shape, transform=transform,
                                  invert=True, all_touched=all_touched)


def condition_urban_dem(
    dem: np.ndarray, transform, *,
    kerbs: Optional[Sequence] = None,
    openings: Optional[Sequence] = None,
    buildings: Optional[Sequence] = None,
    config: UrbanConditioningConfig = UrbanConditioningConfig(),
) -> Tuple[np.ndarray, Dict]:
    """Return ``(conditioned_dem, diagnostics)``.

    Geometries are in the DEM's own CRS. The input array is never mutated: conditioning is
    one option among several and the caller may still need the raw surface.
    """
    kerbs = list(kerbs or [])
    openings = list(openings or [])
    buildings = list(buildings or [])
    if not kerbs and not buildings:
        return dem, {"applied": False, "reason": "no kerb or building geometry published",
                     "n_kerb_cells": 0, "n_building_cells": 0, "n_opening_cells": 0}

    out = np.array(dem, dtype="float64", copy=True)
    shape = out.shape

    kerb_mask = _rasterise(kerbs, shape, transform)
    build_mask = _rasterise(buildings, shape, transform)

    # Gates are punched out of the barrier BEFORE it is raised, so the opening is simply a
    # place the wall was never built — D8 discovers it as the low route instead of being
    # steered through it.
    gate_mask = np.zeros(shape, dtype=bool)
    if openings:
        px = abs(transform.a) or 1.0
        buf = max(config.opening_radius_m, px)
        gate_mask = _rasterise([g.buffer(buf) for g in openings], shape, transform)
        kerb_mask &= ~gate_mask

    finite = np.isfinite(out)
    out[kerb_mask & finite] += config.kerb_height_m
    out[build_mask & finite] += config.building_height_m

    return out, {
        "applied": True,
        "n_kerb_cells": int((kerb_mask & finite).sum()),
        "n_opening_cells": int((gate_mask & finite).sum()),
        "n_building_cells": int((build_mask & finite).sum()),
        "kerb_height_m": config.kerb_height_m,
        "building_height_m": config.building_height_m,
        "opening_radius_m": config.opening_radius_m,
    }


#: How far an inlet may be moved to find the gutter. Beyond about a carriageway width the
#: low point belongs to a different street, and snapping to it hands the inlet a catchment
#: it does not serve.
DEFAULT_SNAP_RADIUS_M = 4.0


def snap_to_local_low(points, dem, transform, *, search_radius_m: float = DEFAULT_SNAP_RADIUS_M,
                      nodata=None):
    """Move each pour point onto the lowest cell within a short search (规划书 §4).

    A published inlet coordinate marks the structure, not the pixel water arrives at. A
    metre or two of survey offset — or a DEM whose gutter sits half a cell away — puts the
    pour point on the kerb or the carriageway crown instead of the low line, and D8 then
    hands that inlet a basin of one pixel while its real catchment drains past it.

    ``points`` maps name to ``(x, y)`` in the DEM's CRS; the same shape comes back. The
    search is deliberately small and the move is reported: a pour point that travelled is a
    fact a reader may want to weigh, not a detail to bury.
    """
    out = dict(points)
    if not points:
        return out, {"n_moved": 0, "n_outside_dem": 0, "max_move_m": 0.0,
                     "search_radius_m": search_radius_m}

    band = np.asarray(dem, dtype="float64")
    if nodata is not None:
        band = np.where(band == float(nodata), np.nan, band)
    h, w = band.shape
    px = abs(transform.a) or 1.0
    reach = max(int(round(search_radius_m / px)), 1)

    n_moved = n_outside = 0
    max_move = 0.0
    inv = ~transform
    for name, (x, y) in points.items():
        col, row = inv * (x, y)
        r0, c0 = int(row), int(col)
        if not (0 <= r0 < h and 0 <= c0 < w):
            n_outside += 1
            continue
        r1, r2 = max(r0 - reach, 0), min(r0 + reach + 1, h)
        c1, c2 = max(c0 - reach, 0), min(c0 + reach + 1, w)
        window = band[r1:r2, c1:c2].copy()
        # The window is square; its diagonal reaches ~1.4x further than its side. Mask the
        # corners so the radius a reader is given actually bounds the move.
        rr_idx = np.arange(r1, r2)[:, None]
        cc_idx = np.arange(c1, c2)[None, :]
        dist = np.hypot((rr_idx - r0) * px, (cc_idx - c0) * px)
        window[dist > search_radius_m] = np.nan
        if np.all(np.isnan(window)):
            continue
        lowest = float(np.nanmin(window))
        here = band[r0, c0]
        # Already at the low: stay. A gutter is flat along its length, so the lowest cell in
        # the window is usually a tie — moving to whichever one argmin happens to return
        # would drag every inlet to the same end of its own gutter.
        if np.isfinite(here) and here <= lowest + 1e-9:
            continue
        k = int(np.nanargmin(window))
        dr, dc = divmod(k, window.shape[1])
        rr, cc = r1 + dr, c1 + dc
        if (rr, cc) == (r0, c0):
            continue
        nx, ny = transform * (cc + 0.5, rr + 0.5)
        out[name] = (nx, ny)
        n_moved += 1
        max_move = max(max_move, ((nx - x) ** 2 + (ny - y) ** 2) ** 0.5)

    return out, {"n_moved": n_moved, "n_outside_dem": n_outside,
                 "max_move_m": round(max_move, 2), "search_radius_m": search_radius_m}
