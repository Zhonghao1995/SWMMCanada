"""Subcatchment validation layer — the model acceptance standard (PRD: subcatchment
validation). Pure `validate_model(...) -> ValidationReport`; reused by the pipeline,
the package (validation.json), the CLI, and the frontend."""
from swmmcanada.validate.core import (
    CheckResult,
    MethodDescriptor,
    SubcatchmentValidationError,
    ValidationReport,
    validate_model,
)

__all__ = [
    "validate_model",
    "ValidationReport",
    "CheckResult",
    "MethodDescriptor",
    "SubcatchmentValidationError",
]
