"""City of Ottawa storm-sewer open data -> SWMM ``NetworkIn`` (geometry-inferred topology).

Ottawa publishes inverts (INVERT_UPSTREAM/DOWNSTREAM), WIDTH, MATERIAL, LENGTHASBUILT but
**no node ids**, so topology is inferred from pipe polyline endpoints by ``cities.base``
(coordinate snapping). A ``0`` invert/width/length means "missing". Parcels/buildings are not
published, so subcatchments seed on catch basins (Storm Inlets, layer 21) and take
imperviousness from land cover (no parcel/building override).

The same WastewaterInfrastructure service publishes the separated Sanitary Pipes (layer 7)
with the identical invert/width schema, so ``fetch_ottawa_sanitary`` + the unchanged builder
give the second tagged system (ADR 0011). Storm Manholes (layer 23) carry NO rim/ground
elevation field (only STRUCT_ID/status, verified 2026-07-03) — so, like Kelowna, no
ground_points are passed and node max depths keep the assembler default.
"""
from swmmcanada.sources.cities import base

ARC = "https://maps.ottawa.ca/arcgis/rest/services/WastewaterInfrastructure/MapServer"
STORM_PIPES = 26
STORM_OUTFALLS = 22
STORM_INLETS = 21  # catch basins / inlets
SANITARY_PIPES = 7  # Sanitary Pipes — same schema as storm (inverts/width/material)
OTTAWA_CRS = "EPSG:32618"  # UTM 18N (metric ops)
_PAGE = 1000

# The sanitary layer is published all-SANP/IN_SERVICE today; the explicit filter keeps the
# gravity skeleton clean if abandoned/proposed lines ever appear. (No force-main indicator is
# published on this layer.)
_SANITARY_WHERE = "LIFE_CYCLE_STATUS = 'IN_SERVICE'"


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


def fetch_ottawa_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary sewer lines intersecting ``bbox`` — the second tagged system
    (ADR 0011). Same publication schema as the storm layer, so :func:`build_ottawa_network`
    assembles it unchanged (per-component sinks stand in for the treatment-bound trunk
    exits)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or OttawaClient()
    return {"pipes": _fetch(SANITARY_PIPES, bbox, client, where=_SANITARY_WHERE)}


def fetch_ottawa_land(bbox, *, client=None) -> dict:
    """Ottawa has no public parcels/buildings — only catch basins (inlets) for seeding."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or OttawaClient()
    return {"catchbasins": _fetch(STORM_INLETS, bbox, client), "parcels": [], "buildings": []}


def _num(v):
    return base.num(v, zero_missing=True)     # 0 == missing in Ottawa's data


_line_ends = base.line_ends


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
