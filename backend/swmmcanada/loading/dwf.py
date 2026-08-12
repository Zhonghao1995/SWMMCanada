"""Dry-weather flow from sewer service areas (ADR 0031).

The sanitary system has had pipes and manholes and no water in it since ADR 0011 grafted it
in as a tracer. This turns it into a model.

**The uncomfortable fact this module is built around:** the accuracy ceiling is set by the
per-capita coefficient, not by how finely the service areas are drawn. Divide a city into
8,000 areas instead of 57 and multiply each by a handbook number carrying +/-50%, and nothing
has become more accurate — you have bought false precision, a very fine polygon times a
guess. So every flow here reports the tier its population estimate came from, and every
coefficient states whether it was measured, calibrated or taken from a handbook.

Layering (ADR 0031), best first::

    A  population       -> census / dwelling counts, real data, handbook coefficient
    C  plant flow       -> measured influent calibrates the coefficient  (the only tier
                           that removes the false precision; not yet sourced -- Phase 0
                           found no influent flow in any municipal GIS catalogue)
    B  address points   -> distributes an ALREADY-CALIBRATED total more finely
    D  land use         -> fallback for commercial/industrial, where people do not
                           represent water use

C outranks B deliberately: calibrate the total before arguing about its distribution.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Sequence

SECONDS_PER_DAY = 86400.0

#: Litres/second -> the model's [OPTIONS] FLOW_UNITS. **Not optional.** SWMM reads a `[DWF]`
#: base value in whatever unit the model declares, and this project defaults to CMS, so
#: writing litres/second unconverted overstates every sanitary inflow by 1000x — a model
#: that still runs, still balances, and is nonsense.
LPS_TO_FLOW_UNITS = {
    "CMS": 1.0e-3,      # m3/s
    "LPS": 1.0,
    "MLD": 86.4e-3,     # megalitres/day
    "CFS": 0.0353147,   # cubic feet/second
    "GPM": 15.8503,     # US gallons/minute
    "MGD": 0.0228245,   # million US gallons/day
}


def to_flow_units(lps: float, flow_units: str) -> float:
    """Convert litres/second to the model's declared flow units."""
    try:
        return lps * LPS_TO_FLOW_UNITS[str(flow_units).upper()]
    except KeyError:  # a unit we have not tabulated must fail loudly, not default to 1.0
        raise ValueError(
            f"no litres/second conversion for FLOW_UNITS={flow_units!r}; add it to "
            f"LPS_TO_FLOW_UNITS rather than writing an unconverted value") from None


class LoadingTier(Enum):
    """Where a service area's population estimate came from. Counted per build, exactly like
    the invert gap-fill ladder, so "how much of this model is estimated" has an answer."""

    MEASURED = "measured"                # the city published population for this area
    DWELLINGS = "dwellings"              # dwelling/address count x household size
    BUILDINGS = "buildings"              # residential building count x household size
    AREA_DENSITY = "area_density"        # area x assumed density -- the honest floor


@dataclass(frozen=True)
class DwfAssumptions:
    """Every number here is a handbook figure until a city's measured plant flow replaces
    it. They live in one object so a build can record exactly what it assumed, and so
    calibration is a single substitution rather than a hunt through the code.

    Defaults are Canadian municipal design practice; they belong in ASSUMPTIONS.md and must
    be cited as assumptions, never as results.
    """

    litres_per_capita_day: float = 280.0     # average-day domestic sanitary flow
    persons_per_dwelling: float = 2.4        # StatCan 2021 average household size
    persons_per_hectare: float = 45.0        # medium-density urban residential
    #: Marks the coefficient's standing. "synthetic" until measured influent calibrates it.
    source: str = "synthetic"

    def calibrated(self, litres_per_capita_day: float, *, source: str = "calibrated"):
        """Return the same assumptions with a coefficient derived from measured flow."""
        return replace(self, litres_per_capita_day=litres_per_capita_day, source=source)


@dataclass(frozen=True)
class PopulationEstimate:
    people: float
    tier: LoadingTier
    basis: str

    def as_dict(self) -> Dict:
        return {"people": round(self.people, 1), "tier": self.tier.value, "basis": self.basis}


def estimate_population(area, assumptions: DwfAssumptions) -> PopulationEstimate:
    """Population served by one service area, taking the best evidence it carries.

    The ladder never silently skips a rung: whichever one answers is named in the result, so
    an area resting on assumed density is distinguishable from one resting on a census count
    even though both end up as a number of people.
    """
    if area.population is not None:
        return PopulationEstimate(float(area.population), LoadingTier.MEASURED,
                                  "published population for this area")
    if area.dwelling_units:
        n = area.dwelling_units * assumptions.persons_per_dwelling
        return PopulationEstimate(n, LoadingTier.DWELLINGS,
                                  f"{area.dwelling_units} dwellings x "
                                  f"{assumptions.persons_per_dwelling} persons")
    return PopulationEstimate(area.area_ha * assumptions.persons_per_hectare,
                              LoadingTier.AREA_DENSITY,
                              f"{area.area_ha:.2f} ha x {assumptions.persons_per_hectare} "
                              f"persons/ha (assumed density)")


def dwf_lps(population: float, assumptions: DwfAssumptions) -> float:
    """Average-day dry-weather flow in litres/second."""
    return population * assumptions.litres_per_capita_day / SECONDS_PER_DAY


#: A standard municipal diurnal shape: overnight minimum, morning and evening peaks. Hourly
#: multipliers on the average day, mean 1.0. Like the coefficient, it is a handbook shape
#: until a city's own flow record replaces it.
DIURNAL_FACTORS: Sequence[float] = (
    0.44, 0.34, 0.29, 0.28, 0.32, 0.55, 1.02, 1.45, 1.61, 1.54, 1.42, 1.32,
    1.25, 1.20, 1.15, 1.13, 1.18, 1.30, 1.38, 1.32, 1.16, 0.97, 0.76, 0.58,
)
DIURNAL_PATTERN_NAME = "DWF_DIURNAL"


def diurnal_pattern():
    """(name, factors) for the hourly DWF pattern. Mean is 1.0 by construction, so the
    pattern redistributes the average day without changing its volume."""
    mean = sum(DIURNAL_FACTORS) / len(DIURNAL_FACTORS)
    return DIURNAL_PATTERN_NAME, [round(f / mean, 4) for f in DIURNAL_FACTORS]


@dataclass
class LoadingResult:
    areas: List
    diagnostics: Dict = field(default_factory=dict)


def load_service_areas(areas: Sequence, assumptions: Optional[DwfAssumptions] = None
                       ) -> LoadingResult:
    """Attach dry-weather flow to each service area, counting the tiers used.

    Returns new areas (the inputs are frozen) plus diagnostics that answer, for the build as
    a whole: how much flow, from what evidence, under which coefficient, and how much of it
    rests on an assumed density rather than a count.
    """
    a = assumptions or DwfAssumptions()
    tiers: Dict[str, int] = {t.value: 0 for t in LoadingTier}
    out, total_lps, total_people = [], 0.0, 0.0

    for area in areas:
        est = estimate_population(area, a)
        flow = dwf_lps(est.people, a)
        tiers[est.tier.value] += 1
        total_lps += flow
        total_people += est.people
        out.append(replace(area, population=round(est.people, 1),
                           dwf_lps=round(flow, 5),
                           dwf_pattern=DIURNAL_PATTERN_NAME,
                           loading_source=a.source))

    estimated = tiers[LoadingTier.AREA_DENSITY.value]
    return LoadingResult(out, {
        "n_service_areas": len(out),
        "population_tiers": tiers,
        "pct_on_assumed_density": round(100.0 * estimated / len(out), 1) if out else 0.0,
        "total_population": round(total_people),
        "total_dwf_lps": round(total_lps, 3),
        "litres_per_capita_day": a.litres_per_capita_day,
        "coefficient_source": a.source,
        # Stated plainly because it is the single most important caveat on the result.
        "accuracy_note": (
            "flow accuracy is bounded by the per-capita coefficient, not by service-area "
            "resolution; a synthetic coefficient carries roughly +/-50% until measured "
            "plant influent calibrates it"),
    })
