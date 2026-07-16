"""On-disk schema constants for the model-ready datastore (spec 11 / ADR 0003).

One place that names every carrier file, GeoPackage layer, attribute column, and netCDF
variable, so `core.write_datastore` and `core.read_datastore` agree by construction. The
datastore is multi-carrier on purpose:

  * ``network.gpkg``  — GeoPackage, the spatial/network structure (4 layers, EPSG:4326).
  * ``forcing.nc``    — netCDF / CF-1.8, the rainfall forcing timeseries.
  * ``datastore.json``— config + provenance + the carrier file list (the citable header).
"""

# 1.1 (2026-07-16): conduits gained inlet/outlet offsets + shape/height/width (#130),
# forcing.nc gained tide_level with datum/clock attrs (#130/ADR 0024). Readers accept
# 1.0 packages (missing columns/vars read back as the pre-#130 defaults).
DATASTORE_VERSION = "1.1"

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
JUNCTION_FIELDS = ["name", "invert_m", "max_depth_m", "system"]
OUTFALL_FIELDS = ["name", "invert_m", "kind", "system"]
CONDUIT_FIELDS = [
    "name", "from_node", "to_node", "length_m", "diameter_m", "roughness_n", "system",
    # 1.1 (#130): drop-structure offsets + real cross-sections
    "inlet_offset_m", "outlet_offset_m", "shape", "height_m", "width_m",
]
SUBCATCHMENT_FIELDS = [
    "name", "outlet_node", "area_ha", "pct_imperv", "width_m", "pct_slope", "cn",
    "n_imperv", "n_perv", "s_imperv_mm", "s_perv_mm", "pct_zero", "system",
    # Infiltration superset (ADR 0013) — all three methods' parameters ride along so
    # switching method is a re-export, not a rebuild.
    "horton_f0_mm_h", "horton_fc_mm_h", "horton_decay_1_h",
    "ga_psi_mm", "ga_ksat_mm_h", "ga_imd",
]

# netCDF (CF) names. Rainfall, temperature, and evaporation are the climate-forcing triad
# (CONTEXT glossary). Each carries its own time coordinate: evaporation/temperature are
# derived per-day and may drop days the raingage keeps (missing temps), so they are not
# assumed to share the rain axis.
CF_CONVENTIONS = "CF-1.8"
TIME_DIM = "time"
PRECIP_VAR = "precipitation"
PRECIP_UNITS = "mm"
EVAP_VAR = "evaporation"
EVAP_UNITS = "mm day-1"
EVAP_TIME_DIM = "evap_time"
TEMP_VAR = "temperature"
TIDE_VAR = "tide_level"
TIDE_TIME_DIM = "tide_time"
TIDE_UNITS = "m"
TEMP_UNITS = "degC"
TEMP_TIME_DIM = "temp_time"
