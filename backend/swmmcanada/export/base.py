"""Model-export interface (ADR 0008): the datastore → a target hydraulic-model format.

One `ModelExporter` seam so EPA SWMM, DHI MIKE+ (and, later, InfoWorks ICM) all consume the
model-ready datastore rather than each other's output, and each reports what did **not**
translate faithfully (surfaced, never silently dropped — issue #5).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Protocol, runtime_checkable

if TYPE_CHECKING:  # avoid importing the heavy datastore/geopandas stack just for a type hint
    from swmmcanada.datastore import ModelReadyDatastore


@dataclass(frozen=True)
class LossyMapping:
    """One datastore field a target format cannot represent exactly.

    ``kind`` is a small controlled vocabulary:
      * ``"approximated"`` — mapped, but with a modelling approximation (e.g. CN → Horton),
      * ``"dropped"``      — no target equivalent, omitted,
      * ``"restructured"`` — represented differently (e.g. one field split across several).
    """

    source: str      # datastore field, e.g. "cn"
    target: str      # target concept it maps to (or "—" when dropped)
    kind: str        # "approximated" | "dropped" | "restructured"
    detail: str      # human-readable explanation


@dataclass
class ExportResult:
    """What an exporter wrote, plus everything it could not translate faithfully."""

    target: str
    out_dir: Path
    files: List[Path] = field(default_factory=list)
    lossy: List[LossyMapping] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@runtime_checkable
class ModelExporter(Protocol):
    """Consume the datastore, write a target format, return an :class:`ExportResult`."""

    target: str

    def export(self, ds: "ModelReadyDatastore", out_dir) -> ExportResult:
        ...
