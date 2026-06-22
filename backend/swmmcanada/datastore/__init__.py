"""Model-ready datastore — the standardized intermediate layer between data-acquisition
and model-build (spec 11 / ADR 0003). Multi-carrier on disk: GeoPackage (network),
netCDF/CF (forcing), JSON (config + provenance)."""
from swmmcanada.datastore.core import (
    ModelReadyDatastore,
    build_from_datastore,
    read_datastore,
    write_datastore,
)

__all__ = [
    "ModelReadyDatastore",
    "write_datastore",
    "read_datastore",
    "build_from_datastore",
]
