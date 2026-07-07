"""Subcatchment validation layer (PRD: subcatchment validation).

`validate_model` is a pure function: assembled model in (network + subcatchments + AOI +
an honest method descriptor), a structured `ValidationReport` out. No I/O, no SWMM, no
pipeline coupling — so the frontend, CLI, and package all reuse one implementation, and
every check is unit-testable in isolation.

Severity is two-tier: an **error** means the model is structurally untrustworthy (the
pipeline should stop before emitting the `.inp`); a **warning** means it runs but is
approximate. `ValidationReport.ok` is true iff there are zero failing error-severity checks.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from swmmcanada.build.models import NetworkIn, SubcatchmentIn
from swmmcanada.validate import checks as C
from swmmcanada.validate import schema


class SubcatchmentValidationError(Exception):
    """Raised when the subcatchment model fails validation (>=1 error-severity check).
    The pipeline writes validation.json, then raises this so no untrusted .inp is emitted."""


@dataclass(frozen=True)
class CheckResult:
    id: str
    severity: str            # schema.ERROR | schema.WARNING (level if it fails)
    passed: bool
    message: str
    metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MethodDescriptor:
    """Honest labelling of how the subcatchments were made (controlled vocabulary)."""
    method: str              # one of schema.METHODS
    physical_basis: str      # e.g. "nearest inlet service area"
    confidence: str          # low | medium | high


@dataclass(frozen=True)
class ValidationReport:
    method: MethodDescriptor
    n_subcatchments: int
    checks: List[CheckResult]
    delineation: Optional[dict] = None   # gate readings / fallback reason (ADR 0010)
    systems: Optional[dict] = None       # per-system element counts (ADR 0011)
    forcing: Optional[dict] = None       # rainfall tier/station/coverage (ADR 0014)

    @property
    def errors(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == schema.ERROR]

    @property
    def warnings(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == schema.WARNING]

    @property
    def ok(self) -> bool:
        """True iff no error-severity check failed (the .inp may be emitted)."""
        return not self.errors

    def to_dict(self) -> dict:
        out = {
            "validation_version": schema.VALIDATION_VERSION,
            "subcatchment_method": self.method.method,
            "physical_basis": self.method.physical_basis,
            "confidence": self.method.confidence,
            "ok": self.ok,
            "summary": {
                "n_subcatchments": self.n_subcatchments,
                "n_errors": len(self.errors),
                "n_warnings": len(self.warnings),
            },
            "checks": [
                {
                    "id": c.id, "severity": c.severity, "passed": c.passed,
                    "message": c.message, "metrics": c.metrics,
                }
                for c in self.checks
            ],
        }
        if self.delineation:
            out["delineation"] = self.delineation
        if self.systems:
            out["systems"] = self.systems
        if self.forcing:
            out["forcing"] = self.forcing
        return out


def validate_model(
    network: NetworkIn,
    subcatchments: List[SubcatchmentIn],
    aoi,
    *,
    method: MethodDescriptor,
    delineation: Optional[dict] = None,
    forcing: Optional[dict] = None,
    water=None,
    served=None,
) -> ValidationReport:
    """Run every check against the assembled model and return a structured report."""
    all_nodes = list(network.junctions) + list(network.outfalls)
    # Outlet resolution may target any node; coverage/distance diagnostics are a STORM
    # question — a sanitary manhole not covered by a (storm) subcatchment is not a gap
    # (ADR 0011 per-system scoping).
    node_names = {n.name for n in all_nodes}
    storm_nodes = [n for n in all_nodes if getattr(n, "system", "storm_minor") == "storm_minor"]
    node_coords = {n.name: (float(n.x), float(n.y)) for n in storm_nodes}
    systems: dict = {}
    for n in all_nodes:
        sysname = getattr(n, "system", "storm_minor")
        systems.setdefault(sysname, {"nodes": 0, "conduits": 0})["nodes"] += 1
    for c in network.conduits:
        sysname = getattr(c, "system", "storm_minor")
        systems.setdefault(sysname, {"nodes": 0, "conduits": 0})["conduits"] += 1

    results: List[CheckResult] = []

    # Topological — always run, even for cells without a polygon.
    results.append(C.check_outlet_present(subcatchments))
    results.append(C.check_outlet_exists(subcatchments, node_names))
    results.append(C.check_area_positive(subcatchments))

    # Geometric — operate on cells that carry a polygon; flag the ones that don't.
    results.append(C.check_geometry_absent(subcatchments))
    geo = C.GeoContext(subcatchments, aoi, water, served)  # one reprojection to metric, shared
    results.append(C.check_geometry_valid(geo))
    results.append(C.check_overlap(geo))
    results.append(C.check_area_conservation(
        subcatchments, aoi,
        effective_aoi_m2=(geo.effective_aoi_m.area
                          if (water is not None or served is not None) else None)))
    results.append(C.check_aoi_coverage(geo))
    results.append(C.check_aoi_containment(geo))
    results.append(C.check_node_coverage(geo, node_coords))
    results.append(C.check_outlet_distance(geo, node_coords))
    results.append(C.check_shape_plausibility(geo))

    if forcing:
        results.append(C.check_forcing_consistency(forcing))

    return ValidationReport(
        method=method, n_subcatchments=len(subcatchments), checks=results,
        delineation=delineation, systems=systems or None, forcing=forcing,
    )
