import os

# Maximum AOI ground area (km², measured in the equal-area ESRI:102001 CRS).
# Decision 2026-06-02: 25 km² (typical AOI is a few km²). Env-configurable.
MAX_AOI_KM2 = float(os.environ.get("SWMMCANADA_MAX_AOI_KM2", "25"))
