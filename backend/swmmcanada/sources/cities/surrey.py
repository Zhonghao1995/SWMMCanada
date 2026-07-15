"""City of Surrey storm-drain open data -> SWMM ``NetworkIn`` (EXPLICIT node topology).

The 2026-07-14 audit found Surrey's OpenData view strips topology columns the city actually
publishes: the token-free ``Public/Drainage/MapServer`` serves the SAME mains assets
(FACILITYID-identical) **plus UP_NODE/DOWN_NODE (100% populated)**, and its node layers
(Manholes 4 / Catch Basins 2 / Devices 3) all carry the joining ``NODE_NO`` along with
RIM_ELEVATION and OUTFLOW_ELEVATION. So topology is now an explicit node-link join
(Kitchener/Vancouver style) with the polyline-vertex fallback for out-of-bbox refs; the old
geometry-inferred path is kept for inputs without node data (legacy fixtures/tests).

Mains also carry UP/DOWN_ELEVATION (m), SLOPE, MAIN_SIZE (mm), MAIN_SHAPE, MATERIAL and
STATUS — the audit caught Abandoned mains slipping through, so the storm filter now requires
``STATUS='In Service'`` like the sanitary one always did. Surrey publishes parcels (Lot) and
Buildings, so subcatchment imperviousness uses the real parcel/building override (ADR 0005).

``f=geojson`` returns real geometry on both services; the ``_as_geojson`` fallback converts
any layer that answers in Esri JSON. See ``tests/fixtures/surrey/README.md``.
"""
from swmmcanada.sources.cities import base

ARC = "https://gisservices.surrey.ca/arcgis/rest/services/OpenData/MapServer"
PUB = "https://gisservices.surrey.ca/arcgis/rest/services/Public/Drainage/MapServer"
STORM_MAINS = 18        # OpenData Drn Mains — legacy layer (no node ids); kept for reference
PUB_MAINS = 14          # Public/Drainage Drainage Mains — same assets + UP_NODE/DOWN_NODE
PUB_MANHOLES = 4        # Public/Drainage Manholes — NODE_NO, RIM_ELEVATION, OUTFLOW_ELEVATION
PUB_CATCHBASINS = 2     # Public/Drainage Catch Basins — NODE_NO, RIM_ELEVATION
PUB_DEVICES = 3         # Public/Drainage Devices — NODE_NO, DEVICE_CLASSIFICATION ('Outlet')
MANHOLES = 23           # OpenData Drainage Manholes (legacy)
CATCHBASINS = 24        # Drainage Catch Basins (land seeding)
DRAINAGE_DEVICES = 25   # OpenData Drainage Devices (legacy outfall source)
SAN_MAINS = 41          # San Mains (polyline) — same schema as Drn Mains (UP/DOWN_ELEVATION, ...)
LAND_PARCELS = 148      # Lot (polygon)
BUILDINGS = 155         # Buildings (polygon)
SURREY_CRS = "EPSG:32610"  # UTM 10N (metric ops) — same zone as Victoria
_PAGE = 2000               # layer maxRecordCount

# Surrey publishes only gravity mains as a routable network; the other MAIN_TYPE2 values
# (Culvert, Stub, Foundation Drain, Forcemain, ...) are not part of the gravity storm graph.
# STATUS keeps Abandoned/Proposed lines out (audit 2026-07-14: 7 Abandoned in one bbox).
_GRAVITY_WHERE = "MAIN_TYPE2='Gravity' AND STATUS='In Service'"
_OUTLET_WHERE = "DEVICE_CLASSIFICATION='Outlet'"
# The sanitary layer additionally carries Abandoned/Proposed lines (STATUS), unlike the storm
# adapter's gravity filter alone; only in-service gravity mains join the sanitary skeleton.
_SAN_WHERE = "MAIN_TYPE2='Gravity' AND STATUS='In Service'"


# Shared ArcGIS client + Esri-JSON->GeoJSON converter live in cities.base (Phase 0).
SurreyClient = base.ArcGISClient


def _fetch(layer, bbox, client, where="1=1", service=ARC) -> list:
    """Paginated bbox query returning GeoJSON Features. Surrey's MapServers serve real
    geometry under ``f=geojson``; if a layer ever returns Esri JSON instead (``attributes``
    rather than ``properties``), ``_as_geojson`` converts each feature."""
    return base.fetch_paged(client, f"{service}/{layer}/query", bbox,
                            where=where, page_size=_PAGE, transform=_as_geojson)


def _as_geojson(feat: dict) -> dict:
    """Pass GeoJSON Features through unchanged; convert Esri-JSON features (``attributes``)."""
    if "attributes" in feat and "properties" not in feat:
        return base.esri_to_geojson(feat)
    return feat


def fetch_surrey_storm(bbox, *, client=None) -> dict:
    """Storm network intersecting ``bbox`` (EPSG:4326 tuple, or object with ``.bbox``):
    in-service gravity mains from ``Public/Drainage/14`` (with UP_NODE/DOWN_NODE) plus the
    NODE_NO-carrying node layers (manholes + catch basins + devices) that resolve them.
    Returns ``{"pipes": [...], "nodes": [...], "outfalls": [...]}`` — ``outfalls`` stays the
    'Outlet'-classified devices for the assembler's direct-outfall detection."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SurreyClient()
    pipes = _fetch(PUB_MAINS, bbox, client, where=_GRAVITY_WHERE, service=PUB)
    nodes = (_fetch(PUB_MANHOLES, bbox, client, service=PUB)
             + _fetch(PUB_CATCHBASINS, bbox, client, service=PUB)
             + _fetch(PUB_DEVICES, bbox, client, service=PUB))
    outfalls = [f for f in nodes
                if str((f.get("properties") or {}).get("DEVICE_CLASSIFICATION") or "") == "Outlet"]
    return {"pipes": pipes, "nodes": nodes, "outfalls": outfalls}


def fetch_surrey_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary (San Mains) sewer lines intersecting ``bbox`` — the second tagged
    system (ADR 0011). Layer 41 shares the Drn Mains publication schema, so
    :func:`build_surrey_network` assembles it unchanged (per-component sinks stand in for
    the treatment-bound trunk exits)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or SurreyClient()
    return {"pipes": _fetch(SAN_MAINS, bbox, client, where=_SAN_WHERE)}


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
    return base.num(v)     # 0 is a legitimate elevation in Surrey (sea level) — only blank/unparseable is missing


# Plausible rim band for Surrey (m AMSL): the city rises from the diked Fraser/Mud Bay
# lowlands (~1 m) to ~134 m. Unlike pipe inverts (where 0 = sea level is legitimate), a
# manhole RIM sits on the ground surface, so a 0.0 rim is a missing-data placeholder — the
# band's lower edge screens it out along with any other implausible value.
_RIM_MIN, _RIM_MAX = 0.5, 200.0


def _rim(v):
    """RIM_ELEVATION -> float m AMSL, or None when missing OR implausible."""
    f = _num(v)
    return f if (f is not None and _RIM_MIN <= f <= _RIM_MAX) else None


_line_ends = base.line_ends


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

    # Explicit topology (audit 2026-07-14): NODE_NO -> coordinates/rims from the fetched
    # node layers. Inputs without node data (legacy fixtures, the OpenData view, sanitary)
    # keep the geometry-inferred path: every lookup below just misses and the polyline
    # fallback takes over.
    node_xy, node_rim = {}, {}
    label_points = []
    nodes_f = _features(storm.get("nodes")) if isinstance(storm, dict) else []
    for f in nodes_f:
        p = f.get("properties") or {}
        nid = str(p.get("NODE_NO") or "").strip()
        c = (f.get("geometry") or {}).get("coordinates")
        if not nid or nid in node_xy or not c or len(c) < 2:
            continue
        node_xy[nid] = (c[0], c[1])
        label_points.append(((c[0], c[1]), nid))
        rim = _rim(p.get("RIM_ELEVATION"))
        if rim is not None:
            node_rim[nid] = rim

    pipes, seen, n_no_geom, n_dangling = [], {}, 0, 0
    shape_hist = {}
    for f in pipes_f:
        p = f.get("properties") or {}
        p0, p1 = _line_ends(f.get("geometry"))
        up_id = str(p.get("UP_NODE") or "").strip()
        dn_id = str(p.get("DOWN_NODE") or "").strip()
        a = node_xy.get(up_id) or p0
        b = node_xy.get(dn_id) or p1
        if node_xy:                              # only meaningful on the explicit path
            n_dangling += int(up_id not in node_xy) + int(dn_id not in node_xy)
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

    # Node RIM_ELEVATION -> ground (max depth = rim - invert). Explicit path: rims ride the
    # NODE_NO join above; legacy path: the OpenData manhole features carry them directly.
    ground_points = [(node_xy[nid], rim) for nid, rim in node_rim.items()]
    manholes_f = _features(storm.get("manholes")) if isinstance(storm, dict) else []
    for f in manholes_f:
        c = (f.get("geometry") or {}).get("coordinates")
        rim = _rim((f.get("properties") or {}).get("RIM_ELEVATION"))
        if c and len(c) >= 2 and rim is not None:
            ground_points.append(((c[0], c[1]), rim))

    result = base.assemble_network(pipes, outfall_points=outfall_points,
                                   ground_points=ground_points, label_points=label_points,
                                   config=config)
    diag = {**result.diagnostics, "city": "surrey", "n_pipes_in": len(pipes_f),
            "n_no_geom": n_no_geom, "shape_histogram": shape_hist,
            "n_ground_points": len(ground_points),
            "n_nodes_in": len(node_xy), "n_dangling_nodes": n_dangling,
            "topology": "explicit_node_ids" if node_xy else "geometry_inferred"}
    return base.NetworkResult(network=result.network, diagnostics=diag)
