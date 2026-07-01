"""InfoWorks ICM exporter — SCAFFOLD (issue #6, not yet implemented).

The third target behind the model-export interface, after SWMM and MIKE+. The plumbing is in
place so ICM slots in as one more ``ModelExporter``; the field mapping and the importable
package are deferred to issue #6 — and should follow the pattern confirmed by the MIKE+ import
verification rather than be guessed ahead of it. Deliberately NOT wired into the build yet.
"""
from __future__ import annotations

from swmmcanada.export.base import ExportResult


class IcmExporter:
    """Innovyze / Autodesk InfoWorks ICM exporter — scaffold; see issue #6.

    Planned: consume the model-ready datastore and emit an ICM-importable package
    (nodes / links / subcatchments + rainfall) with its own field-mapping sheet and a
    lossy report, mirroring the MIKE+ writer once the MIKE+ import path is confirmed.
    """

    target = "icm"

    def export(self, ds, out_dir) -> ExportResult:
        raise NotImplementedError(
            "InfoWorks ICM exporter is a scaffold — the field mapping and import package "
            "are tracked in issue #6 (do it after the MIKE+ import is verified)."
        )
