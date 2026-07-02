"""Result-package contract (ADR 0009): ONE place that names every path in the package a
build ships — the hand-off artifact aiswmm and users consume.

Mirrors the ``datastore/schema.py`` convention: constants, so writers (pipeline) and
shippers (api/tasks) agree by construction. ``mikeplus/`` is optional BY DESIGN — ADR 0008's
graceful degradation means a failed MIKE+ export must never fail the package."""
from pathlib import Path
from typing import List

from swmmcanada.datastore import schema as ds_schema
from swmmcanada.validate import schema as v_schema

MODEL_INP = "model.inp"
MANIFEST_JSON = "manifest.json"
VALIDATION_JSON = v_schema.VALIDATION_JSON
DATASTORE_DIR = "datastore"
PREVIEW_DIR = "preview"
PREVIEW_GEOJSON = f"{PREVIEW_DIR}/network.geojson"
MIKEPLUS_DIR = "mikeplus"          # optional: ADR 0008 graceful degradation

# Paths (relative to the package root) without which the package is NOT shippable.
REQUIRED: List[str] = [
    MODEL_INP,
    MANIFEST_JSON,
    VALIDATION_JSON,
    f"{DATASTORE_DIR}/{ds_schema.NETWORK_GPKG}",
    f"{DATASTORE_DIR}/{ds_schema.FORCING_NC}",
    f"{DATASTORE_DIR}/{ds_schema.DATASTORE_JSON}",
    PREVIEW_GEOJSON,
]


def missing_required(package_dir) -> List[str]:
    """The REQUIRED paths absent from ``package_dir`` — empty list ⇔ shippable."""
    pkg = Path(package_dir)
    return [rel for rel in REQUIRED if not (pkg / rel).exists()]
