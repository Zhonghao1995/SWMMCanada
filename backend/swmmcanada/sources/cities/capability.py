"""Global capability registry (ADR 0030) — the human-declared half of the Phase 0 audit.

Two tables live here, beside ``registry.py`` and ``DATA_TIERS``, because they are a
**fleet-wide** statement ("which city publishes what"), not per-city fetching logic:

* ``SERVICE_CATALOGUE`` — where each city's published layers can be enumerated.
* ``ROLE_MAP``          — the one-time human classification "this layer is a catch basin".

Everything else on a capability row is **measured** by ``swmmcanada.audit.scanner`` and must
never be hand-written (ADR 0030: people fill facts, machines fill judgements).

Role vocabulary normalisation (engineering decision, 2026-08-12)
---------------------------------------------------------------
The frozen design listed roles like ``storm_catchment`` / ``sanitary_subcatchment`` /
``sanitary_lateral`` *by example*. Those names fold the **system** into the **role**, which
re-creates the very ambiguity the enum exists to kill (a lateral would be spellable as
``lateral``, ``sanitary_lateral`` or ``storm_lateral``). So:

    role   = WHAT the layer is       (catchment, lateral, catch_basin, ...)
    system = WHICH network it serves (storm, sanitary, combined)

``storm_catchment`` is therefore ``(role=CATCHMENT, system=storm)`` and ``sanitary_lateral``
is ``(role=LATERAL, system=sanitary)``. Same expressive power, one spelling each.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple


class Role(Enum):
    """What a published layer *is*. Closed set — a new kind of layer needs a code change,
    which is the point: an unrecognised layer must surface as ``unclassified``, never be
    silently absorbed into a near-miss role."""

    # --- drainage-area polygons (the thing this whole audit exists to find) ---
    CATCHMENT = "catchment"            # macro basin: one per outfall / pump station (Level 2)
    SUBCATCHMENT = "subcatchment"      # fine unit: one per inlet / pipe segment (Level 1 hope)
    # --- network assets ---
    CATCH_BASIN = "catch_basin"        # storm inlet structure; the storm anchor
    INLET = "inlet"                    # inlet where a city separates it from catch basins
    LATERAL = "lateral"                # service connection; the parcel->main evidence
    GRAVITY_MAIN = "gravity_main"      # pipe segment; the sanitary anchor
    MANHOLE = "manhole"
    OUTFALL = "outfall"
    CSO = "cso"                        # combined overflow structure = real overflow outlet
    INTERCEPTOR = "interceptor"        # trunk to WWTP = the dry-weather outlet
    # --- land / terrain inputs ---
    PARCEL = "parcel"
    BUILDING = "building"
    CURB = "curb"
    DEM = "dem"


#: Roles that can act as the *denominator* when measuring how fine a polygon layer is.
#: Storm areas are drawn per inlet; sanitary areas are drawn per pipe segment (the wording
#: municipalities themselves use: "the drainage area for each individual length of sewer").
#: Order matters: the first role a city actually publishes wins, and the verdict records
#: which one was used. Combined lists both because a combined polygon layer may be either
#: half of the dual source — a surface catchment drawn per inlet, or a wastewater service
#: area drawn per pipe segment. Hamilton's 8,147 combined areas are the latter ("the
#: drainage area for each individual length of combined sewer"), so a city that tags no
#: combined inlets must still be gradable against its mains.
ANCHOR_ROLES: Dict[str, Tuple[Role, ...]] = {
    "storm": (Role.CATCH_BASIN, Role.INLET, Role.MANHOLE),
    "combined": (Role.CATCH_BASIN, Role.INLET, Role.GRAVITY_MAIN, Role.MANHOLE),
    "sanitary": (Role.GRAVITY_MAIN, Role.MANHOLE),
}

#: Anchors that stand in for the real one. In a gravity network manholes bracket segments
#: roughly 1:1, so a manhole count approximates a segment count — close enough to grade a
#: city that publishes areas but no pipes (Hamilton publishes 22,096 catchment polygons and
#: no network at all). Any verdict resting on one is flagged, because "approximately the
#: right denominator" is a different claim from "the denominator".
PROXY_ANCHOR_ROLES: frozenset = frozenset({Role.MANHOLE})

#: The geometry a role must have. A layer whose geometry disagrees with its suggested role is
#: **not** quietly accepted — it is reported for review. This is what stops cartographic
#: derivatives and lookalike layers from poisoning the measurements: Victoria publishes
#: "Parcel Dimension Lines" (51,914 lines, not parcels) and "Storm Drain Flow Arrows -
#: Gravity Mains" (a second copy of the mains, drawn as arrows). Either one, counted as its
#: name suggests, would silently corrupt an anchor denominator.
EXPECTED_GEOMETRY: Dict[Role, frozenset] = {
    Role.CATCHMENT: frozenset({"esriGeometryPolygon"}),
    Role.SUBCATCHMENT: frozenset({"esriGeometryPolygon"}),
    Role.PARCEL: frozenset({"esriGeometryPolygon"}),
    Role.BUILDING: frozenset({"esriGeometryPolygon"}),
    Role.CATCH_BASIN: frozenset({"esriGeometryPoint", "esriGeometryMultipoint"}),
    Role.INLET: frozenset({"esriGeometryPoint", "esriGeometryMultipoint"}),
    Role.MANHOLE: frozenset({"esriGeometryPoint", "esriGeometryMultipoint"}),
    # Outfalls and overflow structures are points in most catalogues but polygons in a few
    # (the structure footprint); both are legitimate publications of the same asset.
    Role.OUTFALL: frozenset({"esriGeometryPoint", "esriGeometryMultipoint", "esriGeometryPolygon"}),
    Role.CSO: frozenset({"esriGeometryPoint", "esriGeometryMultipoint", "esriGeometryPolygon"}),
    Role.LATERAL: frozenset({"esriGeometryPolyline"}),
    Role.GRAVITY_MAIN: frozenset({"esriGeometryPolyline"}),
    Role.INTERCEPTOR: frozenset({"esriGeometryPolyline"}),
    Role.CURB: frozenset({"esriGeometryPolyline"}),
}

#: Roles that describe **land**, not a drainage network. Asking "which system is this
#: parcel on" is a category error: a parcel drains to storm AND discharges to sanitary. They
#: are shared inputs to every system's delineation, so they carry no system and must never
#: be reported as needing one.
SYSTEM_AGNOSTIC_ROLES: frozenset = frozenset({Role.PARCEL, Role.BUILDING, Role.CURB, Role.DEM})

SYSTEMS: Tuple[str, ...] = ("storm", "sanitary", "combined")


# --------------------------------------------------------------------------------------
# ROLE_MAP — the one-time human classification (ADR 0030)
# --------------------------------------------------------------------------------------
#: Keyed ``(city, service_name, layer_name) -> (Role | None, system | None)``.
#:
#: This is the *only* place a human overrides the machine. Entries exist for two reasons:
#: a layer the pattern matcher cannot resolve (a bare "Sewer" is sanitary in Victoria's
#: catalogue and everything in Toronto's), or a layer it resolves wrongly. ``(None, None)``
#: is a legitimate entry meaning "confirmed irrelevant" — it silences the unclassified
#: report without pretending the layer is something it is not.
#:
#: A re-scan inherits every entry here. A layer that is renamed or newly published matches
#: nothing, stays unclassified, and shows up in the report — which is the whole mechanism
#: for noticing that a city changed its catalogue.
ROLE_MAP: Dict[Tuple[str, str, str], Tuple[Optional[Role], Optional[str]]] = {
    # --- Victoria: catalogue is split by system, so a bare "Sewer" service is sanitary ---
    ("victoria", "OpenData_Sewer", "Sewer SubCatchment Areas"): (Role.SUBCATCHMENT, "sanitary"),
    ("victoria", "OpenData_Sewer", "Sewer Catchment Areas"): (Role.CATCHMENT, "sanitary"),
    ("victoria", "OpenData_Sewer", "Sewer Gravity Mains"): (Role.GRAVITY_MAIN, "sanitary"),
    ("victoria", "OpenData_Sewer", "Sewer Lateral Line"): (Role.LATERAL, "sanitary"),
    ("victoria", "OpenData_Sewer", "Sewer Manholes"): (Role.MANHOLE, "sanitary"),
    ("victoria", "OpenData_Sewer", "Sewer Outfall (Discharge)"): (Role.OUTFALL, "sanitary"),
    ("victoria", "OpenData_Sewer", "Lined Sewer Gravity Mains"): (Role.GRAVITY_MAIN, "sanitary"),

    # --- Esquimalt: "Drain" is the storm system, "Sewer" the sanitary one ---
    ("esquimalt", "Drain", "Drain Catch Basin"): (Role.CATCH_BASIN, "storm"),
    ("esquimalt", "Drain", "Drain Mains"): (Role.GRAVITY_MAIN, "storm"),
    ("esquimalt", "Drain", "Drain Manholes"): (Role.MANHOLE, "storm"),
    ("esquimalt", "Drain", "Outfalls"): (Role.OUTFALL, "storm"),
    ("esquimalt", "Catchment", "Catchment Areas"): (Role.CATCHMENT, "storm"),
    ("esquimalt", "S_Catchment", "Sewer Catchments"): (Role.CATCHMENT, "sanitary"),
    ("esquimalt", "Sewer", "Sewer Mains"): (Role.GRAVITY_MAIN, "sanitary"),
    ("esquimalt", "Sewer", "Sewer Manholes"): (Role.MANHOLE, "sanitary"),
    ("esquimalt", "Sewer", "Sewer Services"): (Role.LATERAL, "sanitary"),

    # --- Hamilton (external reference) ------------------------------------------------
    # Its manholes are ONE layer serving both the sanitary and combined networks. Assigning
    # them to either system would invent a split the city does not publish, so they stay
    # unattributed and Hamilton is reported as ungradable per system. That is the finding,
    # not a gap to paper over: Hamilton publishes 22,096 catchment polygons and no network.
    ("hamilton", "Sewer_Manhole", "Sewer_Manhole"): (Role.MANHOLE, None),
}


def apply_role_map(city: str, service_name: str, layer_name: str,
                   suggested: Tuple[Optional[Role], Optional[str]]):
    """Human classification wins over the matcher, when one exists for this layer."""
    return ROLE_MAP.get((city, service_name, layer_name), suggested)
