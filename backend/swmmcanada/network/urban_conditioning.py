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
