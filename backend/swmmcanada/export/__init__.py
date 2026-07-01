"""Model-export interface + per-target writers (ADR 0008).

The datastore is the hub (ADR 0003/0007); each exporter is a reader off it. SWMM stays the
primary build path and is exposed here via a thin adapter; MIKE+ is the first non-SWMM target;
InfoWorks ICM is a scaffold (issue #6).
"""
from swmmcanada.export.base import ExportResult, LossyMapping, ModelExporter
from swmmcanada.export.icm import IcmExporter
from swmmcanada.export.mikeplus import MikePlusExporter, export_mikeplus
from swmmcanada.export.swmm import SwmmExporter

__all__ = [
    "ModelExporter",
    "ExportResult",
    "LossyMapping",
    "SwmmExporter",
    "MikePlusExporter",
    "export_mikeplus",
    "IcmExporter",
]
