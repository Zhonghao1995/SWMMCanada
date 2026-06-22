"""Error taxonomy for the acquire.* connectors."""


class AcquireError(Exception):
    """Base for all data-acquisition errors."""


class DemError(AcquireError):
    """Base for acquire.dem errors."""


class DegenerateBboxError(DemError):
    """AOI bbox is degenerate (min >= max on an axis)."""


class NoDemCoverageError(DemError):
    """No DEM source covers the AOI (MRDEM is national, so this should be rare)."""
