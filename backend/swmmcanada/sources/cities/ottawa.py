"""City of Ottawa storm-sewer open data -> SWMM ``NetworkIn`` (geometry-inferred topology).

Ottawa publishes inverts (INVERT_UPSTREAM/DOWNSTREAM), WIDTH, MATERIAL, LENGTHASBUILT but
**no node ids**, so topology is inferred from pipe polyline endpoints by ``cities.base``
(coordinate snapping). A ``0`` invert/width/length means "missing". Parcels/buildings are not
published, so subcatchments seed on catch basins (Storm Inlets, layer 21) and take
imperviousness from land cover (no parcel/building override).
"""
from swmmcanada.sources.cities import base

ARC = "https://maps.ottawa.ca/arcgis/rest/services/WastewaterInfrastructure/MapServer"
STORM_PIPES = 26
STORM_OUTFALLS = 22
STORM_INLETS = 21  # catch basins / inlets
OTTAWA_CRS = "EPSG:32618"  # UTM 18N (metric ops)
_PAGE = 1000


# Shared ArcGIS client + Esri-JSON->GeoJSON converter now live in cities.base (Phase 0).
OttawaClient = base.ArcGISClient


def _fetch(layer, bbox, client, where="1=1") -> list:
    """Paginated bbox query. Ottawa's MapServer only serves Esri JSON (``f=geojson`` comes
    back empty), so fetch ``f=json`` and convert every feature."""
    return base.fetch_paged(client, f"{ARC}/{layer}/query", bbox, where=where,
                            fmt="json", page_size=_PAGE, transform=base.esri_to_geojson)


def fetch_ottawa_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or OttawaClient()
    return {"pipes": _fetch(STORM_PIPES, bbox, client), "outfalls": _fetch(STORM_OUTFALLS, bbox, client)}


def fetch_ottawa_land(bbox, *, client=None) -> dict:
    """Ottawa has no public parcels/buildings — only catch basins (inlets) for seeding."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or OttawaClient()
    return {"catchbasins": _fetch(STORM_INLETS, bbox, client), "parcels": [], "buildings": []}


def _num(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None          # 0 == missing in Ottawa's data


def _line_ends(geom):
    coords = (geom or {}).get("coordinates") or []
    if not coords:
        return None, None
    if isinstance(coords[0][0], (list, tuple)):   # MultiLineString -> flatten
        coords = [pt for part in coords for pt in part]
    if len(coords) < 2:
        return None, None
    return tuple(coords[0][:2]), tuple(coords[-1][:2])


# Ottawa has no node ids, so topology is snapped from polyline endpoints: a coarser tolerance
# (~1 m) connects endpoints that don't perfectly coincide, avoiding spurious fragmentation.
_OTTAWA_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def build_ottawa_network(storm, *, config: base.AssembleConfig = _OTTAWA_ASSEMBLE) -> base.NetworkResult:
    pipes_f = storm["pipes"] if isinstance(storm, dict) else list(storm)
    outfalls_f = storm.get("outfalls", []) if isinstance(storm, dict) else []

    pipes, seen, n_no_geom = [], {}, 0
    for f in pipes_f:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("STRUCT_ID") or p.get("OBJECTID") or "P")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:                       # ensure unique conduit names
            name = f"{name}_{p.get('OBJECTID')}"
        w = _num(p.get("WIDTH"))
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_num(p.get("INVERT_UPSTREAM")), inv_b=_num(p.get("INVERT_DOWNSTREAM")),
            diameter_m=(w / 1000.0) if w else None,
            roughness_n=base.material_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=_num(p.get("LENGTHASBUILT")),
        ))

    outfall_points = []
    for f in outfalls_f:
        c = (f.get("geometry") or {}).get("coordinates")
        if c and len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    result = base.assemble_network(pipes, outfall_points=outfall_points, config=config)
    diag = {**result.diagnostics, "city": "ottawa", "n_pipes_in": len(pipes_f), "n_no_geom": n_no_geom}
    return base.NetworkResult(network=result.network, diagnostics=diag)
