"""The resolver — the **only** place a delineation method is chosen (ADR 0029 Q10/Q11).

Two invariants live here, and both exist because of what happened to the datastore
(ADR 0003 built it as an optional path, nobody dogfooded it, it rotted until ADR 0007 forced
it to be the primary one):

1. **Sole entry point.** City adapters discover data and map fields; they do not choose
   methods. The build pipeline executes whatever this returns; it does not branch on data
   availability itself.
2. **Never a bare label.** Every plan carries the reason, the gate results and the evidence
   values behind it. A `Level` that cannot explain itself is unarguable, and Level 1 is
   revocable at runtime — that only works if the grounds are on the record.

**One pipeline, many inputs.** The methods are not separate algorithms. They differ in
exactly three inputs, and everything downstream (shaping, attribute derivation, validation)
is shared:

    boundary  — what bounds the units          (official basin | AOI)
    anchors   — what seeds them                (catch basins | junctions)
    shaping   — how the land is divided        (parcel | DEM D8 | Voronoi)

Adding a method means adding a combination, never a new code path per city × system × level.

Phase 0 (2026-08-12) measured which combinations have real data behind them across 36
cities. The authoritative-polygon path had **zero** subjects — no city publishes drainage
areas fine enough to use as model units — so it is not implemented. That is the rule, not an
omission: only paths with data get built.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from swmmcanada.validate.schema import (METHOD_CATCHBASIN_DEM,
                                        METHOD_CATCHBASIN_VORONOI,
                                        METHOD_JUNCTION_VORONOI)
from typing import Dict, Optional

#: Shaping strategies, coarsest last. Names are the honest, controlled vocabulary that
#: reaches provenance and the result package — `voronoi` must never be presentable as a
#: hydrologically delineated result.
PARCEL = "parcel"
DEM_D8 = "dem_d8"
VORONOI = "voronoi"

CATCH_BASIN = "catch_basin"
JUNCTION = "junction"

AOI = "aoi"
OFFICIAL_BASIN = "official_basin"

#: Coarsest posting on which a kerb is worth encoding. A 150 mm face is far below the
#: vertical noise of a 30 m DEM; at 1–2 m LiDAR it is a real, decisive feature.
KERB_MAX_DEM_RES_M = 2.0


@dataclass(frozen=True)
class Evidence:
    """What is actually available for **this** AOI — runtime facts, not city-level claims.

    The distinction matters: a city can publish 7,864 catch basins and still have none
    inside a lakeside AOI. A static capability row would say "catch basins available" and
    be wrong here.
    """

    n_catchbasins: int = 0
    n_parcels: int = 0
    n_buildings: int = 0
    n_junctions: int = 0
    #: Kerb lines published for this AOI. A kerb decides where street runoff goes, and a
    #: bare DEM at LiDAR posting usually cannot see it (规划书 §4 priority 2).
    n_kerbs: int = 0
    dem_available: bool = False
    dem_resolution_m: Optional[float] = None
    official_basin_level: Optional[str] = None   # capability table verdict, if any
    city: Optional[str] = None
    system: str = "storm"

    def as_dict(self) -> Dict:
        return {"n_catchbasins": self.n_catchbasins, "n_parcels": self.n_parcels,
                "n_buildings": self.n_buildings, "n_junctions": self.n_junctions,
                "n_kerbs": self.n_kerbs,
                "dem_available": self.dem_available,
                "dem_resolution_m": self.dem_resolution_m,
                "official_basin_level": self.official_basin_level}


@dataclass(frozen=True)
class DelineationPlan:
    """A decision plus its grounds. `method` is the controlled-vocabulary label; the three
    input fields are what the shared pipeline actually consumes."""

    method: str
    boundary: str
    anchors: str
    shaping: str
    reason: str
    gates: Dict[str, bool] = field(default_factory=dict)
    evidence: Dict[str, object] = field(default_factory=dict)
    confidence: str = "low"

    def as_dict(self) -> Dict:
        return {"method": self.method, "boundary": self.boundary, "anchors": self.anchors,
                "shaping": self.shaping, "reason": self.reason, "gates": dict(self.gates),
                "evidence": dict(self.evidence), "confidence": self.confidence}


def resolve(evidence: Evidence) -> DelineationPlan:
    """Choose the delineation plan for one AOI. Total function: it always returns a plan,
    because the coarsest option (Voronoi of junctions) needs nothing but nodes."""
    ev = evidence.as_dict()
    # An official basin never *selects* a method — it only bounds one (Phase 0: every
    # official layer measured is a macro basin). Recorded so the choice is visible.
    boundary = (OFFICIAL_BASIN if evidence.official_basin_level in ("level_2", "level_1")
                else AOI)

    has_inlets = evidence.n_catchbasins > 0
    has_land = evidence.n_parcels > 0 or evidence.n_buildings > 0
    # A 150 mm kerb only exists on a surface fine enough to hold it. Conditioning a 30 m
    # posting with kerb lines would be theatre: the edit is smaller than a pixel's noise.
    fine_enough = (evidence.dem_resolution_m is not None
                   and evidence.dem_resolution_m <= KERB_MAX_DEM_RES_M)
    kerbs_usable = bool(evidence.n_kerbs) and evidence.dem_available and fine_enough
    gates = {"inlets_present": has_inlets, "land_present": has_land,
             "dem_present": evidence.dem_available, "kerb_usable": kerbs_usable}

    if has_inlets and kerbs_usable:
        return DelineationPlan(
            method=METHOD_CATCHBASIN_DEM, boundary=boundary, anchors=CATCH_BASIN,
            shaping=DEM_D8, gates=gates, evidence=ev, confidence="medium",
            reason=(f"{evidence.n_catchbasins} inlets, {evidence.n_kerbs} kerb lines and a "
                    f"{evidence.dem_resolution_m:g} m surface: runoff is routed to the "
                    f"inlets over terrain that knows where the kerbs are"))
    if has_inlets and has_land:
        return DelineationPlan(
            method="catchbasin_parcel", boundary=boundary, anchors=CATCH_BASIN,
            shaping=PARCEL, gates=gates, evidence=ev, confidence="medium",
            reason=(f"{evidence.n_catchbasins} inlets and "
                    f"{evidence.n_parcels} parcels / {evidence.n_buildings} buildings in "
                    f"the AOI: land divides on real lot lines and roofs, seeded at the "
                    f"real inlets"))
    if has_inlets:
        return DelineationPlan(
            method=METHOD_CATCHBASIN_VORONOI, boundary=boundary, anchors=CATCH_BASIN,
            shaping=VORONOI, gates=gates, evidence=ev, confidence="low",
            reason=(f"{evidence.n_catchbasins} inlets but no parcels or buildings "
                    f"published for this AOI: seeds are real, the division between them "
                    f"is geometric"))
    if evidence.dem_available:
        return DelineationPlan(
            method="junction_dem", boundary=boundary, anchors=JUNCTION, shaping=DEM_D8,
            gates=gates, evidence=ev, confidence="medium",
            reason=("no inlet data for this AOI; a DEM is available, so basins follow "
                    "terrain to the manholes (subject to the terrain honesty gate)"))
    return DelineationPlan(
        method=METHOD_JUNCTION_VORONOI, boundary=boundary, anchors=JUNCTION,
        shaping=VORONOI,
        gates=gates, evidence=ev, confidence="low",
        reason=("no inlet data and no DEM for this AOI: land is assigned to the nearest "
                "node. Geometric, not hydrological — the honest floor, not a delineation"))
