"""City of London (Ontario) storm-sewer adapter: fetch + network assembly + subcatchments.

London publishes EXPLICIT pipe topology: a pipe's ``UpstreamID``/``DownstreamID`` (e.g. ``8G24``)
match a node's ``GIS_FeatureKey``. A referenced node can live in any of three layers — Manholes(2),
Sewer Other Nodes(3) or Sewer Outfalls(4) — so this adapter harvests the referenced ids and fetches
each layer ``GIS_FeatureKey IN (...)`` (mirroring Victoria's AssetID join). Endpoint coordinates are
resolved from those node points, with a polyline-vertex fallback for the (~1.5%) dangling refs, before
handing canonical pipes to the shared ``cities.base`` assembler.

Outfalls are detected by membership in the outfall layer (NOT an id prefix). Manhole ``LidElevation``
gives node ground (for max-depth); outfall ``PipeInvert`` is usually 0/unpopulated, so outfall inverts
are gap-filled from connected pipe ends by the assembler. The build target is circular-only, so each
pipe maps to the city's ``Diameter`` (mm) as an equivalent circular diameter; the original ``PipeShape``
is kept in diagnostics. London land layers (catch basins / parcels / buildings) feed the ADR 0005
subcatchment method (UTM 17N). See ``tests/fixtures/london/README.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.base import (  # re-exported for callers
    CatchbasinSubcatchmentConfig,
    material_roughness as _material_roughness,
)

Coord = Tuple[float, float]
LONDON_CRS = "EPSG:32617"

# --- ArcGIS layers (see fixtures/london/README.md) ------------------------------
BASE = "https://maps.london.ca/server/rest/services/OpenData/OpenData_Environment/MapServer"
CATCHBASINS, MANHOLES, OTHER_NODES, OUTFALLS, PIPES = 1, 2, 3, 4, 5
LAND_BASE = "https://maps.london.ca/server/rest/services/OpenData/OpenData_BaseMaps/MapServer"
LAND_BUILDINGS, LAND_PARCELS = 3, 53          # Buildings, Parcels
_NODE_LAYERS = (MANHOLES, OTHER_NODES, OUTFALLS)
_PAGE_SIZE, _ID_CHUNK = 1000, 80

# London material codes that the shared table keys differently; normalise before lookup so
# steel/clay/brick get their real Manning's n instead of falling through to the default.
_MATERIAL_ALIASES = {"VIT": "VITC", "BRCK": "BR", "ST": "STL"}


# The thin GET-as-JSON client lives in cities.base (Phase 0); keep a city-named alias.
LondonMapClient = base.ArcGISClient


# --- fetch ----------------------------------------------------------------------
def _payload_features(payload: dict) -> list:
    return (payload or {}).get("features") or []


def _fetch_layer_bbox(base_url, layer, bbox, client, where="1=1") -> list:
    """Paginated envelope-intersect query against one layer (the shared base loop)."""
    return base.fetch_paged(client, f"{base_url}/{layer}/query", bbox,
                            where=where, page_size=_PAGE_SIZE)


def _fetch_network(bbox, client, where: str) -> dict:
    """Pipes matching ``where`` by envelope, then referenced nodes BY GIS_FeatureKey from the
    manhole / other-node / outfall layers (the node layers are shared across flow types)."""
    mains = _fetch_layer_bbox(BASE, PIPES, bbox, client, where=where)
    node_ids = _referenced_node_ids(mains)
    return {
        "mains": mains,
        "manholes": _fetch_nodes_by_key(MANHOLES, node_ids, client),
        "other_nodes": _fetch_nodes_by_key(OTHER_NODES, node_ids, client),
        "outfalls": _fetch_nodes_by_key(OUTFALLS, node_ids, client),
    }


def fetch_london_storm(bbox, *, client=None) -> dict:
    """Storm network intersecting ``bbox`` (EPSG:4326 tuple, or object with ``.bbox``):
    STM pipes by envelope, then referenced nodes BY GIS_FeatureKey from the manhole /
    other-node / outfall layers. Returns
    ``{"mains", "manholes", "other_nodes", "outfalls"}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or LondonMapClient()
    return _fetch_network(bbox, client, "FlowType='STM'")


# The SAME Sewer Pipes layer carries the sanitary system (FlowType='SAN'); unlike the storm
# where-clause, ConstructedStatus matters here — the layer also holds Abandoned / Proposed /
# Removed sanitary lines (~40% over the fixture bbox), which are not part of the built graph.
_SANITARY_WHERE = "FlowType='SAN' AND ConstructedStatus='Built'"


def fetch_london_sanitary(bbox, *, client=None) -> dict:
    """Separated sanitary sewer network intersecting ``bbox`` — the second tagged system
    (ADR 0011). Same layers and join as the storm fetch (SAN pipes reference the same
    manhole/other-node/outfall layers), so :func:`build_london_network` assembles it
    unchanged (per-component sinks stand in for the treatment-bound trunk exits)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or LondonMapClient()
    return _fetch_network(bbox, client, _SANITARY_WHERE)


def _referenced_node_ids(mains) -> List[str]:
    ids, seen = [], set()
    for feat in mains:
        props = feat.get("properties") or {}
        for key in ("UpstreamID", "DownstreamID"):
            node_id = props.get(key)
            if node_id and node_id not in seen:
                seen.add(node_id)
                ids.append(node_id)
    return ids


def _fetch_nodes_by_key(layer: int, node_ids, client) -> list:
    if not node_ids:
        return []
    url = f"{BASE}/{layer}/query"
    features, seen = [], set()
    for start in range(0, len(node_ids), _ID_CHUNK):
        in_list = ",".join(f"'{a}'" for a in node_ids[start: start + _ID_CHUNK])
        params = {"where": f"GIS_FeatureKey IN ({in_list})", "outFields": "*",
                  "returnGeometry": "true", "outSR": 4326, "f": "geojson"}
        for feat in _payload_features(client.get_json(url, params)):
            key = (feat.get("properties") or {}).get("GIS_FeatureKey")
            if key in seen:
                continue
            seen.add(key)
            features.append(feat)
    return features


def fetch_london_land(bbox, *, client=None) -> dict:
    """Catch basins + land units for the parcel/building subcatchment method:
    ``{"catchbasins", "parcels", "buildings"}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or LondonMapClient()
    return {
        "catchbasins": _fetch_layer_bbox(BASE, CATCHBASINS, bbox, client),
        "parcels": _fetch_layer_bbox(LAND_BASE, LAND_PARCELS, bbox, client),
        "buildings": _fetch_layer_bbox(LAND_BASE, LAND_BUILDINGS, bbox, client),
    }


# --- network assembly -----------------------------------------------------------
@dataclass(frozen=True)
class LondonNetworkConfig:
    min_slope: float = 0.001
    default_max_depth_m: float = 2.0
    default_roughness: float = 0.013
    default_diameter_m: float = 0.30
    outfall_link_len_m: float = 10.0
    snap_tol: float = 1e-6                  # deg; polyline-vertex vs node-point match


@dataclass(frozen=True)
class LondonNetworkResult:
    network: "base.NetworkIn"
    diagnostics: dict = field(default_factory=dict)


def material_roughness(material: Optional[str], config: LondonNetworkConfig) -> float:
    """Manning's n for a London material code. Normalises London's code spellings
    (VIT/BRCK/ST) to the shared table's keys, then delegates to cities.base."""
    code = str(material).strip().upper() if material else material
    code = _MATERIAL_ALIASES.get(code, code)
    return _material_roughness(code, config.default_roughness)


def _features(layer) -> List[dict]:
    if layer is None:
        return []
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer)


def _sanitize(name) -> str:
    return "_".join(str(name).split())


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_endpoints(up_id, dn_id, line, coords, *, snap_tol):
    """Resolve a pipe's endpoint coordinates from the node layers. A known node id takes its
    point coordinate; a dangling id snaps to the far polyline vertex; both dangling ->
    line[0]=upstream, line[-1]=downstream. Returns (up_xy, dn_xy, n_dangling)."""
    p0 = tuple(line[0][:2]) if line else None
    p1 = tuple(line[-1][:2]) if line else None
    up, dn = coords.get(up_id), coords.get(dn_id)
    n_dangling = int(up is None) + int(dn is None)
    if up is not None and dn is not None:
        return up, dn, 0
    if up is None and dn is None:
        return p0, p1, n_dangling
    if up is None:
        up = _far_vertex(dn, p0, p1, snap_tol)
    else:
        dn = _far_vertex(up, p0, p1, snap_tol)
    return up, dn, n_dangling


def _far_vertex(known, p0, p1, tol):
    if p0 is None or p1 is None:
        return p0 or p1
    if _close(known, p0, tol):
        return p1
    if _close(known, p1, tol):
        return p0
    return p1 if _sq(known, p1) >= _sq(known, p0) else p0


def _close(a, b, tol):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _sq(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


_GEOM_TOL_DEG = 0.0005  # ~40 m: a pipe endpoint must lie near its own polyline


def _fix_endpoints(up_xy, dn_xy, line, tol=_GEOM_TOL_DEG):
    """A node coordinate joined by GIS_FeatureKey can be grossly wrong vs the pipe's own
    geometry (data inconsistency / mis-located node), drawing the pipe as a long stray line.
    If an endpoint is far from BOTH polyline ends, move it onto the polyline (the end the
    other endpoint isn't at). Returns (up_xy, dn_xy, n_fixed)."""
    if not line or len(line) < 2:
        return up_xy, dn_xy, 0
    p0, p1 = tuple(line[0][:2]), tuple(line[-1][:2])
    far = lambda xy: min(_sq(xy, p0), _sq(xy, p1)) ** 0.5 > tol
    if far(up_xy) and not far(dn_xy):
        return (p1 if _sq(dn_xy, p0) <= _sq(dn_xy, p1) else p0), dn_xy, 1
    if far(dn_xy) and not far(up_xy):
        return up_xy, (p1 if _sq(up_xy, p0) <= _sq(up_xy, p1) else p0), 1
    if far(up_xy) and far(dn_xy):
        return p0, p1, 1
    return up_xy, dn_xy, 0


def build_london_network(
    mains, manholes, other_nodes, outfalls, *,
    config: LondonNetworkConfig = LondonNetworkConfig(),
) -> LondonNetworkResult:
    mains = _features(mains)
    manhole_feats = _features(manholes)
    other_feats = _features(other_nodes)
    outfall_feats = _features(outfalls)

    # node coordinate index (GIS_FeatureKey -> (lon,lat)) across all three node layers,
    # plus ground elevations from manhole LidElevation.
    coords: Dict[str, Coord] = {}
    ground: List[Tuple[Coord, float]] = []
    for f in manhole_feats + other_feats + outfall_feats:
        key = (f.get("properties") or {}).get("GIS_FeatureKey")
        xy = (f.get("geometry") or {}).get("coordinates")
        if key and xy and len(xy) >= 2:
            coords[key] = (xy[0], xy[1])
            elev = _num((f.get("properties") or {}).get("LidElevation"))
            if elev is not None and elev > 0:
                ground.append(((xy[0], xy[1]), elev))

    # outfalls identified by membership in the outfall LAYER (not by id prefix)
    outfall_keys = {
        (f.get("properties") or {}).get("GIS_FeatureKey")
        for f in outfall_feats if (f.get("properties") or {}).get("GIS_FeatureKey")
    }
    outfall_points = [coords[k] for k in outfall_keys if k in coords]
    label_points = [(xy, _sanitize(key)) for key, xy in coords.items()]

    pipes: List[base.RawPipe] = []
    shape_hist: Dict[str, int] = {}
    inv_type_hist: Dict[str, int] = {}
    n_dangling = n_geom_fixed = 0
    for m in mains:
        p = m.get("properties") or {}
        geom = m.get("geometry") or {}
        shape = p.get("PipeShape") or "UNK"
        shape_hist[shape] = shape_hist.get(shape, 0) + 1
        for k in ("UpstreamInventoryType", "DownstreamInventoryType"):
            t = p.get(k) or "UNK"
            inv_type_hist[t] = inv_type_hist.get(t, 0) + 1
        line = geom.get("coordinates") or []
        up_xy, dn_xy, dangling = resolve_endpoints(
            p.get("UpstreamID"), p.get("DownstreamID"), line, coords, snap_tol=config.snap_tol)
        if up_xy is None or dn_xy is None:
            continue
        up_xy, dn_xy, gf = _fix_endpoints(up_xy, dn_xy, line)
        n_dangling += dangling
        n_geom_fixed += gf
        diameter_mm = _num(p.get("Diameter"))
        pipes.append(base.RawPipe(
            name=_sanitize(p.get("GIS_FeatureKey") or p.get("OBJECTID")),
            end_a=up_xy, end_b=dn_xy,
            inv_a=_num(p.get("UpstreamInvert")), inv_b=_num(p.get("DownstreamInvert")),
            diameter_m=(diameter_mm / 1000.0) if diameter_mm and diameter_mm > 0 else None,
            roughness_n=material_roughness(p.get("Material"), config),
            length_m=_num(p.get("Length")),
        ))

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground, label_points=label_points,
        config=base.AssembleConfig(
            min_slope=config.min_slope, default_max_depth_m=config.default_max_depth_m,
            default_diameter_m=config.default_diameter_m, default_roughness=config.default_roughness,
            outfall_link_len_m=config.outfall_link_len_m),
    )
    diagnostics = {**result.diagnostics, "n_dangling_nodes": n_dangling,
                   "n_geom_fixed": n_geom_fixed, "shape_histogram": shape_hist,
                   "inventory_type_histogram": inv_type_hist, "n_mains_in": len(mains)}
    return LondonNetworkResult(network=result.network, diagnostics=diagnostics)


# --- subcatchments (catch-basin + parcel/building, ADR 0005; UTM 17N) ------------
def delineate_catchbasin_subcatchments(network, catchbasins, parcels, buildings, aoi,
                                       *, config: CatchbasinSubcatchmentConfig = CatchbasinSubcatchmentConfig()):
    return base.delineate_catchbasin_subcatchments(
        network, catchbasins, parcels, buildings, aoi, crs=LONDON_CRS, config=config)
