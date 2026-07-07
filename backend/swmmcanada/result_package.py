"""Result-package contract (ADR 0009): ONE place that names every path in the package a
build ships — the hand-off artifact aiswmm and users consume.

Mirrors the ``datastore/schema.py`` convention: constants, so writers (pipeline) and
shippers (api/tasks) agree by construction. ``mikeplus/`` and ``icm/`` are optional BY
DESIGN — ADR 0008/0012 graceful degradation: a failed secondary export never fails the package."""
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
ICM_DIR = "icm"                    # optional: ADR 0012, same graceful degradation
# The 2D-overland raw materials: clipped terrain (LiDAR where covered) + land cover for
# roughness zoning. Promised deliverables, not workspace leftovers — an engineer meshing
# a 2D model in ICM/MIKE+ gets terrain, roughness zones, network + rim elevations and the
# boundary from ONE package. Source/resolution are recorded in manifest.json ("terrain").
DEM_DTM = "dem_dtm.tif"
LANDCOVER = "landcover.tif"

# Paths (relative to the package root) without which the package is NOT shippable.
REQUIRED: List[str] = [
    MODEL_INP,
    MANIFEST_JSON,
    VALIDATION_JSON,
    f"{DATASTORE_DIR}/{ds_schema.NETWORK_GPKG}",
    f"{DATASTORE_DIR}/{ds_schema.FORCING_NC}",
    f"{DATASTORE_DIR}/{ds_schema.DATASTORE_JSON}",
    PREVIEW_GEOJSON,
    DEM_DTM,
    LANDCOVER,
]


def missing_required(package_dir) -> List[str]:
    """The REQUIRED paths absent from ``package_dir`` — empty list ⇔ shippable."""
    pkg = Path(package_dir)
    return [rel for rel in REQUIRED if not (pkg / rel).exists()]


def record_terrain(package_dir, *, source: str, resolution_m: float, coverage: str) -> None:
    """Stamp the 2D-overland terrain metadata into ``manifest.json`` — the first question an
    engineer meshing a 2D model asks is "is this 1 m LiDAR or the 30 m national model?"."""
    import json

    manifest = Path(package_dir) / MANIFEST_JSON
    data = json.loads(manifest.read_text()) if manifest.exists() else {}
    data["terrain"] = {
        "dem": DEM_DTM,
        "source": source,
        "resolution_m": resolution_m,
        "coverage": coverage,
        "landcover": LANDCOVER,
        "note": "2D-overland raw materials: mesh the DEM, zone roughness from the land "
                "cover, couple at the network's manholes (rim/ground elevations included).",
    }
    manifest.write_text(json.dumps(data, indent=2))
