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

from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn

Coord = Tuple[float, float]


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
        edges.append((p.name, ka, kb, p.diameter_m, p.roughness_n, length))

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
    for name, ka, kb, dia, rough, length in edges:
        if kb in direct:
            fr, to = ka, kb
        elif ka in direct:
            fr, to = kb, ka
        else:
            fr, to = (ka, kb) if node_inv[ka] >= node_inv[kb] else (kb, ka)
        conduits.append(ConduitIn(
            name=name, from_node=nid(fr), to_node=nid(to), length_m=length,
            diameter_m=dia if (dia and dia > 0) else config.default_diameter_m,
            roughness_n=rough or config.default_roughness,
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


def _largest_polygon(geom):
    """Keep the main polygon (drop scattered slivers) so each cell is one contiguous shape."""
    from shapely.geometry import Polygon
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        polys = [geom]
    elif hasattr(geom, "geoms"):
        polys = [g for g in geom.geoms if g.geom_type == "Polygon"]
    else:
        polys = []
    return max(polys, key=lambda g: g.area, default=None)


def _parcel_cells(seeds, parcels, aoi, crs):
    """Subcatchment shapes that follow REAL parcel/lot lines: each parcel is assigned whole to
    its nearest catch basin and dissolved (so cell edges fall on lot lines, not a Voronoi
    bisector); street right-of-way (AOI minus parcels) is split between catch basins by their
    Voronoi cells. Returns {cb_id: _Cell}, or {} when no usable parcels — the caller then falls
    back to Voronoi (e.g. Ottawa, which publishes no parcels)."""
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
        poly_m = _largest_polygon(unary_union(parts))
        if poly_m is None or poly_m.area <= 0:
            continue
        cells[cb_id] = _Cell(area_m2=poly_m.area, polygon_4326=shp_transform(to_ll, poly_m))
    return cells


def delineate_catchbasin_subcatchments(
    network: NetworkIn, catchbasins, parcels, buildings, aoi, *, crs: str = "EPSG:32610",
    config: CatchbasinSubcatchmentConfig = CatchbasinSubcatchmentConfig(),
):
    """Voronoi seeded by REAL catch basins; impervious = roofs (buildings) + road
    right-of-way (cell - parcels); outlet = nearest network node. Returns
    (subcatchments, imperv_map, diagnostics). `crs` is the metric CRS for the city."""
    import geopandas as gpd
    import numpy as np
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    from shapely.ops import unary_union

    from swmmcanada.build.models import SubcatchmentIn
    from swmmcanada.network.subcatchments import delineate_subcatchments

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

    cells = _parcel_cells(seeds, parcels, aoi, crs)        # shape follows real parcels (Victoria)
    shape_method = "parcel" if cells else "voronoi"        # fall back to Voronoi where none exist
    if not cells:
        cells = delineate_subcatchments(seeds, aoi.geometry)
    jnames = [j.name for j in network.junctions]
    jcoord = np.array([[j.x, j.y] for j in network.junctions])

    def nearest_node(xy):
        d = (jcoord[:, 0] - xy[0]) ** 2 + (jcoord[:, 1] - xy[1]) ** 2
        return jnames[int(d.argmin())]

    to_m = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform

    def gdf(geoms):
        s = gpd.GeoSeries(geoms, crs="EPSG:4326") if geoms else gpd.GeoSeries([], crs="EPSG:4326")
        return gpd.GeoDataFrame(geometry=s).to_crs(crs)

    par = gdf([shape(f["geometry"]) for f in (parcels or []) if f.get("geometry")])
    bld = gdf([shape(f["geometry"]) for f in (buildings or []) if f.get("geometry")])
    par_sidx = par.sindex if len(par) else None
    bld_sidx = bld.sindex if len(bld) else None

    def query_union(g, sidx, poly):
        if sidx is None or not len(g):
            return None
        idx = sidx.query(poly, predicate="intersects")
        return unary_union(g.geometry.iloc[idx].tolist()) if len(idx) else None

    subs, imperv_map, n_parcel = [], {}, 0
    for cb_id, cell in cells.items():
        if cell.area_m2 <= 0:
            continue
        poly_m = shp_transform(to_m, cell.polygon_4326)
        name = f"S_{cb_id}"
        imperv = config.max_imperv
        par_local = query_union(par, par_sidx, poly_m)
        if par_local is not None and not par_local.is_empty:
            roofs = query_union(bld, bld_sidx, poly_m)
            roofs = roofs.intersection(poly_m) if roofs is not None else None
            roads = poly_m.difference(par_local)
            parts = [g for g in (roofs, roads) if g is not None and not g.is_empty]
            area = unary_union(parts).area if parts else 0.0
            imperv = max(config.min_imperv, min(config.max_imperv, 100.0 * area / cell.area_m2))
            imperv_map[name] = imperv
            n_parcel += 1
        subs.append(SubcatchmentIn(
            name=name, outlet_node=nearest_node(seeds[cb_id]), area_ha=cell.area_m2 / 1e4,
            pct_imperv=imperv, width_m=math.sqrt(cell.area_m2),
            pct_slope=config.default_slope_pct, polygon=cell.exterior))
    diag = {"method": f"catchbasin+parcel/building ({shape_method}-shaped)", "n_catchbasins": len(seeds),
            "n_subcatchments": len(subs), "n_parcel_based_imperv": n_parcel,
            "n_parcels": int(len(par)), "n_buildings": int(len(bld)),
            "mean_imperv": round(sum(imperv_map.values()) / len(imperv_map), 1) if imperv_map else None}
    return subs, imperv_map, diag
