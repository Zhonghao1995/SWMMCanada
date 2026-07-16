"""EPA SWMM exporter — SWMM behind the uniform export seam (ADR 0008).

SWMM `.inp` is the PRIMARY build path (ADR 0007); the pipeline still ships it via
``build_from_datastore``. This adapter merely wraps the existing ``build_model`` so SWMM,
MIKE+ and (later) ICM are peers to callers — it introduces no second `.inp` code path.
SWMM is the native format, so there are NO lossy mappings.
"""
from __future__ import annotations

from pathlib import Path

from swmmcanada.build import build_model
from swmmcanada.datastore import build_config_from_dict
from swmmcanada.export.base import ExportResult


class SwmmExporter:
    """Export the model-ready datastore to EPA SWMM 5.2 (``.inp`` + manifest)."""

    target = "swmm"

    def export(self, ds, out_dir) -> ExportResult:
        config = build_config_from_dict(ds.config, out_dir)
        res = build_model(
            network=ds.network,
            subcatchments=ds.subcatchments,
            rain=ds.rain,
            config=config,
            evaporation=ds.evaporation,
            temperature=ds.temperature,
            tide=ds.tide,
        )
        return ExportResult(
            target="swmm",
            out_dir=Path(out_dir),
            files=[res.inp_path, res.manifest_path],
            lossy=[],
            warnings=list(res.warnings),
        )
