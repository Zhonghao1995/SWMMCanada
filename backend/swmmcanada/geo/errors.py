"""Error taxonomy for the geo module (spec 01 §5). All subclass GeoError."""


class GeoError(Exception):
    """Base class for all geo errors."""


class AOIEmptyError(GeoError):
    """AOI is empty or has zero area."""


class AOIGeometryTypeError(GeoError):
    """AOI input is not a (Multi)Polygon (e.g. a Point or LineString)."""


class AOIInvalidGeometryError(GeoError):
    """AOI geometry could not be repaired into a valid polygon."""


class AOICRSUnsupportedError(GeoError):
    """GeoJSON carried a non-WGS84 CRS member; GeoJSON must be EPSG:4326 lon/lat."""


class AOICRSUnknownError(GeoError):
    """Uploaded shapefile has no CRS (.prj); refuse to guess."""


class AOIOversizeError(GeoError):
    """AOI ground area exceeds MAX_AOI_KM2."""


class AOIOutsideCanadaError(GeoError):
    """AOI falls outside Canada; the product is Canada-only."""


class ShapefileIncompleteError(GeoError):
    """Uploaded zip is missing required shapefile sidecars (.shp/.shx/.dbf)."""
