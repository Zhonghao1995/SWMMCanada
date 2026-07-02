"""City of Regina storm-sewer open data -> SWMM ``NetworkIn`` (geometry-inferred topology).

Regina (opengis.regina.ca, catalogued at open.regina.ca) publishes invert elevations
(STARTELEVATION/ENDELEVATION, doubles, m AMSL ~570+), DIAMETER (integer, mm), MATERIAL and
SURVEYLENGTH (m) on its Storm Sewer Line layer, but **no node ids** — so, like Ottawa/Kelowna,
topology is inferred from pipe polyline endpoints by ``cities.base`` (coordinate snapping at
~1 m). Only ``STATUS = 'ACTIVE'`` lines that are not force mains are fetched: ABANDONED /
CUT OFF / NOT IN USE lines and the pressurized ``Force`` subtype are not part of the gravity
storm graph (Main/Trunk/Drain/Culvert/RetentionTank are). Outfalls come from the Outfall point
layer (455 city-wide, along Wascana Creek and the storm channels).

Regina DOES publish parcels (the ``Parcels`` service's ASSESSMENT_REGIONS layer is lot-level
assessment parcels — ~73k polygons with ACCOUNT_NUMBER/APN) and a BUILDING FOOTPRINT polygon
layer, so subcatchment delineation can use real lot lines + roofs (ADR 0005). Catch basins
(layer 3, with RIMELEVATION/SUMPELEVATION) seed the subcatchments.

The city's own ArcGIS Server (10.91) serves real geometry for ``f=geojson`` and supports
pagination (maxRecordCount 1000). All endpoints verified live 2026-07-02 (see
``tests/fixtures/regina/README.md``).
"""
from swmmcanada.sources.cities import base

ARC = "https://opengis.regina.ca/arcgis/rest/services/OpenData"
STORM = f"{ARC}/StormSewerNetwork/MapServer"
STORM_PIPES = 5        # Storm Sewer Line (polyline); STARTELEVATION/ENDELEVATION, DIAMETER (mm)
STORM_OUTFALLS = 4     # Outfall (point)
STORM_CATCHBASINS = 3  # Catch Basin (point); RIMELEVATION, SUMPELEVATION
STORM_MANHOLES = 2     # Manhole (point); RIMELEVATION (~88% populated) -> node ground/max-depth
DOMESTIC = f"{ARC}/DomesticSewerNetwork/MapServer"
DOMESTIC_PIPES = 3     # Domestic Sewer Line — same schema as storm (inverts/diameter/material)
# Parcels + building footprints live on their own OpenData services.
PARCELS = f"{ARC}/Parcels/MapServer/0"                  # ASSESSMENT_REGIONS (lot polygons)
BUILDINGS = f"{ARC}/BuildingFootprint/MapServer/0"      # BUILDING FOOTPRINT (polygons)

REGINA_CRS = "EPSG:32613"  # UTM 13N (metric ops)
_PAGE = 1000               # layer maxRecordCount

# Gravity storm graph only: drop abandoned/cut-off/not-in-use lines and pressurized force mains
# (SUBTYPENAME is never null in Regina's data, so the inequality is safe).
_PIPES_WHERE = "STATUS = 'ACTIVE' AND SUBTYPENAME <> 'Force'"


# Shared ArcGIS client + Esri-JSON->GeoJSON converter live in cities.base (Phase 0).
ReginaClient = base.ArcGISClient


def _fetch(url, bbox, client, where="1=1") -> list:
    """Paginated bbox query returning GeoJSON Features. Regina's ArcGIS Server serves real
    geometry under ``f=geojson`` (verified 2026-07-02); ``_as_geojson`` converts any layer that
    ever falls back to Esri JSON (``attributes``/``paths`` shaped) so the adapter is robust."""
    return base.fetch_paged(client, f"{url}/query", bbox,
                            where=where, page_size=_PAGE, transform=_as_geojson)


def _as_geojson(feat: dict) -> dict:
    """Pass GeoJSON Features through unchanged; convert Esri-JSON features (``attributes``)."""
    if "attributes" in feat and "properties" not in feat:
        return base.esri_to_geojson(feat)
    return feat


def fetch_regina_storm(bbox, *, client=None) -> dict:
    """Storm network intersecting ``bbox`` (EPSG:4326 tuple, or object with ``.bbox``): active
    gravity Storm Sewer Lines + Outfalls + Manholes (rim elevations for node depths).
    Returns ``{"pipes": [...], "outfalls": [...], "manholes": [...]}``."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ReginaClient()
    return {
        "pipes": _fetch(f"{STORM}/{STORM_PIPES}", bbox, client, where=_PIPES_WHERE),
        "outfalls": _fetch(f"{STORM}/{STORM_OUTFALLS}", bbox, client),
        "manholes": _fetch(f"{STORM}/{STORM_MANHOLES}", bbox, client),
    }


def fetch_regina_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary (domestic) sewer lines intersecting ``bbox`` — the second tagged
    system (ADR 0011). Same publication schema as the storm layer, so
    :func:`build_regina_network` assembles it unchanged (per-component sinks stand in for
    the treatment-bound trunk exits)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ReginaClient()
    return {"pipes": _fetch(f"{DOMESTIC}/{DOMESTIC_PIPES}", bbox, client, where=_PIPES_WHERE)}


def fetch_regina_land(bbox, *, client=None) -> dict:
    """Catch basins + land units for the parcel/building subcatchment method (ADR 0005):
    ``{"catchbasins", "parcels", "buildings"}``. Regina publishes both lot-level parcels
    (ASSESSMENT_REGIONS) and building footprints."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or ReginaClient()
    return {
        "catchbasins": _fetch(f"{STORM}/{STORM_CATCHBASINS}", bbox, client),
        "parcels": _fetch(PARCELS, bbox, client),
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


def _num(v):
    """Float or None; ``""``/None/0/non-numeric count as missing. Regina's inverts are real
    elevations (~570+ m AMSL) and DIAMETER/SURVEYLENGTH are never legitimately zero, so 0 is a
    missing-data sentinel here (mirrors Kelowna/Calgary)."""
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


# Plausible invert band for Regina (m AMSL): the city's terrain sits ~545–595 m. The data has
# rare recording errors ("1.0" placeholders, a "57.23" dropped-digit typo — 5 of ~15,800 active
# lines city-wide); an invert outside the band is treated as missing so the node gap-fills from
# its neighbours instead of the error poisoning the min-invert node logic.
_INVERT_MIN, _INVERT_MAX = 500.0, 700.0


def _invert(v):
    """STARTELEVATION/ENDELEVATION -> float m AMSL, or None when missing OR implausible."""
    f = _num(v)
    return f if (f is not None and _INVERT_MIN <= f <= _INVERT_MAX) else None


def _regina_roughness(material, default):
    """Manning's n for a Regina material code. Regina uses PVC variants ("PVC RIBBED",
    "PVC SDR35", "PVC FLEXLOC", "PVC PERMALOC"), corrugated-metal long forms ("CORREGATED
    GALVANIZED STEEL" — spelling as published), POLY variants, and RCP/VCT/TILE/PRELOAD that the
    shared table doesn't list; normalise those to their base material so they resolve to the
    right pipe roughness instead of silently falling back to the default."""
    if not material:
        return default
    code = str(material).strip().upper()
    if code.startswith("PVC"):
        code = "PVC"                       # PVC RIBBED / SDR35 / FLEXLOC / PERMALOC -> PVC
    elif code.startswith("CORREGATED"):
        code = "CSP"                       # corrugated (galvanized/aluminum) steel pipe
    elif "POLY" in code:
        code = "PE"                        # POLY / POLY B / PERFORATED POLY -> polyethylene
    code = {"RCP": "CONC", "VCT": "VITC", "TILE": "VITC", "PRELOAD": "CONC"}.get(code, code)
    return base.material_roughness(code, default)


def _line_ends(geom):
    coords = (geom or {}).get("coordinates") or []
    if not coords:
        return None, None
    if isinstance(coords[0][0], (list, tuple)):   # MultiLineString -> flatten parts
        coords = [pt for part in coords for pt in part]
    if len(coords) < 2:
        return None, None
    return tuple(coords[0][:2]), tuple(coords[-1][:2])


# Regina has no node ids, so topology is snapped from polyline endpoints: a coarser tolerance
# (~1 m, snap_decimals=5) connects endpoints that don't perfectly coincide, avoiding spurious
# fragmentation — same choice as Ottawa/Calgary/Kelowna.
_REGINA_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    """Normalise a layer to a plain list of GeoJSON Features, accepting either a bare list or a
    ``{"type": "FeatureCollection", "features": [...]}`` dict."""
    if layer is None:
        return []
    if isinstance(layer, dict):
        return list(layer.get("features") or [])
    return list(layer)


def build_regina_network(storm, *, config: base.AssembleConfig = _REGINA_ASSEMBLE) -> base.NetworkResult:
    if isinstance(storm, dict) and ("pipes" in storm or "outfalls" in storm):
        pipes_f = _features(storm.get("pipes"))
        outfalls_f = _features(storm.get("outfalls"))
    else:                                   # a bare pipes list / FeatureCollection, no outfalls
        pipes_f = _features(storm)
        outfalls_f = []

    pipes, seen, n_no_geom = [], {}, 0
    for f in pipes_f:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("GISID") or p.get("OBJECTID") or "P")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:                       # ensure unique conduit names
            name = f"{name}_{len(pipes)}"
        dia_mm = _num(p.get("DIAMETER"))         # integer mm
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_invert(p.get("STARTELEVATION")), inv_b=_invert(p.get("ENDELEVATION")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=_regina_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=_num(p.get("SURVEYLENGTH")),
        ))

    outfall_points = []
    for f in outfalls_f:
        c = (f.get("geometry") or {}).get("coordinates")
        if c and len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    # Manhole RIMELEVATION (~88% populated) -> node ground elevation, so max depth becomes
    # rim - invert instead of the 2 m default. The same plausibility band screens rims as
    # inverts (a bad rim would poison every depth on that node).
    ground_points = []
    manholes_f = _features(storm.get("manholes")) if isinstance(storm, dict) else []
    for f in manholes_f:
        c = (f.get("geometry") or {}).get("coordinates")
        rim = _invert((f.get("properties") or {}).get("RIMELEVATION"))
        if c and len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    result = base.assemble_network(pipes, outfall_points=outfall_points,
                                   ground_points=ground_points, config=config)
    diag = {**result.diagnostics, "city": "regina", "n_pipes_in": len(pipes_f),
            "n_no_geom": n_no_geom, "n_outfall_points": len(outfall_points),
            "n_ground_points": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
