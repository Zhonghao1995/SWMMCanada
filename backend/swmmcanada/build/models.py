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


@dataclass(frozen=True)
class OutfallIn:
    name: str
    invert_m: float
    x: float
    y: float
    kind: str = "FREE"


@dataclass(frozen=True)
class ConduitIn:
    name: str
    from_node: str
    to_node: str
    length_m: float
    diameter_m: float = 0.30
    roughness_n: float = 0.013


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


@dataclass(frozen=True)
class NetworkIn:
    junctions: List[JunctionIn]
    outfalls: List[OutfallIn]
    conduits: List[ConduitIn]


@dataclass(frozen=True)
class RainfallSeries:
    timestamps: List[datetime]
    precip_mm: List[float]
    gage_name: str = "RG1"
    ts_name: str = "rain"
