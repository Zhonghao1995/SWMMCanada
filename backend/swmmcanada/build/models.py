"""Minimal input value objects `build` consumes (lean subset of the integration-spec
contracts, enough for the tracer-bullet model; grown as network/derive mature)."""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class JunctionIn:
    name: str
    invert_m: float
    x: float
    y: float
    max_depth_m: float = 2.0
    # Drainage system tag (ADR 0011, extended by ADR 0029 Q3):
    #   storm_minor | storm_major | sanitary | combined
    # `combined` is a tag, not a separate model: combined pipes stay wired into the storm
    # graph and are the one place storm and sanitary legitimately meet.
    system: str = "storm_minor"


@dataclass(frozen=True)
class OutfallIn:
    name: str
    invert_m: float
    x: float
    y: float
    kind: str = "FREE"     # SWMM boundary condition: FREE | NORMAL | FIXED | TIDAL
    system: str = "storm_minor"
    #: True when this outfall is a modelling boundary we invented because the city
    #: published none for that component — not a structure that exists. Kept separate from
    #: `kind`, which is the hydraulic boundary condition SWMM applies. An invented outfall
    #: that looks published passes validation quietly and then gets used.
    synthesised: bool = False


@dataclass(frozen=True)
class ConduitIn:
    name: str
    from_node: str
    to_node: str
    length_m: float
    diameter_m: float = 0.30
    roughness_n: float = 0.013
    system: str = "storm_minor"
    # Model fidelity (#130): pipe inverts are node inverts PLUS these offsets, so published
    # pipe-end elevations above the node bottom (drop structures) survive into the .inp.
    inlet_offset_m: float = 0.0
    outlet_offset_m: float = 0.0
    # Non-circular cross-sections: SWMM shape name + real dims where a city publishes them;
    # the default stays the equivalent-circular pipe (diameter_m).
    shape: str = "CIRCULAR"
    height_m: Optional[float] = None
    width_m: Optional[float] = None


@dataclass(frozen=True)
class SurfaceCatchment:
    """A patch of ground whose rain reaches one inlet (ADR 0029 Q1).

    The **only** thing that may be written to ``[SUBCATCHMENTS]`` / ``[SUBAREAS]`` /
    ``[POLYGONS]``. Its boundary comes from terrain, roads, kerbs and inlets — where water
    flows over the surface.

    Its counterpart, :class:`SewerServiceArea`, is deliberately *not* a subclass and shares
    no fields: household sewage does not run downhill to a manhole, it travels by lateral,
    so a service area has no width, no slope, no imperviousness and no infiltration. Giving
    the two a common base would invite exactly the confusion the split exists to end.
    """

    name: str
    outlet_node: str
    area_ha: float
    pct_imperv: float
    width_m: float
    pct_slope: float
    cn: float = 80.0
    n_imperv: float = 0.01
    n_perv: float = 0.10
    s_imperv_mm: float = 1.5
    s_perv_mm: float = 5.0
    pct_zero: float = 25.0
    polygon: Optional[List[Tuple[float, float]]] = None
    # Round-2 F-005: interior rings (enclosed water / foreign lots) — the ANALYSIS
    # geometry is Polygon(polygon, holes); the bare exterior is only the SWMM display
    # ring. Zonal statistics must not sample a lake as land.
    holes: Optional[List[List[Tuple[float, float]]]] = None
    system: str = "storm_minor"
    # Infiltration superset (ADR 0013): derive fills all three parameter sets; the writer
    # emits whichever the build's InfiltrationModel switch asks for. Defaults = HSG-B /
    # loam rows so a no-derive build still writes a valid model under any method.
    horton_f0_mm_h: float = 101.6
    horton_fc_mm_h: float = 5.7
    horton_decay_1_h: float = 4.14
    ga_psi_mm: float = 88.9
    ga_ksat_mm_h: float = 6.6
    ga_imd: float = 0.434
    # Land-cover built-up share of the cell, percent (ticket 13) — the coarse raster
    # signal validation's imperviousness cross-check compares pct_imperv against. A
    # derive diagnostic, never written to the .inp; None = no raster read (no derive,
    # no polygon, or no overlap), so the check can declare a skip instead of judging.
    landcover_built_pct: Optional[float] = None


@dataclass(frozen=True)
class SewerServiceArea:
    """The land whose **wastewater** a sanitary or combined sewer collects (ADR 0029 Q1).

    Not a watershed. Sewage reaches a pipe through a lateral connection, not by flowing over
    the ground, so this boundary follows parcels, buildings and service connections and may
    cross a topographic divide without anything being wrong.

    It never becomes a SWMM subcatchment. It enters the model as node loading — ``[DWF]``
    now, and ``[RDII]`` once a city with measured wet-weather sewer flow exists (ADR 0031
    keeps the interface and leaves it inactive rather than inventing RTK parameters).

    Geometry provenance and loading provenance are **separate fields on purpose**: an area
    can be taken verbatim from a municipal polygon while the flow coefficient applied to it
    is a handbook number. Collapsing them into one "source" would let an official boundary
    lend its authority to a synthetic rate.
    """

    name: str
    node: str                       # the sanitary/combined node this area loads
    area_ha: float
    system: str = "sanitary"        # sanitary | combined
    polygon: Optional[List[Tuple[float, float]]] = None
    holes: Optional[List[List[Tuple[float, float]]]] = None
    # --- loading evidence: what the flow was derived FROM, kept beside the flow itself ---
    population: Optional[float] = None
    dwelling_units: Optional[int] = None
    dwf_lps: Optional[float] = None          # dry-weather flow, litres/second
    dwf_pattern: Optional[str] = None        # diurnal pattern name
    # --- provenance, split in two (ADR 0031) ---
    geometry_source: str = "derived"         # official | derived
    loading_source: str = "synthetic"        # measured | calibrated | synthetic


@dataclass(frozen=True)
class NetworkIn:
    junctions: List[JunctionIn]
    outfalls: List[OutfallIn]
    conduits: List[ConduitIn]


def filter_system(network: "NetworkIn", systems="storm_minor") -> "NetworkIn":
    """The subgraph of one or more tagged drainage systems (ADR 0011, multi-select ADR 0029
    Q3) — the shared per-system view exporters consume, so no exporter re-implements it.

    ``systems`` takes a single tag or any iterable of them. A conduit survives only when its
    own tag is selected **and both of its endpoints survive**: a combined pipe joining a
    storm node is not exportable without that node, and a view referencing a node it does
    not contain is not a model.

    This never splits anything. One hydraulic model carries every system; an export is a
    view of it. The interaction between systems is the reason they share a model at all.
    """
    wanted = {systems} if isinstance(systems, str) else set(systems)
    keep = lambda e: getattr(e, "system", "storm_minor") in wanted
    junctions = [j for j in network.junctions if keep(j)]
    outfalls = [o for o in network.outfalls if keep(o)]
    names = {j.name for j in junctions} | {o.name for o in outfalls}
    conduits = [c for c in network.conduits
                if keep(c) and c.from_node in names and c.to_node in names]
    return NetworkIn(junctions=junctions, outfalls=outfalls, conduits=conduits)


def filter_system_report(network: "NetworkIn", systems="storm_minor"):
    """``filter_system`` plus what the filtering cost. Returns ``(view, report)``.

    Filtering a connected graph by tag can cut a component off from its outfall — storm
    pipes draining through a combined trunk lose their destination when combined is
    deselected. Measured across the fleet this is rare (Ottawa's storm and combined networks
    share one node, Toronto's share none), and rare is exactly when a silent failure
    survives to production. So the view is always produced and the damage is always
    reported; the caller decides whether an orphaned view is acceptable.
    """
    view = filter_system(network, systems)
    outfalls = {o.name for o in view.outfalls}
    adj: dict = {}
    for c in view.conduits:
        adj.setdefault(c.from_node, set()).add(c.to_node)
        adj.setdefault(c.to_node, set()).add(c.from_node)

    reached, stack = set(outfalls), [o for o in outfalls if o in adj]
    while stack:
        n = stack.pop()
        for m in adj.get(n, ()):  # noqa: B023 — adjacency is fixed for this walk
            if m not in reached:
                reached.add(m)
                stack.append(m)
    orphaned = sorted({j.name for j in view.junctions} - reached)

    wanted = [systems] if isinstance(systems, str) else sorted(systems)
    return view, {
        "systems": wanted,
        "n_junctions": len(view.junctions), "n_outfalls": len(view.outfalls),
        "n_conduits": len(view.conduits),
        "n_orphaned_nodes": len(orphaned),
        "orphaned_sample": orphaned[:10],
        "note": ("" if not orphaned else
                 f"{len(orphaned)} node(s) lose their route to an outfall in this view: "
                 f"they drain through a system that was not selected"),
    }


@dataclass(frozen=True)
class RainfallSeries:
    timestamps: List[datetime]
    precip_mm: List[float]
    gage_name: str = "RG1"
    ts_name: str = "rain"


@dataclass(frozen=True)
class EvaporationSeries:
    """Daily potential evaporation forcing (SWMM `[EVAPORATION] TIMESERIES`)."""
    timestamps: List[datetime]
    evap_mm_day: List[float]
    ts_name: str = "evap"


@dataclass(frozen=True)
class TemperatureSeries:
    """Daily mean air temperature — the climate-forcing record (and Hargreaves' input)."""
    timestamps: List[datetime]
    tmean_c: List[float]


@dataclass(frozen=True)
class TideSeries:
    """Predicted water levels (CHS wlp) — the stage boundary for tide-affected outfalls
    (#130): SWMM ``[OUTFALLS] ... TIMESERIES <ts_name>``. Levels are in the model's
    geodetic frame (converted from Chart Datum via the station's published offset) and
    timestamps in local STANDARD time (ADR 0024 §1); the conversion fields make the
    frame auditable."""
    timestamps: List[datetime]
    level_m: List[float]
    ts_name: str = "tide"
    station_name: str = ""
    datum: str = ""                    # geodetic datum the levels are in (e.g. CGVD28)
    datum_offset_m: float = 0.0        # applied CD -> datum shift
    clock_utc_offset_h: float = 0.0    # applied UTC -> local-standard-time shift
