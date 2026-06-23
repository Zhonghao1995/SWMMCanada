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
from typing import List

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
        return {
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


def validate_model(
    network: NetworkIn,
    subcatchments: List[SubcatchmentIn],
    aoi,
    *,
    method: MethodDescriptor,
) -> ValidationReport:
    """Run every check against the assembled model and return a structured report."""
    node_names = {n.name for n in list(network.junctions) + list(network.outfalls)}
    node_coords = {n.name: (float(n.x), float(n.y)) for n in list(network.junctions) + list(network.outfalls)}

    results: List[CheckResult] = []

    # Topological — always run, even for cells without a polygon.
    results.append(C.check_outlet_present(subcatchments))
    results.append(C.check_outlet_exists(subcatchments, node_names))
    results.append(C.check_area_positive(subcatchments))

    # Geometric — operate on cells that carry a polygon; flag the ones that don't.
    results.append(C.check_geometry_absent(subcatchments))
    geo = C.GeoContext(subcatchments, aoi)        # one reprojection to a metric CRS, shared
    results.append(C.check_geometry_valid(geo))
    results.append(C.check_overlap(geo))
    results.append(C.check_area_conservation(subcatchments, aoi))
    results.append(C.check_aoi_coverage(geo))
    results.append(C.check_aoi_containment(geo))
    results.append(C.check_node_coverage(geo, node_coords))
    results.append(C.check_outlet_distance(geo, node_coords))
    results.append(C.check_shape_plausibility(geo))

    return ValidationReport(method=method, n_subcatchments=len(subcatchments), checks=results)
