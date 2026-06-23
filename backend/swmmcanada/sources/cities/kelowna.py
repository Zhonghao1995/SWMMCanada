"""City of Kelowna storm-sewer open data -> SWMM ``NetworkIn`` (geometry-inferred topology).

Kelowna (geoportal.kelowna.ca) publishes inverts (INVERT_IN_Z/INVERT_OUT_Z, doubles) but
**no node ids**, so topology is inferred from pipe polyline endpoints by ``cities.base``
(coordinate snapping) — same shape as the Ottawa adapter. DIAMETER and LENGTH are stored as
**strings** (e.g. "300", "47.46") and must be cast to float; an empty/0/non-numeric value
means "missing". The Storm Main layer (22) carries the hydraulics; manholes (layer 7) have no
rim/invert fields, so ``assemble_network`` back-fills node inverts from the connected pipe
ends — no ground_points are passed. Outfalls come from layer 4.

Kelowna DOES publish parcels (Legal Parcel) and Building Outlines, so subcatchment
delineation can use real lot lines + roofs (ADR 0005). Catch basins (layer 19, with
SUMP_ELEVATION / CB_TYPE) seed the subcatchments.
"""
from swmmcanada.sources.cities import base

# Storm utilities service (NOTE: root is geoportal.kelowna.ca, not geo.kelowna.ca).
ARC = "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Utilities_Storm/MapServer"
STORM_PIPES = 22       # Storm Main (polyline); INVERT_IN_Z/OUT_Z, DIAMETER/LENGTH (strings)
STORM_OUTFALLS = 4     # Storm Outfall (point)
STORM_CATCHBASINS = 19  # Storm Catchbasin (point); SUMP_ELEVATION, CB_TYPE
# Planning service hosts parcels + building outlines.
PLANNING = "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Planning_and_other/MapServer"
PARCELS = 3            # Legal Parcel (polygon)
BUILDINGS = 17         # Building Outlines (polygon)

KELOWNA_CRS = "EPSG:32611"  # UTM 11N (metric ops)
_PAGE = 1000


# Shared ArcGIS client + Esri-JSON->GeoJSON converter live in cities.base (Phase 0).
KelownaClient = base.ArcGISClient


def _fetch(url_root, layer, bbox, client, where="1=1") -> list:
    """Paginated bbox query against an ArcGIS layer. Kelowna's MapServer serves real geometry
    for ``f=geojson`` (verified 2026-06-22), so we read GeoJSON directly — no esri_to_geojson
    conversion needed."""
    min_lon, min_lat, max_lon, max_lat = bbox
    qurl = f"{url_root}/{layer}/query"
    features, offset = [], 0
    while True:
        params = {
            "where": where, "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "true",
            "outSR": 4326, "f": "geojson", "resultOffset": offset, "resultRecordCount": _PAGE,
        }
        payload = client.get_json(qurl, params)
        page = payload.get("features") or []
        features.extend(page)
        if not payload.get("exceededTransferLimit") or not page:
            break
        offset += len(page)
    return features


def fetch_kelowna_storm(bbox, *, client=None) -> dict:
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KelownaClient()
    return {
        "pipes": _fetch(ARC, STORM_PIPES, bbox, client),
        "outfalls": _fetch(ARC, STORM_OUTFALLS, bbox, client),
    }


def fetch_kelowna_land(bbox, *, client=None) -> dict:
    """Kelowna publishes catch basins, parcels (Legal Parcel) AND building outlines, so
    subcatchments can follow real lot lines + roofs."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KelownaClient()
    return {
        "catchbasins": _fetch(ARC, STORM_CATCHBASINS, bbox, client),
        "parcels": _fetch(PLANNING, PARCELS, bbox, client),
        "buildings": _fetch(PLANNING, BUILDINGS, bbox, client),
    }


def _num(v):
    """Cast a (possibly string) numeric field to float; treat ""/None/0/non-numeric as
    missing. Kelowna stores DIAMETER and LENGTH as strings, so this also parses "300"/"47.46"."""
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None          # 0 == missing in Kelowna's data


def _kelowna_roughness(material, default):
    """Manning's n for a Kelowna material code. Kelowna uses perforated/ribbed variants
    (PERFPVC, RIBPVC, PERFRIBPVC, PERFAC) and RCP/VIT that the shared table doesn't list;
    normalise by stripping ``PERF``/``RIB`` prefixes and aliasing RCP->concrete, VIT->clay so
    they resolve to the right pipe roughness instead of silently falling back to the default."""
    if not material:
        return default
    code = str(material).strip().upper()
    code = code.replace("PERF", "").replace("RIB", "")   # perforated/ribbed -> base material
    code = {"RCP": "CONC", "VIT": "VITC"}.get(code, code)
    return base.material_roughness(code, default)


def _line_ends(geom):
    coords = (geom or {}).get("coordinates") or []
    if not coords:
        return None, None
    if isinstance(coords[0][0], (list, tuple)):   # MultiLineString -> flatten
        coords = [pt for part in coords for pt in part]
    if len(coords) < 2:
        return None, None
    return tuple(coords[0][:2]), tuple(coords[-1][:2])


# Kelowna has no node ids, so topology is snapped from polyline endpoints: a coarser tolerance
# (~1 m, snap_decimals=5) connects endpoints that don't perfectly coincide, avoiding spurious
# fragmentation.
_KELOWNA_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


def build_kelowna_network(storm, *, config: base.AssembleConfig = _KELOWNA_ASSEMBLE) -> base.NetworkResult:
    pipes_f = storm["pipes"] if isinstance(storm, dict) else list(storm)
    outfalls_f = storm.get("outfalls", []) if isinstance(storm, dict) else []

    pipes, seen, n_no_geom = [], {}, 0
    for f in pipes_f:
        p = f.get("properties") or {}
        a, b = _line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue
        name = str(p.get("FEATUREID") or p.get("OBJECTID") or "P")
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:                       # ensure unique conduit names
            name = f"{name}_{p.get('OBJECTID')}"
        dia_mm = _num(p.get("DIAMETER"))         # DIAMETER stored as STRING (mm)
        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=_num(p.get("INVERT_IN_Z")), inv_b=_num(p.get("INVERT_OUT_Z")),
            diameter_m=(dia_mm / 1000.0) if dia_mm else None,
            roughness_n=_kelowna_roughness(p.get("MATERIAL"), config.default_roughness),
            length_m=_num(p.get("LENGTH")),      # LENGTH stored as STRING (m)
        ))

    outfall_points = []
    for f in outfalls_f:
        c = (f.get("geometry") or {}).get("coordinates")
        if c and len(c) >= 2:
            outfall_points.append((c[0], c[1]))

    # Manholes (layer 7) carry no rim/invert, so we pass no ground_points: base back-fills
    # every node invert from the connected pipe INVERT_IN_Z/INVERT_OUT_Z ends.
    result = base.assemble_network(pipes, outfall_points=outfall_points, config=config)
    diag = {**result.diagnostics, "city": "kelowna", "n_pipes_in": len(pipes_f), "n_no_geom": n_no_geom}
    return base.NetworkResult(network=result.network, diagnostics=diag)
