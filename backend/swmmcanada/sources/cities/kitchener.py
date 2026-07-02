"""Kitchener / Waterloo / Cambridge / Region of Waterloo storm-drain adapter.

The Region of Waterloo open-data org (one hosted FeatureServer org) publishes EXPLICIT pipe
topology: each ``Storm_Pipes`` feature carries integer ``UP_STMMANHOLEID`` / ``DN_STMMANHOLEID``
that join to a single ``Storm_Manholes`` layer keyed by ``STMMANHOLEID`` (simpler than Victoria's
DMH/DFG/DOF-prefixed multi-layer scheme). A sentinel id of ``-1`` (and any id absent from the
manhole layer) means the endpoint is NOT a manhole — it drains to an outlet/catch-basin. Those
dangling endpoints fall back to the pipe's own polyline vertices, which (confirmed) coincide
exactly with the manhole points: ``line[0]`` == upstream end, ``line[-1]`` == downstream end. So
the topology is doubly recoverable (ids + geometry) and this adapter resolves it either way before
handing canonical pipes to the shared ``cities.base`` assembler.

The single ``OWNERSHIP`` field spans KITCHENER / WATERLOO / CAMBRIDGE / REGION, so one feed covers
every municipality in the region. Pipe ``UP_INVERT`` / ``DN_INVERT`` are real, populated double
metres (verified 2026-06-22) — no inverts are synthesized. ``WIDTH`` / ``HEIGHT`` are millimetres
(circular pipes have WIDTH==HEIGHT) and map to an equivalent circular diameter; the build target is
circular-only, so the original ``PIPE_SHAPE`` is kept in diagnostics.

NOTE: this org publishes NO parcel polygons (``Property_Ownership_Public`` is POINT geometry, civic
addresses only), so ``fetch_kitchener_land`` returns ``parcels: []`` and the parcel/building
subcatchment delineation falls back to Voronoi (as it does for Ottawa). Buildings
(``Building_Outlines``, polygons) are available. See ``tests/fixtures/kitchener/README.md``.
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
KITCHENER_CRS = "EPSG:32617"          # UTM 17N — covers the Region of Waterloo

# --- ArcGIS layers (see fixtures/kitchener/README.md) ---------------------------
ORG = "https://services1.arcgis.com/qAo1OsXi67t7XgmS/arcgis/rest/services"
PIPES = f"{ORG}/Storm_Pipes/FeatureServer/0"
MANHOLES = f"{ORG}/Storm_Manholes/FeatureServer/0"
OUTLETS = f"{ORG}/Storm_Outlets/FeatureServer/0"
CATCHBASINS = f"{ORG}/Storm_Catchbasins/FeatureServer/0"
BUILDINGS = f"{ORG}/Building_Outlines/FeatureServer/0"
# Property_Ownership_Public is POINT geometry (no parcel polygons published) -> none.
PARCELS = None

_PAGE_SIZE, _ID_CHUNK = 1000, 80


# The thin GET-as-JSON client lives in cities.base (Phase 0); keep a local alias.
KitchenerMapClient = base.ArcGISClient


# --- fetch ----------------------------------------------------------------------
def _payload_features(payload: dict) -> list:
    return (payload or {}).get("features") or []


def _fetch_layer_bbox(layer_url: str, bbox, client, *, where: str = "1=1", out_fields: str = "*") -> list:
    """Paginated envelope-intersect query against one FeatureServer layer (f=geojson)."""
    return base.fetch_paged(client, f"{layer_url}/query", bbox,
                            where=where, out_fields=out_fields, page_size=_PAGE_SIZE)


def _fetch_manholes_by_id(manhole_ids, client) -> list:
    """Fetch manhole points BY STMMANHOLEID (chunked IN-list), not by bbox — mirrors Victoria,
    so a pipe whose far node sits just outside the envelope is still resolvable."""
    ids = [int(m) for m in manhole_ids if m is not None and int(m) > 0]
    if not ids:
        return []
    url = f"{MANHOLES}/query"
    features, seen = [], set()
    for start in range(0, len(ids), _ID_CHUNK):
        in_list = ",".join(str(i) for i in ids[start: start + _ID_CHUNK])
        params = {"where": f"STMMANHOLEID IN ({in_list})", "outFields": "*", "returnGeometry": "true",
                  "outSR": 4326, "f": "geojson"}
        for feat in _payload_features(client.get_json(url, params)):
            mid = (feat.get("properties") or {}).get("STMMANHOLEID")
            if mid in seen:
                continue
            seen.add(mid)
            features.append(feat)
    return features


def _referenced_manhole_ids(pipes) -> list:
    ids, seen = [], set()
    for feat in pipes:
        props = feat.get("properties") or {}
        for key in ("UP_STMMANHOLEID", "DN_STMMANHOLEID"):
            mid = props.get(key)
            if mid is None or int(mid) <= 0 or mid in seen:
                continue
            seen.add(mid)
            ids.append(mid)
    return ids


def fetch_kitchener_storm(bbox, *, client=None) -> dict:
    """Storm network intersecting ``bbox`` (EPSG:4326 tuple, or object with ``.bbox``): pipes by
    envelope, then the referenced manholes BY STMMANHOLEID, plus outlets by envelope. Returns
    ``{"pipes", "manholes", "outlets"}`` (lists of GeoJSON Features)."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KitchenerMapClient()
    pipes = _fetch_layer_bbox(PIPES, bbox, client)
    manholes = _fetch_manholes_by_id(_referenced_manhole_ids(pipes), client)
    outlets = _fetch_layer_bbox(OUTLETS, bbox, client)
    return {"pipes": pipes, "manholes": manholes, "outlets": outlets}


def fetch_kitchener_land(bbox, *, client=None) -> dict:
    """Drainage inlets + land units for the subcatchment method:
    ``{"catchbasins", "parcels", "buildings"}`` (lists of GeoJSON Features). ``parcels`` is always
    empty — the Region of Waterloo org publishes no parcel polygons — so delineation falls back to
    catch-basin Voronoi."""
    if hasattr(bbox, "bbox"):
        bbox = bbox.bbox
    client = client or KitchenerMapClient()
    return {
        "catchbasins": _fetch_layer_bbox(CATCHBASINS, bbox, client),
        "parcels": [],
        "buildings": _fetch_layer_bbox(BUILDINGS, bbox, client),
    }


# --- network assembly -----------------------------------------------------------
@dataclass(frozen=True)
class KitchenerNetworkConfig:
    min_slope: float = 0.001
    default_max_depth_m: float = 2.0
    default_roughness: float = 0.013
    default_diameter_m: float = 0.30
    outfall_link_len_m: float = 10.0


@dataclass(frozen=True)
class KitchenerNetworkResult:
    network: "base.NetworkIn"
    diagnostics: dict = field(default_factory=dict)


def material_roughness(material: Optional[str], config: KitchenerNetworkConfig) -> float:
    return _material_roughness(material, config.default_roughness)


def _features(layer) -> List[dict]:
    if layer is None:
        return []
    if isinstance(layer, dict):
        return list(layer.get("features", []))
    return list(layer)


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_id(value) -> Optional[int]:
    """A manhole id usable for a join: a positive integer. ``-1`` (sentinel) / 0 / None -> None."""
    if value is None or value == "":
        return None
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def resolve_endpoints(up_id, dn_id, line, coords):
    """Resolve a pipe's endpoint coordinates from integer manhole ids, falling back to the
    polyline. Kitchener geometry is consistent (``line[0]`` is the upstream end, ``line[-1]`` the
    downstream end, each coinciding with its manhole), so a dangling id (``-1`` or absent from the
    manhole layer) simply takes the corresponding polyline vertex. Returns
    ``(up_xy, dn_xy, n_dangling)``; either coord may be None only when geometry is also missing."""
    p0 = tuple(line[0][:2]) if line else None
    p1 = tuple(line[-1][:2]) if line else None
    up = coords.get(_int_id(up_id))
    dn = coords.get(_int_id(dn_id))
    n_dangling = int(up is None) + int(dn is None)
    if up is None:
        up = p0
    if dn is None:
        dn = p1
    return up, dn, n_dangling


def build_kitchener_network(
    pipes, manholes, outlets, *, config: KitchenerNetworkConfig = KitchenerNetworkConfig(),
) -> KitchenerNetworkResult:
    """Assemble the Region-of-Waterloo storm network from explicit integer manhole-id topology."""
    pipes = _features(pipes)
    manhole_feats = _features(manholes)
    outlet_feats = _features(outlets)

    coords: Dict[int, Coord] = {}
    ground: List[Tuple[Coord, float]] = []
    label_points: List[Tuple[Coord, str]] = []
    for f in manhole_feats:
        mid = _int_id((f.get("properties") or {}).get("STMMANHOLEID"))
        xy = (f.get("geometry") or {}).get("coordinates")
        if mid is None or not xy or len(xy) < 2:
            continue
        c = (xy[0], xy[1])
        coords[mid] = c
        label_points.append((c, str(mid)))
        elev = _num((f.get("properties") or {}).get("COVER_ELEVATION"))
        if elev is not None:
            ground.append((c, elev))

    # Outlets are known drain points: their PIPE_INVERT seeds node inverts and their location is a
    # candidate outfall. A dangling pipe end (DN id == -1) coincides with the outlet point, so the
    # base assembler snaps them together and the outlet becomes the component's outfall.
    outfall_points: List[Coord] = []
    for f in outlet_feats:
        xy = (f.get("geometry") or {}).get("coordinates")
        if xy and len(xy) >= 2:
            outfall_points.append((xy[0], xy[1]))

    raw_pipes: List[base.RawPipe] = []
    shape_hist: Dict[str, int] = {}
    n_dangling = 0
    for m in pipes:
        p = m.get("properties") or {}
        line = (m.get("geometry") or {}).get("coordinates") or []
        shape = p.get("PIPE_SHAPE") or "UNK"
        shape_hist[shape] = shape_hist.get(shape, 0) + 1
        up_xy, dn_xy, dangling = resolve_endpoints(
            p.get("UP_STMMANHOLEID"), p.get("DN_STMMANHOLEID"), line, coords)
        if up_xy is None or dn_xy is None:
            continue
        n_dangling += dangling
        # WIDTH/HEIGHT are millimetres; circular pipes have WIDTH==HEIGHT. Use the max as the
        # equivalent circular diameter (so box/elliptical sizes are not understated).
        width_mm = _num(p.get("WIDTH"))
        height_mm = _num(p.get("HEIGHT"))
        dims = [d for d in (width_mm, height_mm) if d and d > 0]
        diameter_m = (max(dims) / 1000.0) if dims else None
        raw_pipes.append(base.RawPipe(
            name=str(p.get("STMPIPEID") or p.get("OBJECTID")),
            end_a=up_xy, end_b=dn_xy,
            inv_a=_num(p.get("UP_INVERT")), inv_b=_num(p.get("DN_INVERT")),
            diameter_m=diameter_m,
            roughness_n=material_roughness(p.get("MATERIAL"), config),
            length_m=_num(p.get("LENGTH")),
        ))

    result = base.assemble_network(
        raw_pipes, outfall_points=outfall_points, ground_points=ground, label_points=label_points,
        config=base.AssembleConfig(
            min_slope=config.min_slope, default_max_depth_m=config.default_max_depth_m,
            default_diameter_m=config.default_diameter_m, default_roughness=config.default_roughness,
            outfall_link_len_m=config.outfall_link_len_m),
    )
    diagnostics = {**result.diagnostics, "n_dangling_nodes": n_dangling,
                   "shape_histogram": shape_hist, "n_pipes_in": len(pipes),
                   "n_outlets_in": len(outlet_feats)}
    return KitchenerNetworkResult(network=result.network, diagnostics=diagnostics)


# --- subcatchments (catch-basin + building, ADR 0005; UTM 17N; Voronoi fallback) -------------
def delineate_catchbasin_subcatchments(network, catchbasins, parcels, buildings, aoi,
                                       *, config: CatchbasinSubcatchmentConfig = CatchbasinSubcatchmentConfig()):
    return base.delineate_catchbasin_subcatchments(
        network, catchbasins, parcels, buildings, aoi, crs=KITCHENER_CRS, config=config)
