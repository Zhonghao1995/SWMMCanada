"""Delineation v2 (ADR 0010, issue #3): DEM D8 basins to manholes behind a terrain
honesty gate.

`delineate_junction_subcatchments` is the junction-seeded delineation seam shared by
synthesis mode and the real-network no-catch-basin fallback. It runs:

  conditioned DEM (fill depressions) → street burning (OSM streets carved in, so urban
  flow follows roads) → D8 basins with junctions as pour points (`pyflwdir`) → uncovered
  AOI cells absorbed to the nearest junction (D8 basins only cover ground draining
  *through* a pour point; the remainder must still belong to someone) → posterior check
  through the validation layer.

Two-layer terrain honesty gate: (1) prior — median AOI slope of the conditioned DEM below
``slope_gate_pct`` ⇒ the DEM cannot be distinguished from noise here, use junction-Voronoi;
(2) posterior — a DEM result that fails validation (coverage holes / overlaps) falls back
to junction-Voronoi instead of failing the build. Every decision and reading lands in the
returned diagnostics (→ `validation.json` and datastore provenance).
"""
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from swmmcanada.build.models import JunctionIn, NetworkIn, SubcatchmentIn
from swmmcanada.network.subcatchments import _AREA_CRS, SubcatchmentCell, _largest_polygon
from swmmcanada.network.synth import NetworkConfig, _build_subcatchments

METHOD_DEM = "junction_dem"
METHOD_VORONOI = "junction_voronoi"


@dataclass(frozen=True)
class DemDelineationConfig:
    """Knobs of the v2 delineator; every reading is recorded in diagnostics."""

    slope_gate_pct: float = 4.0     # prior gate at COARSE posting (≥ fine_res_m): median AOI
    #                                 slope (%) below this → Voronoi. Calibrated 2026-07-02
    #                                 (ADR 0010): 7 downtown fixture AOIs read 1.1–3.3 % on
    #                                 MRDEM-30 (inside its ±1–2 m noise band), hilly AOIs 9–13 %.
    slope_gate_pct_fine: float = 1.0  # gate at FINE posting (< fine_res_m): LiDAR vertical
    #                                 accuracy ~0.1–0.2 m makes urban micro-slope real signal
    #                                 (#51 decision — 1 m downtown medians measured 2.1–4.0 %).
    fine_res_m: float = 10.0        # posting finer than this ⇒ the fine threshold applies
    burn_depth_m: float = 2.0       # street burning: metres carved out of the DEM along roads
    aoi_buffer_cells: int = 3       # DEM window padding around the AOI
    nodata: float = -9999.0

    def gate_for(self, cell_size_m: float) -> float:
        return self.slope_gate_pct_fine if cell_size_m < self.fine_res_m else self.slope_gate_pct


def delineate_junction_subcatchments(
    junction_xy: Dict[str, Tuple[float, float]],
    aoi,
    *,
    dem_path=None,
    streets=None,
    service_mask=None,     # ADR 0017: street service corridor (4326); None = whole AOI (v2)
    min_cell_ha=None,      # ADR 0017: sliver-merge threshold; None = no merging (v2)
    config: DemDelineationConfig = DemDelineationConfig(),
    network_config: NetworkConfig = NetworkConfig(),
) -> Tuple[List[SubcatchmentIn], dict]:
    """One subcatchment per junction, DEM-delineated when the terrain earns it.

    Returns ``(subcatchments, diagnostics)``; diagnostics carries the method used and the
    gate readings/fallback reason, so "why this method" is always answerable.
    """
    gate: dict = {"threshold_pct": config.slope_gate_pct}

    if service_mask is not None:
        # Corridor mode (ADR 0017): the municipal split IS the street-midpoint Voronoi —
        # inside a ~50 m served band, D8 basins add nothing over gutter-midpoint semantics
        # and their corridor-clipped candidates kept failing the posterior geometry gate.
        # DEM basins remain the method for no-corridor contexts (city fallback).
        gate["decision"] = "corridor_voronoi"
        return _voronoi(junction_xy, aoi, network_config, gate,
                        service_mask=service_mask, min_cell_ha=min_cell_ha)

    if dem_path is None:
        gate["decision"] = "no_dem"
        return _voronoi(junction_xy, aoi, network_config, gate,
                        service_mask=service_mask, min_cell_ha=min_cell_ha)

    window = _read_dem_window(dem_path, aoi, config)
    if window is None:
        gate["decision"] = "no_dem_overlap"
        return _voronoi(junction_xy, aoi, network_config, gate,
                        service_mask=service_mask, min_cell_ha=min_cell_ha)
    dem, transform, dem_crs, aoi_mask = window

    import pyflwdir

    # Resolution-aware threshold (#51): what counts as "noise" depends on the posting.
    cell_size = abs(transform.a)
    threshold = config.gate_for(cell_size)
    gate["threshold_pct"] = threshold
    gate["cell_size_m"] = round(cell_size, 2)

    filled, _ = pyflwdir.dem.fill_depressions(dem, nodata=config.nodata)
    median_slope = _median_slope_pct(filled, aoi_mask, transform, config.nodata)
    gate["median_slope_pct"] = round(median_slope, 3)
    if median_slope < threshold:
        gate["decision"] = "below_slope_gate"
        return _voronoi(junction_xy, aoi, network_config, gate,
                        service_mask=service_mask, min_cell_ha=min_cell_ha)

    burned, n_burned = _burn_streets(dem, transform, dem_crs, streets, config)
    gate["streets_burned_cells"] = n_burned

    cells, widths, dem_diag = _dem_basins(
        burned, transform, dem_crs, aoi_mask, junction_xy, aoi, config
    )
    if cells is None:  # degenerate (e.g. every junction outside the DEM window)
        gate["decision"] = "dem_degenerate"
        gate.update(dem_diag)
        return _voronoi(junction_xy, aoi, network_config, gate,
                        service_mask=service_mask, min_cell_ha=min_cell_ha)

    subs = _build_subcatchments(junction_xy, aoi, network_config, cells=cells, widths=widths)

    # Posterior gate: the DEM result must satisfy the validation layer's *errors*; a
    # result with blank holes or gross overlap is worse than an honest Voronoi.
    errors = _posterior_errors(junction_xy, subs, aoi)
    if errors:
        gate["decision"] = "posterior_fallback"
        gate["posterior_errors"] = errors
        return _voronoi(junction_xy, aoi, network_config, gate,
                        service_mask=service_mask, min_cell_ha=min_cell_ha)

    gate["decision"] = "dem"
    diag = {
        "method": METHOD_DEM,
        "n_subcatchments": len(subs),
        "gate": gate,
        "width_method": "area_over_flow_length",
        **dem_diag,
    }
    return subs, diag


def _apply_service(subs, junction_xy, aoi, service_mask, min_cell_ha):
    """Size discipline for corridor mode (ADR 0017 §3). The corridor itself is applied at
    the Voronoi source (clip polygon); this only merges slivers."""
    from swmmcanada.network.service_area import merge_slivers

    diag: dict = {"applied": service_mask is not None or min_cell_ha is not None}
    if min_cell_ha is not None:
        subs, merge_diag = merge_slivers(subs, aoi, min_cell_ha=min_cell_ha)
        diag.update(merge_diag)
    return subs, diag


# --------------------------------------------------------------------------- #
# fallback + gate helpers
# --------------------------------------------------------------------------- #
def _voronoi(junction_xy, aoi, network_config, gate,
             service_mask=None, min_cell_ha=None) -> Tuple[List[SubcatchmentIn], dict]:
    # The Voronoi tiling clips to the corridor at the source (ADR 0017): pass it as the
    # clip polygon, then apply size discipline. None/None = the v2 whole-AOI behaviour.
    clip = None
    if service_mask is not None:
        clip = service_mask
    subs = _build_subcatchments(junction_xy, aoi, network_config, clip_poly=clip)
    subs, service_diag = _apply_service(subs, junction_xy, aoi, None, min_cell_ha)
    service_diag["applied"] = service_mask is not None or min_cell_ha is not None
    return subs, {"method": METHOD_VORONOI, "n_subcatchments": len(subs), "gate": gate,
                  "service": service_diag}


def _posterior_errors(junction_xy, subs, aoi) -> List[str]:
    """Failing *error* check ids of the candidate DEM delineation (geometric screening;
    outlets are the seed junctions by construction, so a minimal network suffices)."""
    from swmmcanada.validate import MethodDescriptor, validate_model

    net = NetworkIn(
        junctions=[JunctionIn(n, 0.0, x, y) for n, (x, y) in junction_xy.items()],
        outfalls=[], conduits=[],
    )
    method = MethodDescriptor(METHOD_DEM, "DEM D8 basins to manholes", "medium")
    report = validate_model(net, subs, aoi, method=method)
    return [c.id for c in report.errors]


def _median_slope_pct(filled, aoi_mask, transform, nodata) -> float:
    import pyflwdir

    slope = pyflwdir.dem.slope(filled, nodata=nodata, latlon=False, transform=transform)
    sel = aoi_mask & (slope != nodata) & np.isfinite(slope)
    if not sel.any():
        return 0.0
    return float(np.median(slope[sel])) * 100.0


# --------------------------------------------------------------------------- #
# DEM I/O + burning + basins
# --------------------------------------------------------------------------- #
def _read_dem_window(dem_path, aoi, config: DemDelineationConfig):
    """The DEM clipped to the AOI (+buffer): (float32 array, transform, crs, aoi_mask)."""
    import rasterio
    from rasterio import features, windows
    from shapely.ops import transform as shp_transform

    from swmmcanada.geo.crs import lonlat_projector

    with rasterio.open(dem_path) as src:
        aoi_dem = shp_transform(lonlat_projector(str(src.crs)), aoi.geometry)
        pad = config.aoi_buffer_cells * abs(src.transform.a)
        minx, miny, maxx, maxy = aoi_dem.bounds
        win = windows.from_bounds(
            minx - pad, miny - pad, maxx + pad, maxy + pad, src.transform
        ).intersection(windows.Window(0, 0, src.width, src.height))
        if win.width < 2 or win.height < 2:
            return None
        dem = src.read(1, window=win).astype("float32")
        transform = src.window_transform(win)
        nodata = src.nodata
        if nodata is not None:
            dem[dem == np.float32(nodata)] = config.nodata
        aoi_mask = features.geometry_mask(
            [aoi_dem], out_shape=dem.shape, transform=transform, invert=True
        )
        if not aoi_mask.any():
            return None
        return dem, transform, str(src.crs), aoi_mask


def _burn_streets(dem, transform, dem_crs, streets, config: DemDelineationConfig):
    """Carve street lines into the DEM (burn depth) so urban flow follows roads. Accepts a
    networkx graph with node x/y in EPSG:4326 (optionally edge ``geometry``); None → no-op."""
    if streets is None or getattr(streets, "number_of_edges", lambda: 0)() == 0:
        return dem, 0

    from rasterio import features
    from shapely.geometry import LineString
    from shapely.ops import transform as shp_transform

    from swmmcanada.geo.crs import lonlat_projector

    to_dem = lonlat_projector(dem_crs)
    lines = []
    for u, v, d in streets.edges(data=True):
        geom = d.get("geometry")
        if geom is None:
            try:
                geom = LineString([
                    (streets.nodes[u]["x"], streets.nodes[u]["y"]),
                    (streets.nodes[v]["x"], streets.nodes[v]["y"]),
                ])
            except KeyError:
                continue
        lines.append(shp_transform(to_dem, geom))
    if not lines:
        return dem, 0

    mask = features.geometry_mask(
        lines, out_shape=dem.shape, transform=transform, invert=True, all_touched=True
    )
    burned = dem.copy()
    valid = burned != config.nodata
    sel = mask & valid
    burned[sel] -= config.burn_depth_m
    return burned, int(sel.sum())


def _grow_labels(labels, uncovered):
    """Grow labelled regions into ``uncovered`` cells, one 8-neighbour ring per pass
    (deterministic shift order), until nothing grows. Keeps every label contiguous."""
    labels = labels.copy()
    todo = uncovered.copy()
    shifts = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
    while todo.any():
        grew = False
        for dr, dc in shifts:
            src = np.zeros_like(labels)
            src[max(dr, 0) or None: labels.shape[0] + min(dr, 0) or None,
                max(dc, 0) or None: labels.shape[1] + min(dc, 0) or None] = \
                labels[max(-dr, 0) or None: labels.shape[0] + min(-dr, 0) or None,
                       max(-dc, 0) or None: labels.shape[1] + min(-dc, 0) or None]
            take = todo & (labels == 0) & (src > 0)
            if take.any():
                labels[take] = src[take]
                todo &= labels == 0
                grew = True
        if not grew:
            break
    return labels


def _dem_basins(dem, transform, dem_crs, aoi_mask, junction_xy, aoi, config):
    """D8 basins per junction + nearest-junction absorption of uncovered AOI cells →
    {name: SubcatchmentCell} in the Voronoi cell vocabulary (4326 polygon, equal-area m²)."""
    import pyflwdir
    from pyproj import Transformer
    from rasterio import features
    from shapely.geometry import shape as shp_shape
    from shapely.ops import transform as shp_transform, unary_union

    from swmmcanada.geo.crs import lonlat_projector

    to_dem = lonlat_projector(dem_crs)
    names = list(junction_xy)
    px, py = [], []
    inside = []
    h, w = dem.shape
    for n in names:
        x, y = to_dem(*junction_xy[n])
        col, row = ~transform * (x, y)
        if 0 <= int(row) < h and 0 <= int(col) < w:
            inside.append(n)
            px.append(x)
            py.append(y)
    diag = {"n_junctions_outside_dem": len(names) - len(inside)}
    if len(inside) < 2:
        return None, None, diag

    flw = pyflwdir.from_dem(dem, nodata=config.nodata, transform=transform, latlon=False)
    labels = flw.basins(xy=(np.asarray(px), np.asarray(py))).astype("int32")

    # Per-basin longest flow path to its pour point (SWMM width = area / flow length):
    # distnc is along-flow distance to the terminal outlet, so within a basin the length
    # to the pour point is max(distnc) − distnc(pour cell).
    distnc = flw.distnc
    cols_r = np.clip(((np.asarray(px) - transform.c) / transform.a).astype(int), 0, w - 1)
    rows_r = np.clip(((np.asarray(py) - transform.f) / transform.e).astype(int), 0, h - 1)
    flow_len: Dict[str, float] = {}
    cell_size = abs(transform.a)
    for i, name in enumerate(inside, start=1):
        sel = labels == i
        if not sel.any():
            continue
        L = float(np.nanmax(distnc[sel]) - distnc[rows_r[i - 1], cols_r[i - 1]])
        if np.isfinite(L) and L > cell_size:
            flow_len[name] = L

    # Absorb AOI cells no basin claimed (ground that drains PAST every pour point) by
    # region-growing the existing basins outward — growth from a basin keeps each label's
    # region contiguous, so the largest-polygon normalisation below cannot re-open holes
    # (assigning by nearest junction instead leaves disconnected fragments that get dropped).
    uncovered = aoi_mask & (labels == 0) & (dem != config.nodata)
    n_uncovered = int(uncovered.sum())
    labels = _grow_labels(labels, uncovered)
    still = aoi_mask & (labels == 0) & (dem != config.nodata)
    if still.any():  # pockets with no labelled neighbour at all → nearest junction
        rows, cols = np.nonzero(still)
        xs = transform.c + (cols + 0.5) * transform.a
        ys = transform.f + (rows + 0.5) * transform.e
        nearest = np.argmin(
            (xs[:, None] - np.asarray(px)[None, :]) ** 2
            + (ys[:, None] - np.asarray(py)[None, :]) ** 2, axis=1)
        labels[rows, cols] = nearest + 1
    diag["n_cells_absorbed"] = n_uncovered

    # Vectorize per label → clip to AOI → largest polygon → 4326 + equal-area m².
    aoi_dem = shp_transform(to_dem, aoi.geometry)
    to_4326 = Transformer.from_crs(dem_crs, "EPSG:4326", always_xy=True).transform
    to_area = Transformer.from_crs("EPSG:4326", _AREA_CRS, always_xy=True).transform

    polys_by_label: Dict[int, list] = {}
    for geom, val in features.shapes(labels, mask=labels > 0, transform=transform):
        polys_by_label.setdefault(int(val), []).append(shp_shape(geom))

    main: Dict[str, object] = {}
    n_multi = 0
    for i, name in enumerate(inside, start=1):
        parts = polys_by_label.get(i)
        if not parts:
            continue
        poly = unary_union(parts).intersection(aoi_dem)
        if poly.geom_type != "Polygon":
            n_multi += 1
        poly = _clean_polygon(poly)
        if poly is None or poly.is_empty or poly.area == 0:
            continue
        main[name] = poly
    diag["n_multipart_cells"] = n_multi
    if not main:
        return None, None, diag

    # Vector-side leftover absorption: keeping each label's largest polygon drops the
    # point-touching fragments raster→vector splits off (the "keep-largest" blank-hole
    # mechanism). Merge every leftover piece into the neighbour sharing the longest border.
    main, n_left = _absorb_leftovers(main, aoi_dem)
    diag["n_leftover_fragments_merged"] = n_left

    from shapely.geometry import Polygon as ShpPolygon

    cells: Dict[str, SubcatchmentCell] = {}
    for name, poly in main.items():
        poly_4326 = shp_transform(to_4326, poly)
        # The consumed shape is the EXTERIOR ring in EPSG:4326 (SWMM [POLYGONS] contract) —
        # so THAT ring is what must be valid: reprojection of stair-step vertices can
        # re-introduce micro self-intersections, hence the repair happens here, last.
        shell = _clean_polygon(ShpPolygon(list(poly_4326.exterior.coords)))
        if shell is None or shell.is_empty:
            continue
        area_m2 = shp_transform(to_area, shell).area
        if area_m2 <= 0:
            continue
        cells[name] = SubcatchmentCell(
            polygon_4326=shell,
            area_m2=area_m2,
            exterior=[(float(x), float(y)) for x, y in shell.exterior.coords],
        )
    if not cells:
        return None, None, diag
    widths = {n: cells[n].area_m2 / flow_len[n] for n in cells if n in flow_len}
    return cells, widths, diag


def _clean_polygon(poly):
    """A valid, single-part polygon (or None): raster stair-step corners can make unions
    self-touching/invalid — repair with buffer(0), then keep the largest part. The emitted
    polygon must be valid, or the validation layer rightly rejects the whole delineation."""
    if poly is None or poly.is_empty:
        return None
    if not poly.is_valid:
        poly = poly.buffer(0)
    return _largest_polygon(poly)


def _absorb_leftovers(main: Dict[str, object], aoi_dem):
    """AOI area covered by no cell → merged into the adjacent cell with the longest shared
    boundary (deterministic tie-break by name). One pass over the leftover pieces."""
    from shapely.ops import unary_union

    leftover = aoi_dem.difference(unary_union(list(main.values())))
    if leftover.is_empty:
        return main, 0
    pieces = list(leftover.geoms) if hasattr(leftover, "geoms") else [leftover]
    n_merged = 0
    for piece in pieces:
        if piece.is_empty or piece.geom_type != "Polygon" or piece.area == 0:
            continue
        best, best_len = None, -1.0
        for name in sorted(main):
            shared = main[name].boundary.intersection(piece.boundary).length
            if shared > best_len:
                best, best_len = name, shared
        if best is None or best_len <= 0:
            continue
        merged = _clean_polygon(unary_union([main[best], piece]))
        if merged is not None and not merged.is_empty:
            main[best] = merged
            n_merged += 1
    return main, n_merged
