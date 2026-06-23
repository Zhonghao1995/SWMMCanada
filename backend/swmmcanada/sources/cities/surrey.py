"""City of Surrey storm-drain open data -> SWMM ``NetworkIn`` (geometry-inferred topology).

Surrey publishes inverts (UP_ELEVATION/DOWN_ELEVATION, in m), SLOPE, MAIN_SIZE (mm),
MAIN_SHAPE, MATERIAL and SHAPE.LEN on its Drn Mains layer, but **no node ids on the mains**,
so topology is inferred from pipe polyline endpoints by ``cities.base`` (coordinate snapping) —
the same approach as Ottawa. Unlike Ottawa, Surrey also publishes parcels (Lot) and Buildings,
so subcatchment imperviousness can use the real parcel/building override (ADR 0005).

Surrey's service is a MapServer on the city's own ArcGIS Server; ``f=geojson`` returns real
geometry, so features are fetched as GeoJSON directly (no Esri-JSON conversion needed). A
``base.esri_to_geojson`` fallback path is kept so a layer that only serves Esri JSON still works.
See ``tests/fixtures/surrey/README.md``.
"""
from swmmcanada.sources.cities import base

ARC = "https://gisservices.surrey.ca/arcgis/rest/services/OpenData/MapServer"
STORM_MAINS = 18        # Drn Mains (polyline) — UP/DOWN_ELEVATION, MAIN_SIZE, MATERIAL, MAIN_TYPE2
MANHOLES = 23           # Drainage Manholes — NODE_NO, RIM_ELEVATION, OUTFLOW_ELEVATION
CATCHBASINS = 24        # Drainage Catch Basins
DRAINAGE_DEVICES = 25   # Drainage Devices — filter DEVICE_CLASSIFICATION='Outlet' for outfalls
LAND_PARCELS = 148      # Lot (polygon)
BUILDINGS = 155         # Buildings (polygon)
SURREY_CRS = "EPSG:32610"  # UTM 10N (metric ops) — same zone as Victoria
_PAGE = 2000               # layer maxRecordCount

# Surrey publishes only gravity mains as a routable network; the other MAIN_TYPE2 values
# (Culvert, Stub, Foundation Drain, Forcemain, ...) are not part of the gravity storm graph.
_GRAVITY_WHERE = "MAIN_TYPE2='Gravity'"
_OUTLET_WHERE = "DEVICE_CLASSIFICATION='Outlet'"


# Shared ArcGIS client + Esri-JSON->GeoJSON converter live in cities.base (Phase 0).
SurreyClient = base.ArcGISClient


def _fetch(layer, bbox, client, where="1=1") -> list:
    """Paginate an ArcGIS layer query over ``bbox`` and return GeoJSON Features.

    Surrey's MapServer serves real geometry under ``f=geojson``; if a layer ever returns
    Esri JSON instead (``attributes`` rather than GeoJSON ``properties``), each feature is
    converted with ``base.esri_to_geojson`` so downstream code always sees GeoJSON.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    url = f"{ARC}/{layer}/query"
    features, offset = [], 0
    while True:
        params = {
            "where": where, "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "true",
            "outSR": 4326, "f": "geojson", "resultOffset": offset, "resultRecordCount": _PAGE,
        }
        payload = client.get_json(url, params)
        page = payload.get("features") or []
        features.extend(_as_geojson(f) for f in page)
        if not payload.get("exceededTransferLimit") or not page:
            break
        offset += len(page)
    return features


def _as_geojson(feat: dict) -> dict:
    """Pass GeoJSON Features through unchanged; convert Esri-JSON features (``attributes``)."""
    if "attributes" in feat and "properties" not in feat:
        return base.esri_to_geojson(feat)
    return feat


def fetch_surrey_storm(bbox, *, client=None) -> dict:
    """Storm network intersecting ``bbox`` (EPSG:4326 tuple, or object with ``.bbox``):
    gravity Drn Mains + 'Outlet' Drainage Devices. Returns
    ``{"pipes": [...], "outfalls": [...]}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SurreyClient()
    return {
        "pipes": _fetch(STORM_MAINS, bbox, client, where=_GRAVITY_WHERE),
        "outfalls": _fetch(DRAINAGE_DEVICES, bbox, client, where=_OUTLET_WHERE),
    }


def fetch_surrey_land(bbox, *, client=None) -> dict:
    """Catch basins + land units for the parcel/building subcatchment method:
    ``{"catchbasins", "parcels", "buildings"}`` (lists of GeoJSON Features). Surrey, unlike
    Ottawa, publishes both parcels (Lot) and buildings."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SurreyClient()
    return {
        "catchbasins": _fetch(CATCHBASINS, bbox, client),
        "parcels": _fetch(LAND_PARCELS, bbox, client),
        "buildings": _fetch(BUILDINGS, bbox, client),
    }


def _num(v):
    """Float or None. Unlike Ottawa, ``0`` is a legitimate elevation in Surrey (sea level), so
    only blank/None/unparseable is treated as missing — never a real zero."""
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _line_ends(geom):
    coords = (geom or {}).get("coordinates") or []
    if not coords:
        return None, None
    if isinstance(coords[0][0], (list, tuple)):   # MultiLineString -> flatten parts
        coords = [pt for part in coords for pt in part]
    if len(coords) < 2:
        return None, None
    return tuple(coords[0][:2]), tuple(coords[-1][:2])


# Surrey has no node ids on mains, so topology is snapped from polyline endpoints: a coarser
# tolerance (snap_decimals=5, ~1 m) connects endpoints that don't perfectly coincide, avoiding
# spurious fragmentation (mirrors Ottawa).
_SURREY_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def _features(layer) -> list:
    """Normalize a layer arg to a list of Features: a FeatureCollection dict, a plain list, or
    None all collapse to ``[...]`` (so callers can pass either shape)."""
    if layer is None:
        return []
    if isinstance(layer, dict):
        return list(layer.get("features") or [])
    return list(layer)


def build_surrey_network(storm, *, config: base.AssembleConfig = _SURREY_ASSEMBLE) -> base.NetworkResult:
    if isinstance(storm, dict) and ("pipes" in storm or "outfalls" in storm):
        pipes_f = _features(storm.get("pipes"))
        outfalls_f = _features(storm.get("outfalls"))
    else:                                  # a bare FeatureCollection / list of pipe features
        pipes_f = _features(storm)
        outfalls_f = []

    pipes, seen, n_no_geom = [], {}, 0
    shape_hist = {}
    for f in pipes_f:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        shape = p.get("MAIN_SHAPE") or "UNK"
        shape_hist[shape] = shape_hist.get(shape, 0) + 1
        name = str(p.get("FACILITYID") or p.get("OBJECTID") or "P")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:                       # ensure unique conduit names
            name = f"{name}_{p.get('OBJECTID')}"
        size_mm = _num(p.get("MAIN_SIZE"))
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_num(p.get("UP_ELEVATION")), inv_b=_num(p.get("DOWN_ELEVATION")),
            diameter_m=(size_mm / 1000.0) if size_mm and size_mm > 0 else None,
            roughness_n=base.material_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=_num(p.get("SHAPE.LEN")),
        ))

    outfall_points = []
    for f in outfalls_f:
        c = (f.get("geometry") or {}).get("coordinates")
        if c and len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    result = base.assemble_network(pipes, outfall_points=outfall_points, config=config)
    diag = {**result.diagnostics, "city": "surrey", "n_pipes_in": len(pipes_f),
            "n_no_geom": n_no_geom, "shape_histogram": shape_hist}
    return base.NetworkResult(network=result.network, diagnostics=diag)
