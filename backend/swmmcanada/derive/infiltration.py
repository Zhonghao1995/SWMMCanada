"""Infiltration parameter tables + USDA texture classifier (ADR 0013).

Every build derives ALL THREE SWMM infiltration parameter sets from the same soil data —
the datastore stores the superset and the writer picks the set the build's
``InfiltrationModel`` switch asks for:

  * SCS Curve Number  — dominant HSG -> ``SoilResult.hsg_to_cn`` (pre-existing path);
  * Horton            — dominant HSG -> :data:`HSG_HORTON` typical values;
  * Green-Ampt        — dominant USDA texture class -> :data:`GA_BY_TEXTURE`
                        (Rawls, Brakensiek & Miller 1983, Table 9), falling back to the
                        HSG's representative texture when the soil source publishes no
                        texture (HYSOGs raster / constant-HSG fallback).

All values are documented FIRST-PASS defaults — same epistemic tier as the HSG->CN table
and the snowmelt pack: standard published values, uncalibrated. Calibrate downstream.
"""
from typing import Dict, Optional, Tuple

# --- Horton by hydrologic soil group -----------------------------------------------------
# (f0 max rate mm/h, fc min rate mm/h, decay 1/h). fc from the Musgrave (1955) HSG ranges
# (EPA SWMM Hydrology Reference Manual), mid-range; f0 from the customary dry-soil urban
# tables (~2-5 in/h by group); decay 4.14 1/h is the classic Horton default. Dry time (7 d)
# stays the shared build default.
HSG_HORTON: Dict[str, Tuple[float, float, float]] = {
    "A": (127.0, 9.5, 4.14),
    "B": (101.6, 5.7, 4.14),
    "C": (76.2, 2.5, 4.14),
    "D": (50.8, 0.6, 4.14),
}

# --- Green-Ampt by USDA texture class ----------------------------------------------------
# (psi suction head mm, Ksat mm/h, IMD initial/max moisture deficit, fraction).
# Rawls, Brakensiek & Miller (1983) Table 9 (psi cm -> mm; Ksat cm/h -> mm/h then HALVED —
# the paper's own guidance for Green-Ampt use); IMD = effective porosity (dry-antecedent
# maximum deficit; SWMM tracks recovery from there). "silt" (rare) borrows silt loam.
GA_BY_TEXTURE: Dict[str, Tuple[float, float, float]] = {
    "sand": (49.5, 117.8, 0.417),
    "loamy sand": (61.3, 29.9, 0.401),
    "sandy loam": (110.1, 10.9, 0.412),
    "loam": (88.9, 6.6, 0.434),
    "silt loam": (166.8, 3.4, 0.486),
    "silt": (166.8, 3.4, 0.486),
    "sandy clay loam": (218.5, 1.5, 0.330),
    "clay loam": (208.8, 1.0, 0.390),
    "silty clay loam": (273.0, 1.0, 0.432),
    "sandy clay": (239.0, 0.6, 0.321),
    "silty clay": (292.2, 0.5, 0.423),
    "clay": (316.3, 0.3, 0.385),
}

# HSG -> representative texture, for soil sources that publish only HSG (HYSOGs, the
# constant fallback). Provenance records which tier produced the GA parameters.
HSG_REPRESENTATIVE_TEXTURE: Dict[str, str] = {
    "A": "loamy sand", "B": "loam", "C": "clay loam", "D": "clay",
}

# Raster code for each texture class (uint8; 0 = nodata). The SoilGrids source classifies
# per-pixel clay/sand into these codes; derive takes the majority code per subcatchment.
TEXTURE_CLASSES = tuple(GA_BY_TEXTURE)          # index+1 == raster code
TEXTURE_CODE: Dict[str, int] = {name: i + 1 for i, name in enumerate(TEXTURE_CLASSES)}
CODE_TEXTURE: Dict[int, str] = {v: k for k, v in TEXTURE_CODE.items()}


def usda_texture_class(clay_pct: float, sand_pct: float) -> str:
    """USDA soil-texture triangle, 12 classes, from clay% + sand% (silt = remainder).
    Standard boundary rules; anything degenerate falls back to loam."""
    clay, sand = float(clay_pct), float(sand_pct)
    silt = 100.0 - clay - sand
    if clay < 0 or sand < 0 or silt < -0.5:
        return "loam"
    silt = max(silt, 0.0)

    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 2.0 * clay < 30:
        return "loamy sand"
    if clay >= 40:
        if sand > 45:
            return "sandy clay"
        if silt >= 40:
            return "silty clay"
        return "clay"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 27:
        if sand <= 20:
            return "silty clay loam"
        if sand <= 45:
            return "clay loam"
        return "sandy clay loam"          # clay 27-35, sand > 45
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if silt >= 50:
        if clay >= 12 or silt < 80:
            return "silt loam"
        return "silt"
    if clay >= 7 and sand > 52:
        return "sandy loam"
    if clay < 7 and silt < 50:
        return "sandy loam"
    return "loam"


def horton_for_hsg(letter: Optional[str]) -> Tuple[float, float, float]:
    """(f0, fc, decay) for an HSG letter; unknown/None -> the B row (loamy default)."""
    return HSG_HORTON.get(letter or "B", HSG_HORTON["B"])


def green_ampt_for_texture(texture: Optional[str]) -> Tuple[float, float, float]:
    """(psi_mm, ksat_mm_h, imd) for a USDA texture class; unknown/None -> loam."""
    return GA_BY_TEXTURE.get(texture or "loam", GA_BY_TEXTURE["loam"])


def green_ampt_for_hsg(letter: Optional[str]) -> Tuple[float, float, float]:
    """GA parameters via the HSG's representative texture (the no-texture fallback tier)."""
    return green_ampt_for_texture(HSG_REPRESENTATIVE_TEXTURE.get(letter or "B", "loam"))
