"""EPA SWMM exporter — SWMM behind the uniform export seam (ADR 0008).

SWMM `.inp` is the PRIMARY build path (ADR 0007); the pipeline still ships it via
``build_from_datastore``. This adapter merely wraps the existing ``build_model`` so SWMM,
MIKE+, ICM and HEC-RAS are peers to callers — it introduces no second `.inp` code path.
SWMM is the native format, so there are NO lossy mappings.

``systems=`` (ADR 0029 Q3 / ADR 0033 Q3) writes a per-system *view* of the same model
through the same writer: the HEC-RAS package needs a storm+combined ``.inp`` for RAS
Mapper's SWMM importer, and a filtered rebuild is how it gets one without a second writer.
"""
from __future__ import annotations

from pathlib import Path

from swmmcanada.build import build_model
from swmmcanada.build.models import filter_system_report
from swmmcanada.datastore import build_config_from_dict
from swmmcanada.export.base import ExportResult


class SwmmExporter:
    """Export the model-ready datastore to EPA SWMM 5.2 (``.inp`` + manifest)."""

    target = "swmm"

    def export(self, ds, out_dir, *, systems=None) -> ExportResult:
        config = build_config_from_dict(ds.config, out_dir)
        network, subcatchments, service_areas = ds.network, ds.subcatchments, ds.service_areas
        view: dict = {}
        warnings: list = []
        if systems is not None:
            network, view = filter_system_report(ds.network, systems)
            keep = {n.name for n in list(network.junctions) + list(network.outfalls)}
            # A surface catchment / service area whose node left the view leaves with it —
            # a model referencing a node it does not contain is not a model.
            subcatchments = [s for s in ds.subcatchments if s.outlet_node in keep]
            service_areas = [a for a in ds.service_areas if getattr(a, "node", None) in keep]
            dropped = len(ds.subcatchments) - len(subcatchments)
            if dropped:
                warnings.append(f"{dropped} subcatchment(s) drain to nodes outside the "
                                f"selected systems {sorted(view['systems'])}; omitted")
        res = build_model(
            network=network,
            subcatchments=subcatchments,
            service_areas=service_areas,
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
            warnings=warnings + list(res.warnings),
            view=view,
        )
