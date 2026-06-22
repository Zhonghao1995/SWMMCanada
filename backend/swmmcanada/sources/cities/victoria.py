"""City of Victoria storm-drain adapter: fetch + network assembly + subcatchments.

Victoria publishes EXPLICIT pipe topology (Upstream/DownstreamNodeID joined to point layers by
``AssetID``). This adapter resolves each main's endpoint coordinates (with a polyline fallback
for ~10% dangling refs) and hands canonical pipes to the shared ``cities.base`` assembler. It
also fetches catch basins + parcels + buildings for the ADR 0005 subcatchment method (UTM 10N).
The build target is circular-only, so pipes map to an equivalent circular diameter; the original
``CrossSectionShape`` is kept in diagnostics. See ``tests/fixtures/victoria/README.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.base import (  # re-exported for callers
    CatchbasinSubcatchmentConfig,
    material_roughness as _material_roughness,
)

Coord = Tuple[float, float]
_VICTORIA_CRS = "EPSG:32610"

# --- ArcGIS layers (see fixtures/victoria/README.md) ----------------------------
BASE = "https://maps.victoria.ca/server/rest/services/OpenData/OpenData_StormDrain/MapServer"
MAINS, MANHOLES, FITTINGS, OUTFALLS, CATCHBASINS = 10, 4, 3, 5, 1
LAND_BASE = "https://maps.victoria.ca/server/rest/services/OpenData/OpenData_Land/MapServer"
LAND_PARCELS, LAND_BUILDINGS = 5, 1            # Parcels (Folio based), Buildings
_PREFIX_LAYER = {"DMH": MANHOLES, "DFG": FITTINGS, "DOF": OUTFALLS}
_PAGE_SIZE, _ID_CHUNK = 1000, 80


class VicMapClient:
    """Live ArcGIS client: thin GET-as-JSON wrapper (mirrors GeoMetClient)."""

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def get_json(self, url: str, params: dict) -> dict:
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


# --- fetch ----------------------------------------------------------------------
def _payload_features(payload: dict) -> list:
    return (payload or {}).get("features") or []


def fetch_victoria_storm(bbox, *, client=None) -> dict:
    """Storm network intersecting ``bbox`` (EPSG:4326 tuple, or object with ``.bbox``):
    STM mains by envelope, then referenced nodes BY AssetID. Returns
    ``{"mains", "manholes", "fittings", "outfalls"}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or VicMapClient()
    mains = _fetch_mains(bbox, client)
    by_layer = _node_ids_by_layer(mains)
    result = {"mains": mains, "manholes": [], "fittings": [], "outfalls": []}
    for layer, key in {MANHOLES: "manholes", FITTINGS: "fittings", OUTFALLS: "outfalls"}.items():
        result[key] = _fetch_nodes_by_assetid(layer, by_layer.get(layer, []), client)
    return result


def _fetch_mains(bbox, client) -> list:
    min_lon, min_lat, max_lon, max_lat = bbox
    url = f"{BASE}/{MAINS}/query"
    features, offset = [], 0
    while True:
        params = {
            "where": "WaterType='STM'", "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "true",
            "outSR": 4326, "f": "geojson", "resultOffset": offset, "resultRecordCount": _PAGE_SIZE,
        }
        payload = client.get_json(url, params)
        page = _payload_features(payload)
        features.extend(page)
        if not payload.get("exceededTransferLimit") or not page:
            break
        offset += len(page)
    return features


def _node_ids_by_layer(mains) -> dict:
    by_layer = {MANHOLES: [], FITTINGS: [], OUTFALLS: []}
    seen = set()
    for feat in mains:
        props = feat.get("properties") or {}
        for key in ("UpstreamNodeID", "DownstreamNodeID"):
            node_id = props.get(key)
            if not node_id:
                continue
            layer = _PREFIX_LAYER.get(node_id[:3])
            if layer is None or node_id in seen:
                continue
            seen.add(node_id)
            by_layer[layer].append(node_id)
    return by_layer


def _fetch_nodes_by_assetid(layer: int, asset_ids, client) -> list:
    if not asset_ids:
        return []
    url = f"{BASE}/{layer}/query"
    features, seen = [], set()
    for start in range(0, len(asset_ids), _ID_CHUNK):
        in_list = ",".join(f"'{a}'" for a in asset_ids[start: start + _ID_CHUNK])
        params = {"where": f"AssetID IN ({in_list})", "outFields": "*", "returnGeometry": "true",
                  "outSR": 4326, "f": "geojson"}
        for feat in _payload_features(client.get_json(url, params)):
            asset_id = (feat.get("properties") or {}).get("AssetID")
            if asset_id in seen:
                continue
            seen.add(asset_id)
            features.append(feat)
    return features


def _fetch_layer_bbox(base_url, layer, bbox, client, where="1=1") -> list:
    min_lon, min_lat, max_lon, max_lat = bbox
    url = f"{base_url}/{layer}/query"
    features, offset = [], 0
    while True:
        params = {
            "where": where, "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "true",
            "outSR": 4326, "f": "geojson", "resultOffset": offset, "resultRecordCount": _PAGE_SIZE,
        }
        payload = client.get_json(url, params)
        page = _payload_features(payload)
        features.extend(page)
        if not payload.get("exceededTransferLimit") or not page:
            break
        offset += len(page)
    return features


def fetch_victoria_land(bbox, *, client=None) -> dict:
    """Drainage inlets + land units for the parcel/building subcatchment method:
    ``{"catchbasins", "parcels", "buildings"}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or VicMapClient()
    return {
        "catchbasins": _fetch_layer_bbox(BASE, CATCHBASINS, bbox, client),
        "parcels": _fetch_layer_bbox(LAND_BASE, LAND_PARCELS, bbox, client),
        "buildings": _fetch_layer_bbox(LAND_BASE, LAND_BUILDINGS, bbox, client),
    }


# --- network assembly -----------------------------------------------------------
@dataclass(frozen=True)
class VictoriaNetworkConfig:
    min_slope: float = 0.001
    default_max_depth_m: float = 2.0
    default_roughness: float = 0.013
    default_diameter_m: float = 0.30
    outfall_link_len_m: float = 10.0
    snap_tol: float = 1e-6                  # deg; polyline-vertex vs node-point match


@dataclass(frozen=True)
class VictoriaNetworkResult:
    network: "base.NetworkIn"
    diagnostics: dict = field(default_factory=dict)


def material_roughness(material: Optional[str], config: VictoriaNetworkConfig) -> float:
    return _material_roughness(material, config.default_roughness)


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
    """Resolve a main's endpoint coordinates. Known node ids take their point-layer
    coordinate; a dangling id snaps to the far polyline vertex; both dangling ->
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


def build_victoria_network(
    mains, manholes, fittings, outfalls, *, config: VictoriaNetworkConfig = VictoriaNetworkConfig(),
) -> VictoriaNetworkResult:
    mains = _features(mains)
    node_feats = _features(manholes) + _features(fittings) + _features(outfalls)

    coords: Dict[str, Coord] = {}
    ground: List[Tuple[Coord, float]] = []
    for f in node_feats:
        aid = (f.get("properties") or {}).get("AssetID")
        xy = (f.get("geometry") or {}).get("coordinates")
        if aid and xy and len(xy) >= 2:
            coords[aid] = (xy[0], xy[1])
            elev = _num((f.get("properties") or {}).get("Elevation"))
            if elev is not None:
                ground.append(((xy[0], xy[1]), elev))
    outfall_points = [coords[aid] for aid in coords if str(aid).upper().startswith("DOF")]
    label_points = [(xy, _sanitize(aid)) for aid, xy in coords.items()]

    pipes: List[base.RawPipe] = []
    shape_hist: Dict[str, int] = {}
    n_dangling = 0
    for m in mains:
        p = m.get("properties") or {}
        geom = m.get("geometry") or {}
        shape = p.get("CrossSectionShape") or "UNK"
        shape_hist[shape] = shape_hist.get(shape, 0) + 1
        up_xy, dn_xy, dangling = resolve_endpoints(
            p.get("UpstreamNodeID"), p.get("DownstreamNodeID"),
            geom.get("coordinates") or [], coords, snap_tol=config.snap_tol)
        if up_xy is None or dn_xy is None:
            continue
        n_dangling += dangling
        diameter_mm = _num(p.get("Diameter"))
        pipes.append(base.RawPipe(
            name=_sanitize(p.get("AssetID") or p.get("InfrastructureID") or p.get("OBJECTID")),
            end_a=up_xy, end_b=dn_xy,
            inv_a=_num(p.get("UpstreamInvert")), inv_b=_num(p.get("DownstreamInvert")),
            diameter_m=(diameter_mm / 1000.0) if diameter_mm and diameter_mm > 0 else None,
            roughness_n=material_roughness(p.get("Material"), config),
            length_m=_num(p.get("Length_2D")),
        ))

    result = base.assemble_network(
        pipes, outfall_points=outfall_points, ground_points=ground, label_points=label_points,
        config=base.AssembleConfig(
            min_slope=config.min_slope, default_max_depth_m=config.default_max_depth_m,
            default_diameter_m=config.default_diameter_m, default_roughness=config.default_roughness,
            outfall_link_len_m=config.outfall_link_len_m),
    )
    diagnostics = {**result.diagnostics, "n_dangling_nodes": n_dangling,
                   "shape_histogram": shape_hist, "n_mains_in": len(mains)}
    return VictoriaNetworkResult(network=result.network, diagnostics=diagnostics)


# --- subcatchments (catch-basin + parcel/building, ADR 0005; UTM 10N) ------------
def delineate_catchbasin_subcatchments(network, catchbasins, parcels, buildings, aoi,
                                       *, config: CatchbasinSubcatchmentConfig = CatchbasinSubcatchmentConfig()):
    return base.delineate_catchbasin_subcatchments(
        network, catchbasins, parcels, buildings, aoi, crs=_VICTORIA_CRS, config=config)
