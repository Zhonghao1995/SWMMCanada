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
# Sanitary utilities service — Sanitary Main shares the storm schema (INVERT_IN_Z/OUT_Z,
# string DIAMETER/LENGTH); force mains live on their own layer (12) and are not fetched.
SAN_ARC = "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Utilities_Sanitary/MapServer"
SAN_MAINS = 11         # Sanitary Main (polyline)
# Planning service hosts parcels + building outlines.
PLANNING = "https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Planning_and_other/MapServer"
PARCELS = 3            # Legal Parcel (polygon)
BUILDINGS = 17         # Building Outlines (polygon)

KELOWNA_CRS = "EPSG:32611"  # UTM 11N (metric ops)
_PAGE = 1000

# Active sanitary gravity mains only (STATUS also holds B / I inactive-ish codes).
_SANITARY_WHERE = "STATUS = 'A'"


# Shared ArcGIS client + Esri-JSON->GeoJSON converter live in cities.base (Phase 0).
KelownaClient = base.ArcGISClient


def _fetch(url_root, layer, bbox, client, where="1=1") -> list:
    """Paginated bbox query against an ArcGIS layer. Kelowna's MapServer serves real geometry
    for ``f=geojson`` (verified 2026-06-22), so we read GeoJSON directly — no esri_to_geojson
    conversion needed."""
    return base.fetch_paged(client, f"{url_root}/{layer}/query", bbox,
                            where=where, page_size=_PAGE)


def fetch_kelowna_storm(bbox, *, client=None) -> dict:
    """Pipes + outfalls, plus Building Outlines whose ``Ground_Z`` (97.6% populated, audit
    2026-07-14) is the only public ground-elevation source in Kelowna — node rims are
    genuinely unpublished (locked in internal Cityworks), so nearby building ground
    elevations serve as a rim PROXY for node max depths (ADR 0021 §7; mitigation, not
    measurement — inverts are untouched)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KelownaClient()
    result = {
        "pipes": _fetch(ARC, STORM_PIPES, bbox, client),
        "outfalls": _fetch(ARC, STORM_OUTFALLS, bbox, client),
        "buildings": [],
    }
    try:    # the rim PROXY is additive and must never block the network fetch
        result["buildings"] = _fetch(PLANNING, BUILDINGS, bbox, client)
    except Exception:  # noqa: BLE001 — slow/unreachable PLANNING -> default max depths
        pass
    return result


def fetch_kelowna_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary (Sanitary Main) sewer lines intersecting ``bbox`` — the second
    tagged system (ADR 0011). Same publication schema as the storm layer, so
    :func:`build_kelowna_network` assembles it unchanged (per-component sinks stand in for
    the treatment-bound trunk exits)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KelownaClient()
    return {"pipes": _fetch(SAN_ARC, SAN_MAINS, bbox, client, where=_SANITARY_WHERE)}


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
    return base.num(v, zero_missing=True)     # 0 == missing; also parses Kelowna's string-typed DIAMETER/LENGTH


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


_line_ends = base.line_ends


# Kelowna has no node ids, so topology is snapped from polyline endpoints: a coarser tolerance
# (~1 m, snap_decimals=5) connects endpoints that don't perfectly coincide, avoiding spurious
# fragmentation.
_KELOWNA_ASSEMBLE = base.AssembleConfig(snap_decimals=5)


# Kelowna terrain runs ~340-650 m AMSL; a Ground_Z outside the band is a placeholder.
_GROUND_BAND = (300.0, 700.0)
_GROUND_TOL_M = 60.0     # a building within this distance of a node is a usable rim proxy


def _building_ground_points(buildings, node_coords):
    """Nearest building ``Ground_Z`` within ``_GROUND_TOL_M`` of each node coordinate ->
    ``[(xy, elev)]`` for the assembler's max-depth logic. O(nodes x buildings) is fine at
    AOI scale; the proxy never touches inverts (ADR 0021 §7)."""
    import math
    samples = []
    for f in buildings or []:
        gz = base.num((f.get("properties") or {}).get("Ground_Z"), zero_missing=True)
        if gz is None or not (_GROUND_BAND[0] <= gz <= _GROUND_BAND[1]):
            continue
        g = f.get("geometry") or {}
        ring = (g.get("coordinates") or [[]])[0]
        if g.get("type") == "MultiPolygon":
            ring = (g.get("coordinates") or [[[]]])[0][0]
        if not ring:
            continue
        cx = sum(x for x, *_ in ring) / len(ring)
        cy = sum(y for _, y, *_ in ring) / len(ring)
        samples.append((cx, cy, gz))
    if not samples:
        return []
    out = []
    for (nx, ny) in node_coords:
        best, best_d = None, None
        for (cx, cy, gz) in samples:
            d = math.hypot((cx - nx) * 71500.0, (cy - ny) * 111320.0)
            if best_d is None or d < best_d:
                best, best_d = gz, d
        if best is not None and best_d <= _GROUND_TOL_M:
            out.append(((nx, ny), best))
    return out


def build_kelowna_network(storm, *, config: base.AssembleConfig = _KELOWNA_ASSEMBLE) -> base.NetworkResult:
    pipes_f = storm["pipes"] if isinstance(storm, dict) else list(storm)
    outfalls_f = storm.get("outfalls", []) if isinstance(storm, dict) else []
    buildings_f = storm.get("buildings", []) if isinstance(storm, dict) else []

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

    # Manholes (layer 7) carry no rim/invert (genuinely unpublished — locked in internal
    # Cityworks, audit 2026-07-14). Building Ground_Z within 60 m serves as a rim PROXY so
    # node max depths beat the flat 2.0 m default; inverts stay pipe-end-derived.
    node_coords = {pt for p_ in pipes for pt in (p_.end_a, p_.end_b)}
    ground_points = _building_ground_points(buildings_f, node_coords)
    result = base.assemble_network(pipes, outfall_points=outfall_points,
                                   ground_points=ground_points, config=config)
    diag = {**result.diagnostics, "city": "kelowna", "n_pipes_in": len(pipes_f),
            "n_no_geom": n_no_geom, "n_ground_proxy_points": len(ground_points),
            "ground_basis": "building Ground_Z proxy (<=60 m), ADR 0021 §7" if ground_points
                            else "none (default max depths)"}
    return base.NetworkResult(network=result.network, diagnostics=diag)
