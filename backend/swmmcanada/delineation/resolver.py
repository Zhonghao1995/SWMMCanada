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

from swmmcanada.validate.schema import (METHOD_JUNCTION_DEM,
                                        METHOD_JUNCTION_PARCEL,
                                        METHOD_JUNCTION_STREET,
                                        METHOD_USER_SUPPLIED,
                                        METHOD_CATCHBASIN_VORONOI,
                                        METHOD_JUNCTION_VORONOI)
from typing import Dict, Optional

#: Shaping strategies, coarsest last. Names are the honest, controlled vocabulary that
#: reaches provenance and the result package — `voronoi` must never be presentable as a
#: hydrologically delineated result.
PARCEL = "parcel"
DEM_D8 = "dem_d8"
STREET_SEGMENT = "street_segment"
VORONOI = "voronoi"

CATCH_BASIN = "catch_basin"
JUNCTION = "junction"

AOI = "aoi"
OFFICIAL_BASIN = "official_basin"
USER = "user"

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
    #: Street centrelines available for this AOI. Frontage splitting needs them: a lot
    #: drains to the street it faces, and without the streets there is nothing to face.
    n_streets: int = 0
    #: Kerb lines published for this AOI. A kerb decides where street runoff goes, and a
    #: bare DEM at LiDAR posting usually cannot see it (规划书 §4 priority 2).
    n_kerbs: int = 0
    #: Units in a subcatchment layer the user uploaded. Every choice this module makes is a
    #: judgement call under uncertainty; where someone has local knowledge, theirs wins.
    n_user_units: int = 0
    dem_available: bool = False
    dem_resolution_m: Optional[float] = None
    official_basin_level: Optional[str] = None   # capability table verdict, if any
    city: Optional[str] = None
    system: str = "storm"

    def as_dict(self) -> Dict:
        return {"n_catchbasins": self.n_catchbasins, "n_parcels": self.n_parcels,
                "n_buildings": self.n_buildings, "n_junctions": self.n_junctions,
                "n_kerbs": self.n_kerbs, "n_streets": self.n_streets,
                "n_user_units": self.n_user_units,
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
    # Priority 0 (规划书 §4, extended): an explicit choice outranks anything we would infer
    # AND anything the city published. Nothing below is even consulted.
    if evidence.n_user_units:
        return DelineationPlan(
            method=METHOD_USER_SUPPLIED, boundary=USER, anchors=USER, shaping=USER,
            gates={"user_layer": True}, evidence=ev, confidence="unrated",
            reason=(f"{evidence.n_user_units} subcatchments supplied by the user: these "
                    f"boundaries are theirs, not ours, and override every method here"))

    # An official basin never *selects* a method — it only bounds one (Phase 0: every
    # official layer measured is a macro basin). Recorded so the choice is visible.
    boundary = (OFFICIAL_BASIN if evidence.official_basin_level in ("level_2", "level_1")
                else AOI)

    # Municipal practice: a subcatchment discharges to a node that exists in the model, and
    # in a published network those are the maintenance holes. Catch basins are surface
    # structures joined by leads — almost none are model nodes, and the reach between two
    # nodes has ONE tributary area however many inlets sit on it. They stay as evidence
    # (which main a lead taps) without becoming the unit land is divided among.
    has_nodes = evidence.n_junctions > 0
    has_streets = evidence.n_streets > 0
    has_inlets = evidence.n_catchbasins > 0
    has_land = evidence.n_parcels > 0 or evidence.n_buildings > 0
    # A 150 mm kerb only exists on a surface fine enough to hold it. Conditioning a 30 m
    # posting with kerb lines would be theatre: the edit is smaller than a pixel's noise.
    fine_enough = (evidence.dem_resolution_m is not None
                   and evidence.dem_resolution_m <= KERB_MAX_DEM_RES_M)
    kerbs_usable = bool(evidence.n_kerbs) and evidence.dem_available and fine_enough
    terrain_usable = evidence.dem_available and fine_enough
    gates = {"nodes_present": has_nodes, "streets_present": has_streets,
             "inlets_present": has_inlets, "land_present": has_land,
             "dem_present": evidence.dem_available, "kerb_usable": kerbs_usable,
             "terrain_usable": terrain_usable}

    # 规划书 §4 priorities 2 and 3 are the SAME pipeline: inlets as drainage targets over a
    # conditioned surface. Kerbs are one more input, not another algorithm — they change how
    # much the answer is worth, not how it is produced.
    # Storm land is always divided among the MODEL's nodes. What changes between methods is
    # how it is divided, never what it is divided among — because a subcatchment has to
    # discharge to a node that exists, and the reach between two nodes has one tributary
    # area however many inlets sit on it.
    if has_nodes and has_streets:
        return DelineationPlan(
            method=METHOD_JUNCTION_STREET, boundary=boundary, anchors=JUNCTION,
            shaping=STREET_SEGMENT, gates={**gates, "streets_present": True},
            evidence=ev, confidence="medium",
            reason=(f"{evidence.n_junctions} nodes and {evidence.n_streets} street "
                    f"segments: each node takes the land draining to its own reach — the "
                    f"segment plus the lots fronting it"))
    if has_nodes and terrain_usable:
        return DelineationPlan(
            method=METHOD_JUNCTION_DEM, boundary=boundary, anchors=JUNCTION,
            shaping=DEM_D8, gates=gates, evidence=ev,
            confidence="high" if kerbs_usable else "medium",
            reason=(f"{evidence.n_junctions} nodes and a "
                    f"{evidence.dem_resolution_m:g} m surface with "
                    f"{evidence.n_kerbs} kerb lines: land follows terrain to its node"
                    if kerbs_usable else
                    f"{evidence.n_junctions} nodes and a "
                    f"{evidence.dem_resolution_m:g} m surface, no kerb lines: land follows "
                    f"terrain to its node, which cannot see where a kerb sends it"))
    if has_nodes and has_land:
        # Same unit, better edges: land still goes to a model node, and the boundary between
        # neighbouring nodes follows real lot lines instead of a bisector through the middle
        # of somebody's garden.
        return DelineationPlan(
            method=METHOD_JUNCTION_PARCEL, boundary=boundary, anchors=JUNCTION,
            shaping=PARCEL, gates=gates, evidence=ev, confidence="medium",
            reason=(f"{evidence.n_junctions} nodes with {evidence.n_parcels} parcels and "
                    f"{evidence.n_buildings} buildings, but no streets: land divides on "
                    f"real lot lines, assigned to its node"))

    return DelineationPlan(
        method=METHOD_JUNCTION_VORONOI, boundary=boundary, anchors=JUNCTION,
        shaping=VORONOI,
        gates=gates, evidence=ev, confidence="low",
        reason=("no streets and no usable surface for this AOI: land is assigned to the "
                "nearest node. Geometric, not hydrological — the honest floor, not a "
                "delineation"))
