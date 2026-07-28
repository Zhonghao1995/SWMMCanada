"""District of North Vancouver storm/sanitary open data -> SWMM ``NetworkIn``
(download-and-cache SHP dumps, tier-2 endpoint snapping — the wave's final city).

The District (geoweb.dnv.org) publishes token-free static SHP zips under
``/Products/Data/SHP/`` — actively refreshed (the dump carried a same-week Last-Modified
during scouting). ``StmMain_shp.zip`` (19,839 mains, EPSG:26910) carries ``UP_ELEV``/
``DN_ELEV`` per-end inverts AS STRINGS with the **-99** sentinel (44% real on the Lynn
Valley fixture), ``AM_SIZE`` string mm and spelled-out ``AM_MATERIA``; no node ids.
``SanMain_shp.zip`` mirrors for the ADR 0011 tracer. The fitting dump publishes structure
inverts but no rims, and its type domain arrives as bare codes in the SHP — so no seeds
are consumed (junction delineation, Windsor precedent) and depths keep the default.

Elevation semantics (per the #157 convention — verified 2026-07-28 on the live dump):
  * ``UP_ELEV``/``DN_ELEV`` = pipe-end INVERTS (string-typed), m AMSL; **-99 = missing**
    (genuine near-zero values exist along the Burrard shore, so the screen is > -90).
"""
import json

from swmmcanada.sources import _download
from swmmcanada.sources.cities import base

SHP_BASE = "https://geoweb.dnv.org/Products/Data/SHP"

NORTHVAN_CRS = "EPSG:32610"  # UTM 10N (metric ops; dumps ship EPSG:26910)


def _load_zip(name: str, bbox) -> list:
    import geopandas as gpd

    path = _download.fetch_file(f"{SHP_BASE}/{name}_shp.zip", cache_name=f"dnv_{name}.zip")
    gdf = gpd.read_file(f"zip://{path}").to_crs(4326)
    min_lon, min_lat, max_lon, max_lat = bbox
    clip = gdf.cx[min_lon:max_lon, min_lat:max_lat]
    keep = ["ASSET_ID", "AM_SIZE", "AM_MATERIA", "UP_ELEV", "DN_ELEV", "ASB_SLOPE",
            "geometry"]
    clip = clip[[c for c in keep if c in clip.columns]]
    return json.loads(clip.to_json())["features"]


def fetch_northvandistrict_storm(bbox, *, client=None) -> dict:
    """``client`` accepted for registry symmetry; the source is a file download."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    return {"mains": _load_zip("StmMain", bbox)}


def fetch_northvandistrict_sanitary(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    return {"mains": _load_zip("SanMain", bbox)}


def fetch_northvandistrict_land(bbox, *, client=None) -> dict:
    """No seeds consumed (the fitting dump's type domain is bare codes) — junction
    delineation; CadParcels/BldBuilding zips exist as future enrichment."""
    return {"catchbasins": [], "parcels": [], "buildings": []}


def _elev(v):
    """String elevation -> float m AMSL, or None (-99 = missing; > -90 screen keeps the
    genuine near-zero shore values)."""
    f = base.num(v)
    return f if (f is not None and f > -90.0) else None


_line_ends = base.line_ends

_NORTHVAN_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer or [])


def build_northvandistrict_network(data, *, config: base.AssembleConfig = _NORTHVAN_ASSEMBLE) -> base.NetworkResult:
    """Canonical pipes (string sizes/elevations parsed; no node ids)."""
    mains = _features((data or {}).get("mains") if isinstance(data, dict) else data)

    pipes = []
    n_no_geom = 0
    seen_names: dict = {}
    for f in mains:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("ASSET_ID") or f.get("id"))
        seen_names[name] = seen_names.get(name, 0) + 1
        if seen_names[name] > 1:
            name = f"{name}_{seen_names[name]}"
        dia_mm = base.num(p.get("AM_SIZE"), zero_missing=True)
        material = str(p.get("AM_MATERIA") or "")
        material = {"NON REINF CONC": "CONC", "REINF CONC": "CONC"}.get(material.upper(), material)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_elev(p.get("UP_ELEV")), inv_b=_elev(p.get("DN_ELEV")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=base.material_roughness(material, config.default_roughness),
        ))

    result = base.assemble_network(pipes, config=config)
    diag = {**result.diagnostics, "city": "northvandistrict", "n_mains_in": len(mains),
            "n_no_geom": n_no_geom}
    return base.NetworkResult(network=result.network, diagnostics=diag)
