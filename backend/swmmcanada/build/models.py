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
    system: str = "storm_minor"   # drainage system tag (ADR 0011): storm_minor|storm_major|sanitary


@dataclass(frozen=True)
class OutfallIn:
    name: str
    invert_m: float
    x: float
    y: float
    kind: str = "FREE"
    system: str = "storm_minor"


@dataclass(frozen=True)
class ConduitIn:
    name: str
    from_node: str
    to_node: str
    length_m: float
    diameter_m: float = 0.30
    roughness_n: float = 0.013
    system: str = "storm_minor"


@dataclass(frozen=True)
class SubcatchmentIn:
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
    system: str = "storm_minor"


@dataclass(frozen=True)
class NetworkIn:
    junctions: List[JunctionIn]
    outfalls: List[OutfallIn]
    conduits: List[ConduitIn]


def filter_system(network: "NetworkIn", system: str = "storm_minor") -> "NetworkIn":
    """The subgraph of one tagged drainage system (ADR 0011) — the shared per-system view
    exporters consume (MIKE+, ICM), so no exporter re-implements the tag filter."""
    keep = lambda e: getattr(e, "system", "storm_minor") == system
    return NetworkIn(junctions=[j for j in network.junctions if keep(j)],
                     outfalls=[o for o in network.outfalls if keep(o)],
                     conduits=[c for c in network.conduits if keep(c)])


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
