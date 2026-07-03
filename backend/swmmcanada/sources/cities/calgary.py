"""City of Calgary storm-sewer open data -> SWMM ``NetworkIn`` (geometry-inferred topology).

Calgary publishes inverts (UP_INVERT/DN_INVERT, m AMSL), HEIGHT/WIDTH (mm; equal -> circular
diameter), MATERIAL, LENGTH and SLOPE on its STORM_PIPE layer, but **no node ids** — so, like
Ottawa, topology is inferred from pipe polyline endpoints by ``cities.base`` (coordinate
snapping at ~1 m). Outfalls come from the Inlet/Outfall layer: a feature is an outfall when its
``OUT_INLET`` names a receiving water body (e.g. "BOW RIVER") — i.e. ``OUT_INLET`` is non-null —
or its ``S_FUNCTION`` contains "OUTFALL"; inlets have a null ``OUT_INLET``.

Unlike Ottawa, Calgary DOES publish polygon parcels and buildings, so ``fetch_calgary_land``
returns them for the ADR 0005 parcel/building subcatchment method:
  * parcels   = ``Parcel_with_Roll_2026`` (ROLL_CPID_2026, full-coverage parcel polygons;
                NOT ``Parcel_Assessment``, which is a polyline display layer);
  * buildings = ``Buildings_from_Digital_Aerial_Survey`` (DAS_BUILDING polygons).

The hosted FeatureServer serves real GeoJSON for ``f=geojson`` (geometry populated), so we fetch
with ``f=geojson`` directly; ``base.esri_to_geojson`` is still applied defensively for any layer
that falls back to Esri JSON (``attributes``/``paths`` shaped) so the adapter is robust either way.
All endpoints verified live 2026-06-22 (see ``tests/fixtures/calgary/README.md``).
"""
from swmmcanada.sources.cities import base

ORG = "https://services1.arcgis.com/AVP60cs0Q9PEA8rH/arcgis/rest/services"
STORM_PIPES = "Storm_pipe_DMAP"               # layer STORM_PIPE (polyline)
STORM_INLET_OUTFALL = "Storm_Inlet_Outfall_DMAP"  # OUT_INLET names water body for outfalls
STORM_MANHOLES = "Storm_Manholes_DMAP"        # RIM_ELEV (m AMSL) -> node ground/max-depth
STORM_CATCHBASINS = "Storm_catch_basin_DMAP"  # ASSET_ID
SANITARY_PIPES = "Sanitary_pipes_DMAP"        # layer SANITARY_PIPE — same invert/size schema
PARCELS = "Parcel_with_Roll_2026"             # ROLL_CPID_2026 polygons (full coverage)
BUILDINGS = "Buildings_from_Digital_Aerial_Survey"  # DAS_BUILDING polygons
CALGARY_CRS = "EPSG:32611"                    # UTM 11N (metric ops)
_PAGE = 2000                                  # hosted FeatureServer maxRecordCount


# Shared ArcGIS client + Esri-JSON->GeoJSON converter live in cities.base (Phase 0).
CalgaryClient = base.ArcGISClient


def _as_geojson(feat: dict) -> dict:
    """Normalise a feature to GeoJSON. ``f=geojson`` already returns GeoJSON Features
    (``properties`` + ``geometry`` with ``coordinates``); an Esri-JSON fallback (``attributes``
    + ``paths``/``x,y``/``rings``) is converted with ``base.esri_to_geojson``."""
    geom = feat.get("geometry") or {}
    if "attributes" in feat or any(k in geom for k in ("paths", "rings", "x")):
        return base.esri_to_geojson(feat)
    return feat


def _fetch(service, bbox, client, where="1=1") -> list:
    """Paginated bbox query against a hosted FeatureServer layer, features normalised to
    GeoJSON (``_as_geojson`` handles any Esri-JSON fallback)."""
    return base.fetch_paged(client, f"{ORG}/{service}/FeatureServer/0/query", bbox,
                            where=where, page_size=_PAGE, transform=_as_geojson)


# A feature is an outfall when OUT_INLET names a receiving water body (non-null) OR S_FUNCTION
# says OUTFALL. Inlets carry a null OUT_INLET.
_OUTFALL_WHERE = "OUT_INLET IS NOT NULL"


def fetch_calgary_storm(bbox, *, client=None) -> dict:
    """Storm network intersecting ``bbox``: pipes + outfalls + manholes (RIM_ELEV for node
    ground/max-depth). Returns ``{"pipes": [...], "outfalls": [...], "manholes": [...]}``."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or CalgaryClient()
    return {
        "pipes": _fetch(STORM_PIPES, bbox, client),
        "outfalls": _fetch(STORM_INLET_OUTFALL, bbox, client, where=_OUTFALL_WHERE),
        "manholes": _fetch(STORM_MANHOLES, bbox, client),
    }


# Gravity sanitary graph only: MAIN (local/collector) + TL (trunk) carry the routable gravity
# skeleton; FM (force mains), SYP (syphons), SL / "C/MF SERV" (service laterals), DCT and
# SUBDRAIN are not part of it. STATUS_IND drops INACTIVE lines (the ACTIVE field is unpopulated).
_SANITARY_WHERE = "STATUS_IND = 'ACTIVE' AND P_FUNCTION IN ('MAIN', 'TL')"


def fetch_calgary_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary sewer lines intersecting ``bbox`` — the second tagged system
    (ADR 0011). SANITARY_PIPE shares STORM_PIPE's invert/size schema (UP_INVERT/DN_INVERT,
    WIDTH/HEIGHT mm, MATERIAL, LENGTH), so :func:`build_calgary_network` assembles it
    unchanged (per-component sinks stand in for the treatment-bound trunk exits)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or CalgaryClient()
    return {"pipes": _fetch(SANITARY_PIPES, bbox, client, where=_SANITARY_WHERE)}


def fetch_calgary_land(bbox, *, client=None) -> dict:
    """Catch basins + parcels + buildings for the ADR 0005 parcel/building subcatchment method.
    Calgary publishes real polygon parcels (``Parcel_with_Roll_2026``) and buildings
    (``Buildings_from_Digital_Aerial_Survey``); if either query yields nothing, its key is ``[]``."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or CalgaryClient()
    return {
        "catchbasins": _fetch(STORM_CATCHBASINS, bbox, client),
        "parcels": _fetch(PARCELS, bbox, client),
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


def _num(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # 0 is Calgary's missing-data sentinel for inverts/length (real inverts are ~1040 m AMSL),
    # mirroring Ottawa. Treat it as missing so node inverts gap-fill instead of sinking to 0.
    return f if f != 0 else None


# Plausible rim band for Calgary (m AMSL): the city's terrain sits ~975–1300 m (Bow River
# valley to the western edge). A rim outside the band is treated as missing so a placeholder
# value cannot poison the rim-derived max depth on that node (mirrors Regina's invert band).
_RIM_MIN, _RIM_MAX = 900.0, 1400.0


def _rim(v):
    """RIM_ELEV -> float m AMSL, or None when missing OR implausible."""
    f = _num(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


def _line_ends(geom):
    """First and last (lon, lat) of a (Multi)LineString geometry; MultiLineString is flattened
    so endpoint snapping sees the polyline's true ends."""
    coords = (geom or {}).get("coordinates") or []
    if not coords:
        return None, None
    if isinstance(coords[0][0], (list, tuple)):   # MultiLineString -> flatten
        coords = [pt for part in coords for pt in part]
    if len(coords) < 2:
        return None, None
    return tuple(coords[0][:2]), tuple(coords[-1][:2])


def _diameter_m(props):
    """Circular diameter in metres from the WIDTH (mm) field. Calgary stores circular pipes with
    HEIGHT == WIDTH (both mm); the diameter is WIDTH/1000. Non-circular (HEIGHT != WIDTH) still
    maps to an equivalent circular bore via WIDTH so the circular-only builder stays valid."""
    w = _num(props.get("WIDTH"))
    return (w / 1000.0) if (w and w > 0) else None


def _outfall_points(features):
    points = []
    for f in features:
        c = (f.get("geometry") or {}).get("coordinates")
        if c and len(c) >= 2:
            points.append((c[0], c[1]))
    return points


# Calgary has no node ids, so topology is snapped from polyline endpoints: a coarser tolerance
# (~1 m, snap_decimals=5) connects endpoints that don't perfectly coincide, avoiding spurious
# fragmentation — same choice as Ottawa.
_CALGARY_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    """Normalise a layer to a plain list of GeoJSON Features, accepting either a bare list or a
    ``{"type": "FeatureCollection", "features": [...]}`` dict."""
    if layer is None:
        return []
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer)


def build_calgary_network(storm, *, config: base.AssembleConfig = _CALGARY_ASSEMBLE) -> base.NetworkResult:
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
        name = str(p.get("OBJECTID") or p.get("GLOBALID") or "P")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:                       # ensure unique conduit names
            name = f"{name}_{len(pipes)}"
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_num(p.get("UP_INVERT")), inv_b=_num(p.get("DN_INVERT")),
            diameter_m=_diameter_m(p),
            roughness_n=base.material_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=_num(p.get("LENGTH")),
        ))

    outfall_points = _outfall_points(outfalls_f)

    # Manhole RIM_ELEV (100% populated on the fixture bbox, verified 2026-07-03) -> node
    # ground elevation, so max depth becomes rim - invert instead of the 2 m default.
    ground_points = []
    manholes_f = _features(storm.get("manholes")) if isinstance(storm, dict) else []
    for f in manholes_f:
        c = (f.get("geometry") or {}).get("coordinates")
        rim = _rim((f.get("properties") or {}).get("RIM_ELEV"))
        if c and len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    result = base.assemble_network(pipes, outfall_points=outfall_points,
                                   ground_points=ground_points, config=config)
    diag = {**result.diagnostics, "city": "calgary", "n_pipes_in": len(pipes_f),
            "n_no_geom": n_no_geom, "n_outfall_points": len(outfall_points),
            "n_ground_points": len(ground_points)}
    return base.NetworkResult(network=result.network, diagnostics=diag)
