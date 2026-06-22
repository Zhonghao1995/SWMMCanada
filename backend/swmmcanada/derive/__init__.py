"""derive (spec 07): zonal-statistic aggregation of acquired DEM / land-cover / soil
rasters within each subcatchment polygon into real SWMM subcatchment parameters
(`pct_imperv`, `cn`, `pct_slope`), overwriting `network`'s placeholders.

Pure computation, offline by construction — no network I/O. See `core.derive_parameters`.
"""
from swmmcanada.derive.core import DeriveError, derive_parameters

__all__ = ["derive_parameters", "DeriveError"]
