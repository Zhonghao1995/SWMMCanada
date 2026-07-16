"""City-agnostic real-network assembly (ADR 0006 — the multi-city base).

Every city adapter resolves its raw pipe layer into a list of `RawPipe` (endpoint
coordinates + inverts + diameter/material/length), plus the known outfall points and any
node ground elevations. `assemble_network` then does ALL the city-independent work:

  * snap pipe endpoints to shared NODES by coordinate (works whether the city gives explicit
    node ids — Victoria — or only geometry — Ottawa);
  * node invert = min of connected pipe-end inverts (gap-filled from neighbours);
  * orient each conduit downhill; enforce the SWMM single-link-outfall rule;
  * emit a build-ready `NetworkIn`.

Also hosts the shared material->roughness table and the catch-basin + parcel/building
subcatchment delineation (ADR 0005), parameterised by metric CRS so any city can use it.
"""
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from swmmcanada.sources import _http
from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn

Coord = Tuple[float, float]


# --- shared ArcGIS fetch helpers (lifted so every city adapter reuses one copy) ----------
class ArcGISClient:
    """Thin ArcGIS REST client: GET a query URL with params, return parsed JSON. Shared by
    every city adapter (Victoria/Ottawa keep their old client names as aliases)."""

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def get_json(self, url: str, params: dict) -> dict:
        return _http.request_with_retry("GET", url, params=params, timeout=self.timeout).json()


def esri_to_geojson(feat: dict) -> dict:
    """Convert an Esri-JSON feature's geometry to a GeoJSON Feature. Some ArcGIS MapServers
    only serve Esri JSON (``f=geojson`` returns empty); geometry-inferred adapters that hit
    those services convert with this. ``paths``->Line/MultiLineString, ``x,y``->Point,
    ``rings``->Polygon; empty geometry -> None."""
    geom = feat.get("geometry") or {}
    g = None
    if geom.get("paths"):
        paths = geom["paths"]
        g = ({"type": "MultiLineString", "coordinates": paths} if len(paths) > 1
             else {"type": "LineString", "coordinates": paths[0]})
    elif "x" in geom and "y" in geom:
        g = {"type": "Point", "coordinates": [geom["x"], geom["y"]]}
    elif geom.get("rings"):
        g = {"type": "Polygon", "coordinates": geom["rings"]}
    return {"type": "Feature", "properties": feat.get("attributes") or {}, "geometry": g}


def fetch_paged(client, url, bbox, *, where: str = "1=1", fmt: str = "geojson",
                out_fields: str = "*", page_size: int = 1000, transform=None) -> list:
    """Drain a paginated ArcGIS bbox-envelope layer query, concatenating every page's
    features. Every city adapter pages the same way: request ``page_size`` features at
    ``resultOffset``, advance by the page returned, and stop when the server no longer
    reports ``exceededTransferLimit`` (or a page comes back empty). ``fmt`` is the response
    format (``geojson``, or ``json`` for MapServers that only serve Esri JSON); ``transform``
    converts each raw feature (e.g. ``esri_to_geojson``). Per-city layer URLs, where-clauses
    and page sizes stay in the adapters."""
    min_lon, min_lat, max_lon, max_lat = bbox
    features, offset = [], 0
    while True:
        params = {
            "where": where, "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": out_fields,
            "returnGeometry": "true", "outSR": 4326, "f": fmt,
            "resultOffset": offset, "resultRecordCount": page_size,
        }
        payload = client.get_json(url, params) or {}
        page = payload.get("features") or []
        features.extend(map(transform, page) if transform else page)
        if not payload.get("exceededTransferLimit") or not page:
            break
        offset += len(page)
    return features


# --- material -> Manning's n (uppercase code match; default when unknown) ----------------
MATERIAL_ROUGHNESS: Dict[str, float] = {
    "PVC": 0.010, "VT": 0.013, "VITC": 0.013, "VC": 0.013,            # clay
    "CONC": 0.013, "CON": 0.013, "RC": 0.013, "CO": 0.013,            # concrete
    "AC": 0.011, "ASB": 0.011, "AbsC": 0.011,                          # asbestos cement
    "PE": 0.011, "HDPE": 0.011,                                        # polyethylene
    "CMP": 0.024, "CSP": 0.024,                                        # corrugated metal
    "DI": 0.013, "CI": 0.013, "CAST": 0.013,                           # iron
    "BR": 0.015, "BRK": 0.015,                                         # brick
    "STL": 0.012, "STEEL": 0.012,                                      # steel
}


def material_roughness(material: Optional[str], default: float = 0.013) -> float:
    if not material:
        return default
    return MATERIAL_ROUGHNESS.get(str(material).strip().upper(), default)


def num(v, *, zero_missing: bool = False) -> Optional[float]:
    """Parse a (possibly string) numeric field to float; ``""``/None/non-numeric -> None.
    ``zero_missing=True`` also maps 0 to None — for cities whose schema uses 0 as the
    missing-data sentinel (their inverts are real elevations far above 0, so a stored 0
    means "not recorded"; cities at sea level keep 0 as a legitimate elevation)."""
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (zero_missing and f == 0) else f


def line_ends(geom) -> Tuple[Optional[Coord], Optional[Coord]]:
    """First/last vertex of a GeoJSON LineString/MultiLineString -> ``((x, y), (x, y))``,
    or ``(None, None)`` for empty/degenerate geometry. MultiLineString parts are flattened
    in order (city layers publish single-part lines; rare multiparts stay contiguous)."""
    coords = (geom or {}).get("coordinates") or []
    if not coords:
        return None, None
    if isinstance(coords[0][0], (list, tuple)):   # MultiLineString -> flatten
        coords = [pt for part in coords for pt in part]
    if len(coords) < 2:
        return None, None
    return tuple(coords[0][:2]), tuple(coords[-1][:2])


# --- canonical pipe + config + result ---------------------------------------------------
@dataclass(frozen=True)
class RawPipe:
    name: str
    end_a: Coord                       # (lon, lat)
    end_b: Coord
    inv_a: Optional[float] = None      # invert at end_a / end_b (m); either may be missing
    inv_b: Optional[float] = None
    diameter_m: Optional[float] = None
    roughness_n: float = 0.013
    length_m: Optional[float] = None    # geodesic from geometry when missing/zero
    shape: Optional[str] = None         # city cross-section code; None -> circular (#130)
    height_m: Optional[float] = None    # real section dims for non-circular shapes
    width_m: Optional[float] = None


# City shape vocab -> SWMM XSECTIONS shape (#130). Unknown/missing -> CIRCULAR, and any
# non-circular shape without BOTH dims falls back to the equivalent-circular pipe.
SHAPE_MAP = {
    "ROUND": "CIRCULAR", "CIRC": "CIRCULAR", "CIRCULAR": "CIRCULAR", "R": "CIRCULAR",
    "RECT": "RECT_CLOSED", "RECTANGULAR": "RECT_CLOSED", "BOX": "RECT_CLOSED",
    "SQUARE": "RECT_CLOSED", "RECT_CLOSED": "RECT_CLOSED",
    "EGG": "EGG", "E": "EGG",
    "ARCH": "ARCH", "CMPA": "ARCH",
    "ELLIPSE": "HORIZ_ELLIPSE", "ELLIPTICAL": "HORIZ_ELLIPSE", "ELH": "HORIZ_ELLIPSE",
    "HORSESHOE": "HORSESHOE", "HS": "HORSESHOE",
    "TRAPEZOIDAL": "TRAPEZOIDAL",
}


def swmm_shape(raw_shape, height_m, width_m):
    """(shape, height_m, width_m) for the ConduitIn: a mapped non-circular shape only when
    the city gave BOTH dimensions, else CIRCULAR with no dims (diameter_m rules)."""
    mapped = SHAPE_MAP.get(str(raw_shape or "").strip().upper())
    if mapped and mapped != "CIRCULAR" and height_m and width_m and height_m > 0 and width_m > 0:
        return mapped, height_m, width_m
    return "CIRCULAR", None, None


@dataclass(frozen=True)
class AssembleConfig:
    snap_decimals: int = 6             # ~0.1 m; endpoints within this round to one node
    min_slope: float = 0.001
    default_cover_depth_m: float = 1.5
    default_max_depth_m: float = 2.0
    default_diameter_m: float = 0.30
    default_roughness: float = 0.013
    outfall_link_len_m: float = 10.0


@dataclass(frozen=True)
class NetworkResult:
    network: NetworkIn
    diagnostics: dict = field(default_factory=dict)


def _haversine_m(a: Coord, b: Coord) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dphi = math.radians(b[1] - a[1])
    dlmb = math.radians(b[0] - a[0])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def assemble_network(
    pipes: List[RawPipe],
    *,
    outfall_points: List[Coord] = (),
    ground_points: List[Tuple[Coord, float]] = (),
    label_points: List[Tuple[Coord, str]] = (),
    config: AssembleConfig = AssembleConfig(),
) -> NetworkResult:
    """Assemble a SWMM `NetworkIn` from canonical pipes by snapping endpoints to nodes.

    outfall_points: known outfall locations (e.g. an outfall layer). ground_points: node
    ground elevations (for max-depth). label_points: preferred node ids by location (e.g. a
    city's asset ids); unlabelled nodes get generated ``N#`` ids.
    """
    def snap(xy: Coord) -> Coord:
        return (round(xy[0], config.snap_decimals), round(xy[1], config.snap_decimals))

    label = {snap(xy): str(lab) for xy, lab in label_points if lab}
    ground = {snap(xy): e for xy, e in ground_points if e is not None}
    outfall_keys_all = {snap(xy) for xy in outfall_points}

    node_xy: Dict[Coord, Coord] = {}
    inv_cands: Dict[Coord, list] = defaultdict(list)
    edges: List[Tuple[str, Coord, Coord, Optional[float], float, float]] = []
    dropped: List[dict] = []

    for p in pipes:
        ka, kb = snap(p.end_a), snap(p.end_b)
        if ka == kb:
            dropped.append({"name": p.name, "reason": "self_loop"})
            continue
        length = p.length_m if (p.length_m and p.length_m > 0) else _haversine_m(p.end_a, p.end_b)
        if length <= 0:
            dropped.append({"name": p.name, "reason": "zero_length"})
            continue
        node_xy.setdefault(ka, p.end_a)
        node_xy.setdefault(kb, p.end_b)
        if p.inv_a is not None:
            inv_cands[ka].append(p.inv_a)
        if p.inv_b is not None:
            inv_cands[kb].append(p.inv_b)
        edges.append((p.name, ka, kb, p.diameter_m, p.roughness_n, length,
                      p.inv_a, p.inv_b, p.shape, p.height_m, p.width_m))

    if not edges:
        return NetworkResult(NetworkIn([], [], []), {"reason": "no usable pipes", "dropped": dropped})

    # node inverts: min of connected pipe-ends, then fill gaps from neighbours / global min
    node_inv: Dict[Coord, Optional[float]] = {k: (min(inv_cands[k]) if inv_cands.get(k) else None) for k in node_xy}
    n_missing = sum(1 for v in node_inv.values() if v is None)
    adj: Dict[Coord, set] = defaultdict(set)
    for _, ka, kb, *_ in edges:
        adj[ka].add(kb)
        adj[kb].add(ka)
    for k in node_xy:
        if node_inv[k] is None:
            neigh = [node_inv[n] for n in adj[k] if node_inv[n] is not None]
            node_inv[k] = min(neigh) if neigh else None
    known = [v for v in node_inv.values() if v is not None]
    fallback = min(known) if known else 0.0
    node_inv = {k: (v if v is not None else fallback) for k, v in node_inv.items()}

    # outfalls: known outfall nodes with one link -> direct; else dedicated single-link outfall
    incident = Counter()
    for _, ka, kb, *_ in edges:
        incident[ka] += 1
        incident[kb] += 1
    outfall_keys = outfall_keys_all & set(node_xy)
    direct, dedicated = set(), []
    for k in outfall_keys:
        (direct.add(k) if incident[k] == 1 else dedicated.append(k))

    # EVERY connected component must be able to drain, else trapped water wrecks the routing
    # mass balance (geometry-inferred networks fragment). Give each outfall-less component a
    # dedicated sink at its lowest node.
    parent = {k: k for k in node_xy}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for _, ka, kb, *_ in edges:
        ra, rb = find(ka), find(kb)
        if ra != rb:
            parent[ra] = rb
    has_outfall = {find(k) for k in direct} | {find(k) for k in dedicated}
    comp_nodes: Dict[Coord, list] = defaultdict(list)
    for k in node_xy:
        comp_nodes[find(k)].append(k)
    for root, nodes in comp_nodes.items():
        if root not in has_outfall:
            dedicated.append(min(nodes, key=lambda k: node_inv[k]))
    n_components = len(comp_nodes)

    seq = {}

    def nid(k: Coord) -> str:
        if k in label:
            return label[k]
        if k not in seq:
            seq[k] = f"N{len(seq) + 1}"
        return seq[k]

    junctions = [
        JunctionIn(
            name=nid(k), invert_m=node_inv[k], x=node_xy[k][0], y=node_xy[k][1],
            max_depth_m=(ground[k] - node_inv[k]) if (k in ground and ground[k] - node_inv[k] > 0)
            else config.default_max_depth_m,
        )
        for k in node_xy if k not in direct
    ]

    conduits: List[ConduitIn] = []
    n_offset_ends = 0
    n_noncircular = 0
    for name, ka, kb, dia, rough, length, inv_a, inv_b, raw_shape, h_m, w_m in edges:
        if kb in direct:
            fr, to = ka, kb
            inv_fr, inv_to = inv_a, inv_b
        elif ka in direct:
            fr, to = kb, ka
            inv_fr, inv_to = inv_b, inv_a
        else:
            if node_inv[ka] >= node_inv[kb]:
                fr, to, inv_fr, inv_to = ka, kb, inv_a, inv_b
            else:
                fr, to, inv_fr, inv_to = kb, ka, inv_b, inv_a
        # #130: pipe invert = node invert + offset. Node inverts are min-of-ends, so a
        # published end elevation above the node bottom is a drop structure the offset
        # preserves; a missing end elevation means offset 0 (today's behaviour).
        inlet_off = max(0.0, inv_fr - node_inv[fr]) if inv_fr is not None else 0.0
        outlet_off = max(0.0, inv_to - node_inv[to]) if inv_to is not None else 0.0
        n_offset_ends += int(inlet_off > 0) + int(outlet_off > 0)
        shape, height_m, width_m = swmm_shape(raw_shape, h_m, w_m)
        n_noncircular += int(shape != "CIRCULAR")
        conduits.append(ConduitIn(
            name=name, from_node=nid(fr), to_node=nid(to), length_m=length,
            diameter_m=dia if (dia and dia > 0) else config.default_diameter_m,
            roughness_n=rough or config.default_roughness,
            inlet_offset_m=round(inlet_off, 3), outlet_offset_m=round(outlet_off, 3),
            shape=shape, height_m=height_m, width_m=width_m,
        ))

    outfalls = [OutfallIn(name=nid(k), invert_m=node_inv[k], x=node_xy[k][0], y=node_xy[k][1]) for k in direct]
    for k in dedicated:
        oname = f"OUT_{nid(k)}"
        outfalls.append(OutfallIn(
            name=oname, invert_m=node_inv[k] - config.min_slope * config.outfall_link_len_m,
            x=node_xy[k][0] + 1e-4, y=node_xy[k][1]))
        conduits.append(ConduitIn(
            name=f"C_{oname}", from_node=nid(k), to_node=oname,
            length_m=config.outfall_link_len_m, diameter_m=config.default_diameter_m,
            roughness_n=config.default_roughness))

    network = NetworkIn(junctions=junctions, outfalls=outfalls, conduits=conduits)
    _assert_invariants(network)
    diagnostics = {
        "n_junctions": len(junctions), "n_outfalls": len(outfalls), "n_conduits": len(conduits),
        "n_nodes": len(node_xy), "n_inverts_gapfilled": n_missing,
        "n_direct_outfalls": len(direct), "n_dedicated_outfalls": len(dedicated),
        "n_components": n_components, "n_dropped": len(dropped), "dropped": dropped[:20],
        "n_offset_ends": n_offset_ends, "n_noncircular": n_noncircular,
    }
    return NetworkResult(network=network, diagnostics=diagnostics)


def _assert_invariants(network: NetworkIn) -> None:
    names = [j.name for j in network.junctions] + [o.name for o in network.outfalls]
    assert all(n and str(n).strip() for n in names), "empty node name"
    assert len(names) == len(set(names)), "duplicate node names"
    node_set = set(names)
    incident = Counter()
    for c in network.conduits:
        assert c.from_node in node_set, f"conduit {c.name} from_node {c.from_node} unknown"
        assert c.to_node in node_set, f"conduit {c.name} to_node {c.to_node} unknown"
        incident[c.from_node] += 1
        incident[c.to_node] += 1
    for o in network.outfalls:
        assert incident[o.name] == 1, f"outfall {o.name} must have exactly one link"


# --- catch-basin + parcel/building subcatchments (ADR 0005), CRS-parameterised -----------
@dataclass(frozen=True)
class CatchbasinSubcatchmentConfig:
    default_slope_pct: float = 1.0
    min_imperv: float = 1.0
    max_imperv: float = 100.0


@dataclass
class _Cell:
    """A subcatchment cell: metric area + its EPSG:4326 polygon. Same shape interface the
    Voronoi delineator returns, so the impervious/outlet loop downstream is identical either way."""
    area_m2: float
    polygon_4326: object

    @property
    def exterior(self):
        return [(float(x), float(y)) for x, y in self.polygon_4326.exterior.coords]


def _all_polygons(geom):
    """All polygon pieces of a geometry (PRD #3). A catch basin's service area can come out
    disconnected (diagonal parcels, an island parcel); keeping every piece — instead of only
    the largest — means the dropped land no longer becomes a blank hole. Each piece becomes
    its own subcatchment draining to the same inlet."""
    from shapely.geometry import Polygon
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        return [g for g in geom.geoms if g.geom_type == "Polygon" and not g.is_empty]
    return []


def _largest_valid(geom):
    """Largest valid, non-empty Polygon of a geometry, repairing self-intersections with
    ``buffer(0)``; returns None when nothing usable remains. Set ops (union/difference) and
    reprojection can leave a cell polygon self-intersecting or empty — the build's geometry
    validator rejects those outright, so every emitted cell is cleaned through this."""
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    polys = [p for p in _all_polygons(geom) if not p.is_empty and p.area > 0]
    return max(polys, key=lambda p: p.area) if polys else None


def _parcel_cells(seeds, parcels, aoi, crs):
    """Subcatchment shapes that follow REAL parcel/lot lines: each parcel is assigned whole to
    its nearest catch basin and dissolved (so cell edges fall on lot lines, not a Voronoi
    bisector); street right-of-way (AOI minus parcels) is split between catch basins by their
    Voronoi cells. Returns {cb_id: [_Cell, ...]} (contiguous pieces per basin — disconnected
    land is kept, not dropped), or {} when no usable parcels — the caller then falls back to
    Voronoi (e.g. Ottawa, which publishes no parcels)."""
    import geopandas as gpd
    import numpy as np
    from pyproj import Transformer
    from shapely.geometry import MultiPoint, Point, shape
    from shapely.ops import transform as shp_transform
    from shapely.ops import unary_union, voronoi_diagram

    pgeoms = [shape(f["geometry"]) for f in (parcels or []) if f.get("geometry")]
    if len(pgeoms) < 2:
        return {}
    to_m = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    aoi_m = shp_transform(to_m, aoi.geometry)

    cb_ids = list(seeds)
    cb_pts = [Point(*to_m(lon, lat)) for (lon, lat) in seeds.values()]
    cb_xy = np.array([[p.x, p.y] for p in cb_pts])

    par = gpd.GeoSeries(pgeoms, crs="EPSG:4326").to_crs(crs)
    par = par[par.notna() & ~par.is_empty]
    par = par[par.intersects(aoi_m)]
    if len(par) < 2:
        return {}

    # assign each parcel (whole) to its nearest catch basin by centroid
    assign = {cb_id: [] for cb_id in cb_ids}
    for geom in par.geometry:
        c = geom.centroid
        i = int(((cb_xy[:, 0] - c.x) ** 2 + (cb_xy[:, 1] - c.y) ** 2).argmin())
        clipped = geom.intersection(aoi_m)
        if not clipped.is_empty:
            assign[cb_ids[i]].append(clipped)

    # street right-of-way = AOI minus parcels, split between catch basins by their Voronoi cells
    slivers = {cb_id: None for cb_id in cb_ids}
    streets = aoi_m.difference(unary_union(list(par.geometry)))
    if not streets.is_empty:
        for cell in voronoi_diagram(MultiPoint(cb_pts), envelope=aoi_m).geoms:
            owner = next((cb_ids[i] for i, p in enumerate(cb_pts) if cell.covers(p)), None)
            if owner is not None:
                s = streets.intersection(cell)
                if not s.is_empty:
                    slivers[owner] = s

    cells = {}
    for cb_id in cb_ids:
        parts = list(assign[cb_id])
        if slivers[cb_id] is not None:
            parts.append(slivers[cb_id])
        if not parts:
            continue
        pieces = [p for p in _all_polygons(unary_union(parts)) if p.area > 0]
        if pieces:
            cells[cb_id] = [_Cell(area_m2=p.area, polygon_4326=shp_transform(to_ll, p)) for p in pieces]
    return cells


def _shape_cells(seeds, parcels, aoi, crs):
    """SHAPING seam: seeds -> repaired per-catchbasin cell polygons (issue #3 swaps this
    step — e.g. DEM delineation — without touching imperviousness attribution).

    Chooses the shape source as before: parcel-shaped cells along real lot lines
    (``_parcel_cells``) when usable parcels exist, else Voronoi cells around the seeds.
    Owns cell geometry repair: each piece is cleaned in the metric CRS (``_largest_valid``);
    pieces that are empty, under 1 m², or whose stored EPSG:4326 ring fails the metric
    round-trip (float precision) are dropped. Returns ``(pieces, method, n_dropped)`` where
    ``pieces`` is ``[(cb_id, piece_index, poly_m, exterior_4326), ...]`` in seed order —
    ``piece_index`` keeps the pre-drop numbering so split-piece names stay stable — and
    ``method`` is ``"parcel"`` or ``"voronoi"``."""
    from pyproj import Transformer
    from shapely.geometry import Polygon
    from shapely.ops import transform as shp_transform

    from swmmcanada.network.subcatchments import delineate_subcatchments

    cells = _parcel_cells(seeds, parcels, aoi, crs)        # {cb_id: [pieces]} along real parcels
    method = "parcel" if cells else "voronoi"              # fall back to Voronoi where none exist
    if not cells:                                          # Voronoi gives one cell per seed
        cells = {cb_id: [c] for cb_id, c in delineate_subcatchments(seeds, aoi.geometry).items()}

    to_m = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform

    pieces, n_dropped = [], 0
    for cb_id, piece_list in cells.items():
        for i, cell in enumerate(piece_list):
            if cell.area_m2 <= 0:
                continue
            # Repair the cell polygon (set ops + reprojection can leave it self-intersecting or
            # empty) so the emitted geometry is always valid; drop empties and sub-1m² slivers.
            poly_m = _largest_valid(shp_transform(to_m, cell.polygon_4326))
            if poly_m is None or poly_m.area < 1.0:
                n_dropped += 1
                continue
            exterior = [(float(x), float(y)) for x, y in shp_transform(to_ll, poly_m).exterior.coords]
            # Guard the validator's exact check: the stored EPSG:4326 ring must reproject back to a
            # valid, non-empty metric polygon. A rare sliver is valid in metric yet flips invalid
            # through the 4326 round-trip (float precision) — drop it rather than ship it.
            check_m = shp_transform(to_m, Polygon(exterior))
            if check_m.is_empty or not check_m.is_valid:
                n_dropped += 1
                continue
            pieces.append((cb_id, i, poly_m, exterior))
    return pieces, method, n_dropped


def _impervious_fraction(cell_poly, parcels_gdf, parcels_sidx, buildings_gdf, buildings_sidx,
                         *, config: CatchbasinSubcatchmentConfig = CatchbasinSubcatchmentConfig()):
    """IMPERVIOUSNESS seam: percent imperviousness of one metric cell polygon — roofs
    (buildings clipped to the cell) plus road right-of-way (cell minus parcels) as a share
    of cell area, clamped to [min_imperv, max_imperv]. Pure geometry on prebuilt
    GeoDataFrames + spatial indexes, so it is unit-testable without any Voronoi/shaping.

    Returns ``(pct, parcel_based)``: with no parcels intersecting the cell there is no
    land-use evidence, so pct = max_imperv and ``parcel_based`` is False (the caller then
    leaves the cell out of ``imperv_map``)."""
    from shapely.ops import unary_union

    def query_union(g, sidx):
        if sidx is None or not len(g):
            return None
        idx = sidx.query(cell_poly, predicate="intersects")
        return unary_union(g.geometry.iloc[idx].tolist()) if len(idx) else None

    par_local = query_union(parcels_gdf, parcels_sidx)
    if par_local is None or par_local.is_empty:
        return config.max_imperv, False
    roofs = query_union(buildings_gdf, buildings_sidx)
    roofs = roofs.intersection(cell_poly) if roofs is not None else None
    # Evidence gate (F-004/ADR 0024 §3, mirroring the synthesis-side ADR 0023 rule): a
    # parcel-covered cell with NO mapped roof must NOT override the land-cover estimate —
    # "fully parcelled + building layer gap" used to collapse a 60% residential cell to
    # the 1% clamp. No roof evidence -> parcel_based=False and the raster value stands.
    if roofs is None or roofs.is_empty:
        return config.max_imperv, False
    roads = cell_poly.difference(par_local)
    parts = [g for g in (roofs, roads) if g is not None and not g.is_empty]
    area = unary_union(parts).area if parts else 0.0
    return max(config.min_imperv, min(config.max_imperv, 100.0 * area / cell_poly.area)), True


def merge_secondary_system(primary: NetworkIn, secondary: NetworkIn, *, prefix: str,
                           system: str) -> NetworkIn:
    """One model, N tagged systems (ADR 0011): graft ``secondary`` into ``primary`` as a
    disconnected subgraph — every element renamed with ``prefix`` (collision-free) and
    tagged ``system``. The primary's elements are untouched."""
    from dataclasses import replace as _rep

    js = [_rep(j, name=f"{prefix}{j.name}", system=system) for j in secondary.junctions]
    os_ = [_rep(o, name=f"{prefix}{o.name}", system=system) for o in secondary.outfalls]
    cs = [_rep(c, name=f"{prefix}{c.name}", from_node=f"{prefix}{c.from_node}",
               to_node=f"{prefix}{c.to_node}", system=system) for c in secondary.conduits]
    return NetworkIn(junctions=list(primary.junctions) + js,
                     outfalls=list(primary.outfalls) + os_,
                     conduits=list(primary.conduits) + cs)


def _outlet_resolver(network: NetworkIn, crs: str):
    """``(lon, lat) -> node name``: the nearer endpoint of the NEAREST conduit, measured in
    the city's metric CRS. A catch basin's lead taps the closest main, so its outlet must
    sit on that pipe — the globally nearest node can belong to a parallel branch and
    mis-route the whole cell (the outlet_distance diagnostic exists for exactly this).
    Falls back to nearest node when the network has no usable conduits."""
    import numpy as np

    from swmmcanada.geo.crs import lonlat_projector

    to_m = lonlat_projector(crs)
    nodes = {n.name: to_m(n.x, n.y) for n in list(network.junctions) + list(network.outfalls)}

    ends = [(nodes[c.from_node], nodes[c.to_node], c.from_node, c.to_node)
            for c in network.conduits if c.from_node in nodes and c.to_node in nodes]
    if not ends:
        names = list(nodes)
        coords = np.array([nodes[n] for n in names])

        def nearest_node(xy):
            p = np.asarray(to_m(*xy))
            return names[int(((coords - p) ** 2).sum(axis=1).argmin())]

        return nearest_node

    A = np.array([e[0] for e in ends])
    B = np.array([e[1] for e in ends])
    AB = B - A
    L2 = (AB ** 2).sum(axis=1)
    L2[L2 == 0] = 1e-12
    end_names = [(e[2], e[3]) for e in ends]

    def resolver(xy):
        p = np.asarray(to_m(*xy))
        t = np.clip(((p - A) * AB).sum(axis=1) / L2, 0.0, 1.0)
        d2 = ((A + t[:, None] * AB - p) ** 2).sum(axis=1)
        k = int(d2.argmin())
        fa, fb = end_names[k]
        return fa if ((A[k] - p) ** 2).sum() <= ((B[k] - p) ** 2).sum() else fb

    return resolver


def delineate_catchbasin_subcatchments(
    network: NetworkIn, catchbasins, parcels, buildings, aoi, *, crs: str = "EPSG:32610",
    config: CatchbasinSubcatchmentConfig = CatchbasinSubcatchmentConfig(),
):
    """Voronoi seeded by REAL catch basins; impervious = roofs (buildings) + road
    right-of-way (cell - parcels); outlet = nearest network node. Returns
    (subcatchments, imperv_map, diagnostics). `crs` is the metric CRS for the city.
    Orchestration only: ``_shape_cells`` shapes + repairs the cells, ``_impervious_fraction``
    attributes each cell's imperviousness."""
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import shape

    from swmmcanada.build.models import SubcatchmentIn

    if not network.junctions:
        return [], {}, {"reason": "no network junctions"}
    seeds = {}
    for i, f in enumerate(catchbasins or []):
        g = (f.get("geometry") or {}).get("coordinates")
        if g and len(g) >= 2:
            p = f.get("properties") or {}
            seeds[str(p.get("AssetID") or p.get("InfrastructureID") or p.get("OBJECTID") or f"CB{i}")] = (g[0], g[1])
    if len(seeds) < 2:
        return [], {}, {"reason": "insufficient catch basins", "n_catchbasins": len(seeds)}

    cells, shape_method, n_dropped = _shape_cells(seeds, parcels, aoi, crs)

    outlet_of = _outlet_resolver(network, crs)

    def gdf(geoms):
        s = gpd.GeoSeries(geoms, crs="EPSG:4326") if geoms else gpd.GeoSeries([], crs="EPSG:4326")
        return gpd.GeoDataFrame(geometry=s).to_crs(crs)

    par = gdf([shape(f["geometry"]) for f in (parcels or []) if f.get("geometry")])
    bld = gdf([shape(f["geometry"]) for f in (buildings or []) if f.get("geometry")])
    par_sidx = par.sindex if len(par) else None
    bld_sidx = bld.sindex if len(bld) else None

    subs, imperv_map, n_parcel, n_split = [], {}, 0, 0
    for cb_id, i, poly_m, exterior in cells:
        area_m2 = poly_m.area
        name = f"S_{cb_id}" if i == 0 else f"S_{cb_id}__{i + 1}"   # split pieces -> same outlet
        if i > 0:
            n_split += 1
        imperv, parcel_based = _impervious_fraction(poly_m, par, par_sidx, bld, bld_sidx, config=config)
        if parcel_based:
            imperv_map[name] = imperv
            n_parcel += 1
        subs.append(SubcatchmentIn(
            name=name, outlet_node=outlet_of(seeds[cb_id]), area_ha=area_m2 / 1e4,
            pct_imperv=imperv, width_m=math.sqrt(area_m2),
            pct_slope=config.default_slope_pct, polygon=exterior))
    diag = {"method": f"catchbasin+parcel/building ({shape_method}-shaped)", "n_catchbasins": len(seeds),
            "n_subcatchments": len(subs), "n_split_pieces": n_split, "n_dropped_invalid": n_dropped,
            "n_parcel_based_imperv": n_parcel,
            "n_parcels": int(len(par)), "n_buildings": int(len(bld)),
            "mean_imperv": round(sum(imperv_map.values()) / len(imperv_map), 1) if imperv_map else None}
    return subs, imperv_map, diag
