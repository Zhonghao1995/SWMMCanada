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
    """The REQUIRED paths absent from ``package_dir`` — empty list ⇔ shippable.
    F-019: a required path that exists but is a directory or a symlink counts as
    missing — the package must be made of plain files that live inside its root."""
    pkg = Path(package_dir).resolve()
    bad: List[str] = []
    for rel in REQUIRED:
        f = pkg / rel
        if (not f.exists() or f.is_symlink() or not f.is_file()
                or not f.resolve().is_relative_to(pkg)):
            bad.append(rel)
    return bad


def member_checksums(package_dir) -> dict:
    """SHA-256 + size for every regular file under the package root (F-019): the
    manifest's integrity block, so a shipped ZIP can be verified member by member."""
    import hashlib

    pkg = Path(package_dir).resolve()
    sums: dict = {}
    for f in sorted(pkg.rglob("*")):
        if f.is_symlink() or not f.is_file() or f.name == MANIFEST_JSON:
            continue
        rel = str(f.relative_to(pkg))
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        sums[rel] = {"sha256": h.hexdigest(), "bytes": f.stat().st_size}
    return sums


def record_checksums(package_dir) -> None:
    """Stamp ``member_checksums`` into manifest.json (call LAST, after every other
    stamp, so the sums cover the final artifact set)."""
    import json

    manifest = Path(package_dir) / MANIFEST_JSON
    data = json.loads(manifest.read_text()) if manifest.exists() else {}
    data["integrity"] = {"algorithm": "sha256", "members": member_checksums(package_dir)}
    manifest.write_text(json.dumps(data, indent=2))


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


def record_forcing(package_dir, forcing: dict) -> None:
    """Stamp the rainfall-forcing record (ADR 0014) into ``manifest.json`` beside the
    terrain block: which resolution the raingage got (hourly/daily), from which station,
    at what coverage — or why it fell back."""
    import json

    manifest = Path(package_dir) / MANIFEST_JSON
    data = json.loads(manifest.read_text()) if manifest.exists() else {}
    data["forcing"] = {k: v for k, v in forcing.items() if k != "mismatch_warning"}
    manifest.write_text(json.dumps(data, indent=2))
