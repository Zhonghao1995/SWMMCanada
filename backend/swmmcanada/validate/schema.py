"""Constants for the subcatchment validation layer (PRD: subcatchment validation).

One place that names every `validation.json` field, the severity values, the controlled
method vocabulary, and the default check thresholds — so the layer, the package, and any
reader agree by construction (mirrors `datastore/schema.py`).
"""

VALIDATION_VERSION = "1.0"
VALIDATION_JSON = "validation.json"          # package filename, beside model.inp

# --- severities (two tiers; see the Error/Warning policy in the PRD) ----------
ERROR = "error"        # structurally untrustworthy -> stop the .inp
WARNING = "warning"    # runs, but the user should know it's approximate

# --- controlled method vocabulary (honest labelling) --------------------------
# Produced today:
METHOD_CATCHBASIN_PARCEL = "catchbasin_parcel"      # real inlets, parcel-shaped (Victoria)
METHOD_CATCHBASIN_VORONOI = "catchbasin_voronoi"    # real inlets, Voronoi (Ottawa)
METHOD_JUNCTION_VORONOI = "junction_voronoi"        # synthesized / fallback
# Reserved for future methods (not produced yet):
METHOD_MUNICIPAL_POLYGON = "municipal_polygon"      # city-published catchment polygons
METHOD_CATCHBASIN_DEM = "catchbasin_dem"            # DEM-refined inlet service area

METHODS = frozenset({
    METHOD_CATCHBASIN_PARCEL, METHOD_CATCHBASIN_VORONOI, METHOD_JUNCTION_VORONOI,
    METHOD_MUNICIPAL_POLYGON, METHOD_CATCHBASIN_DEM,
})

CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})

# --- default check thresholds (fractions of area unless noted; arg-tunable) ---
OVERLAP_WARN_FRAC = 0.005      # >0.5% of total cell area overlapping -> warning
OVERLAP_ERROR_FRAC = 0.05      # >5% -> error (double-counted runoff)
AREA_CONSERVATION_TOL = 0.05   # |Σcells − AOI| / AOI > 5% -> warning
AOI_COVERAGE_WARN_FRAC = 0.02  # >2% of AOI uncovered (blank holes) -> warning
AOI_COVERAGE_ERROR_FRAC = 0.10 # >10% uncovered -> error
AOI_OUTSIDE_WARN_FRAC = 0.02   # >2% of cell area outside the AOI (aggregate) -> warning
CELL_OUTSIDE_ERROR_FRAC = 0.50 # a single cell >50% outside the AOI -> error
DISCARDED_AREA_WARN_FRAC = 0.01  # delineator threw away >1% of AOI -> warning

OUTLET_DIST_WARN_M = 20.0      # outlet 20–50 m from its cell -> warning tier
OUTLET_DIST_HIGH_M = 50.0      # outlet >50 m from its cell -> high-risk tier

SHAPE_AREA_OUTLIER_FACTOR = 20.0   # cell area >20× or <1/20× the median -> flag
SHAPE_THINNESS_MAX = 8.0           # perimeter² / (4π·area) above this -> elongated
