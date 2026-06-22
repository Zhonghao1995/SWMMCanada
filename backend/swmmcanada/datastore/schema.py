"""On-disk schema constants for the model-ready datastore (spec 11 / ADR 0003).

One place that names every carrier file, GeoPackage layer, attribute column, and netCDF
variable, so `core.write_datastore` and `core.read_datastore` agree by construction. The
datastore is multi-carrier on purpose:

  * ``network.gpkg``  — GeoPackage, the spatial/network structure (4 layers, EPSG:4326).
  * ``forcing.nc``    — netCDF / CF-1.8, the rainfall forcing timeseries.
  * ``datastore.json``— config + provenance + the carrier file list (the citable header).
"""

DATASTORE_VERSION = "1.0"

# Carrier files (relative to the datastore directory).
NETWORK_GPKG = "network.gpkg"
FORCING_NC = "forcing.nc"
DATASTORE_JSON = "datastore.json"

# The two binary carriers listed in datastore.json["files"]; datastore.json itself is the
# header and is intentionally not listed among the data files.
DATA_FILES = [NETWORK_GPKG, FORCING_NC]

# All coordinates in the datastore are stored in this CRS (lon/lat), per the repo contract.
CRS = "EPSG:4326"

# GeoPackage layer names.
LAYER_JUNCTIONS = "junctions"
LAYER_OUTFALLS = "outfalls"
LAYER_CONDUITS = "conduits"
LAYER_SUBCATCHMENTS = "subcatchments"

# Non-geometry attribute columns per layer (geometry is carried separately by the GPKG).
JUNCTION_FIELDS = ["name", "invert_m", "max_depth_m"]
OUTFALL_FIELDS = ["name", "invert_m", "kind"]
CONDUIT_FIELDS = ["name", "from_node", "to_node", "length_m", "diameter_m", "roughness_n"]
SUBCATCHMENT_FIELDS = [
    "name", "outlet_node", "area_ha", "pct_imperv", "width_m", "pct_slope", "cn",
    "n_imperv", "n_perv", "s_imperv_mm", "s_perv_mm", "pct_zero",
]

# netCDF (CF) names.
CF_CONVENTIONS = "CF-1.8"
TIME_DIM = "time"
PRECIP_VAR = "precipitation"
PRECIP_UNITS = "mm"
