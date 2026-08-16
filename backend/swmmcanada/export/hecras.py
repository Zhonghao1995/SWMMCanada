"""HEC-RAS exporter — a RAS Mapper import package for a **2D rain-on-grid + pipe-network**
model of the same AOI (ADR 0033).

HEC-RAS is not "the same model in another format": it has no subcatchment object. Its
hydrology is a land-cover layer (Manning n + % impervious per class), an infiltration layer
(SCS CN / Green-Ampt / Deficit-Constant — **no Horton**) and precipitation on the 2D mesh.
So this package hands RAS Mapper the **2D raw materials** the parent package already ships
(``../dem_dtm.tif``, ``../landcover.tif``, ``../hsg.tif``) plus *our own* lookup tables
(TR-55 CN by cover category × HSG, Green-Ampt by HSG tier, NALCMS n / %imperv) — written
from the very dicts ``derive`` uses, so CI can check the transcription verbatim.

The pipe network travels as a **system-filtered ``model.inp``**: RAS Mapper's *Import SWMM
Geometry* reads junctions/outfalls/conduits (invert, shape, rise/span, n, offsets) directly
and can optionally merge the subcatchment polygons into a 2D area with breaklines — more
than its shapefile importer takes, so no nodes/links shapefile is written here (ADR 0033 Q3).
The only hard requirement is that the ``.inp`` coordinates and the HEC-RAS project share a
coordinate system: ``projection.prj`` carries the datastore's projected CRS for that.

Everything a RAS user then still has to type is precomputed into CSVs (per-node terrain
override / drop-inlet defaults / base area, per-outfall boundary-condition suggestions,
the precipitation ``Interval`` token) and every field this model class cannot represent is
in the lossy report. Verification is the ADR 0008/0012 split: CI checks structure and
transcription; the first RAS Mapper import (HEC-RAS 7.0.x, Windows) is the HITL step —
free software, so the author can actually run it.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon, box, shape

from swmmcanada.acquire.landcover import DEFAULT_NALCMS_IMPERVIOUS, DEFAULT_NALCMS_LEGEND
from swmmcanada.build.models import filter_system_report
from swmmcanada.derive.core import NALCMS_CATEGORY, TR55_CN_TABLE
from swmmcanada.derive.infiltration import (
    GA_BY_TEXTURE,
    HSG_HORTON,
    HSG_REPRESENTATIVE_TEXTURE,
    TEXTURE_CODE,
    green_ampt_for_hsg,
)
from swmmcanada.export._shared import to_crs, write_rain_csv
from swmmcanada.export.base import ExportResult, LossyMapping
from swmmcanada.export.swmm import SwmmExporter

#: ADR 0033 Q4 — the systems whose nodes physically take surface water. Sanitary manholes
#: do not (a RAS user would read them as drop inlets); ``storm_major`` never enters because
#: in HEC-RAS the 2D terrain *is* the major system.
DEFAULT_SYSTEMS: Tuple[str, ...] = ("storm_minor", "combined")
NEVER_SYSTEMS: Tuple[str, ...] = ("storm_major",)

#: The 2D raw materials, by their package-root file names (result_package constants are
#: mirrored here rather than imported: this module must stay importable without the
#: package contract, and the names are part of the ADR 0009 / ADR 0033 contract anyway).
DEM_DTM = "dem_dtm.tif"
LANDCOVER = "landcover.tif"
SOIL_HSG = "hsg.tif"
SOIL_TEXTURE = "soil_texture.tif"

#: HSG raster codes (HYSOGs250m; dual codes 11-14 reduce to A-D, as in derive.core).
HSG_CODES: Dict[int, str] = {1: "A", 2: "B", 3: "C", 4: "D", 11: "A", 12: "B", 13: "C", 14: "D"}

#: Manning's n per NALCMS 2020 class for the RAS Mapper land-cover layer — a NEW documented
#: default (SWMM's n_imperv/n_perv are per surface, not per cover class). Mid-range of the
#: HEC-RAS 2D User's Manual land-cover n table (NLCD classes; Chow 1959 / Kalyanapu et al.
#: 2009 / Bunya et al. 2010 lineage) mapped onto the NALCMS legend. Calibration starting
#: point, not a measurement — ASSUMPTIONS.md, same tier as the Horton defaults.
DEFAULT_NALCMS_MANNING_N: Dict[int, float] = {
    1: 0.12, 2: 0.12, 3: 0.12, 4: 0.12, 5: 0.12, 6: 0.12,   # forests
    7: 0.08, 8: 0.08, 11: 0.08,                              # shrubland
    9: 0.035, 10: 0.035, 12: 0.035,                          # grassland
    13: 0.03, 16: 0.03, 19: 0.03,                            # barren / snow-ice
    14: 0.08,                                                # wetland
    15: 0.045,                                               # cropland
    17: 0.12,                                                # urban and built-up (developed, medium intensity)
    18: 0.035,                                               # water
}

#: Pipe-network supplements (SI), documented defaults — ASSUMPTIONS.md.
MANHOLE_BASE_AREA_M2 = 1.13          # 1,200 mm precast manhole, π·0.6²
DROP_INLET_WEIR_LENGTH_M = 0.9       # HEC pipe-network tutorial curb inlet, 3 ft
DROP_INLET_ORIFICE_AREA_M2 = 0.42    # HEC pipe-network tutorial, 4.5 ft²
NORMAL_DEPTH_SLOPE_FLOOR = 0.001     # m/m, when the last pipe reads flat or adverse


class HecRasExporter:
    """Write a HEC-RAS (RAS Mapper) import package from the datastore (ADR 0033)."""

    target = "hecras"

    def export(self, ds, out_dir, *, systems=None, package_root=None) -> ExportResult:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        root = Path(package_root) if package_root is not None else out.parent

        crs = ds.config.get("coordinate_crs")
        systems = list(DEFAULT_SYSTEMS) if systems is None else list(systems)
        systems = [s for s in systems if s not in NEVER_SYSTEMS]
        network, view_report = filter_system_report(ds.network, systems)
        view_report["default_systems"] = list(DEFAULT_SYSTEMS)
        view_report["never_systems"] = list(NEVER_SYSTEMS)

        lossy: List[LossyMapping] = []
        warnings: List[str] = []
        files: List[Path] = []

        # 1. The pipe network: a system-filtered .inp through the ONE .inp writer.
        swmm = SwmmExporter().export(ds, out, systems=systems)
        warnings.extend(f"model.inp: {w}" for w in swmm.warnings)
        inp_path, manifest_path = swmm.files[0], swmm.files[1]
        build_manifest = out / "model_build_manifest.json"   # not the package manifest
        manifest_path.replace(build_manifest)
        files.extend([inp_path, build_manifest])

        # 2. Projection — the one hard requirement of RAS Mapper's SWMM importer.
        prj = _write_projection(out / "projection.prj", crs, warnings)
        if prj is not None:
            files.append(prj)

        # 3. The 2D flow area perimeter (the AOI), and the raw materials the package root
        #    already ships (referenced, never copied — one copy per package).
        aoi_shp = _write_aoi_2d_area(out / "aoi_2d_area.shp", ds, crs, warnings)
        if aoi_shp is not None:
            files.append(aoi_shp)
        materials = _materials_present(root, warnings)

        # 4. Our own lookup tables, transcribed from the derive dicts.
        files.append(_write_landcover_table(out / "landcover_table.csv"))
        files.append(_write_scs_cn_table(out / "infiltration_scs_cn.csv"))
        files.append(_write_green_ampt_table(out / "infiltration_green_ampt.csv",
                                             with_texture=materials[SOIL_TEXTURE]))

        # 5. Forcing: precipitation (+ its HEC-RAS Interval token) and the tide stage.
        files.append(write_rain_csv(out / "rain.csv", ds.rain))
        interval = precipitation_interval(ds.rain.timestamps)
        _warn_if_daily(ds, interval, warnings)
        if ds.tide is not None:
            files.append(_write_tide_csv(out / "tide.csv", ds.tide))

        # 6. Node supplement (terrain override, drop inlets, base area) + outfall BCs.
        dem_at = _sample_dem(root / DEM_DTM, network) if materials[DEM_DTM] else {}
        files.append(_write_nodes_supplement(out / "nodes_supplement.csv", ds, network,
                                             dem_at, warnings))
        files.append(_write_boundary_conditions(out / "boundary_conditions.csv", ds, network,
                                                warnings))

        # 7. Receipts.
        lossy.extend(_hydrology_lossy(ds, network, materials))
        files.append(_write_field_mapping(out / "field_mapping.md", lossy, view_report,
                                          interval, materials))
        files.append(_write_readme(out / "README.md", interval, materials))

        return ExportResult(
            target=self.target, out_dir=out, files=files, lossy=lossy, warnings=warnings,
            view=view_report,
        )


def export_hecras(datastore_dir, out_dir, *, systems=None, package_root=None) -> ExportResult:
    """Read a datastore directory and write its HEC-RAS import package into ``out_dir``.

    ``systems`` selects the tagged systems the ``.inp`` carries (default
    :data:`DEFAULT_SYSTEMS`); ``package_root`` is where the 2D raw materials live
    (default: the parent of ``out_dir``, i.e. the result package root)."""
    from swmmcanada.datastore import read_datastore

    return HecRasExporter().export(read_datastore(datastore_dir), out_dir,
                                   systems=systems, package_root=package_root)


# --------------------------------------------------------------------------- #
# projection / 2D area / materials
# --------------------------------------------------------------------------- #
def _write_projection(path: Path, crs: Optional[str], warnings: List[str]) -> Optional[Path]:
    """ESRI WKT of the datastore's projected CRS — set as the RAS Mapper projection so the
    ``.inp`` coordinates land where they belong. RAS Mapper reprojects the terrain and
    land-cover rasters (EPSG:3979) into it at layer creation; the vectors are not touched."""
    if not crs:
        warnings.append("projection.prj not written: the datastore carries no projected "
                        "coordinate_crs — the .inp is in EPSG:4326 lon/lat, which HEC-RAS "
                        "cannot use as a project projection; set one before importing")
        return None
    from pyproj import CRS
    from pyproj.enums import WktVersion

    path.write_text(CRS.from_user_input(crs).to_wkt(WktVersion.WKT1_ESRI))
    return path


def _aoi_geometry(ds):
    """The AOI polygon from provenance (``aoi_geojson``, written by the build spine), else
    the bbox rectangle, else the envelope of the delineated cells, else None."""
    prov = ds.provenance or {}
    if prov.get("aoi_geojson"):
        try:
            return shape(prov["aoi_geojson"])
        except Exception:  # noqa: BLE001 — provenance is advisory, fall through
            pass
    bbox = prov.get("aoi_bbox")
    if bbox and len(bbox) == 4:
        return box(*[float(v) for v in bbox])
    polys = [Polygon([(float(x), float(y)) for x, y in s.polygon])
             for s in ds.subcatchments if s.polygon]
    if polys:
        return MultiPolygon(polys).envelope
    return None


def _write_aoi_2d_area(path: Path, ds, crs: Optional[str], warnings: List[str]) -> Optional[Path]:
    geom = _aoi_geometry(ds)
    if geom is None or geom.is_empty:
        warnings.append("aoi_2d_area.shp not written: no AOI polygon, bbox or delineated "
                        "cells in the datastore — draw the 2D flow area in RAS Mapper")
        return None
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    gdf = gpd.GeoDataFrame(
        {"name": [f"AOI_2D_{i + 1}" if len(parts) > 1 else "AOI_2D" for i in range(len(parts))]},
        geometry=parts, crs="EPSG:4326")
    to_crs(gdf, crs).to_file(path)
    return path


def _materials_present(root: Path, warnings: List[str]) -> Dict[str, bool]:
    present = {name: (root / name).is_file() for name in (DEM_DTM, LANDCOVER, SOIL_HSG, SOIL_TEXTURE)}
    for name in (DEM_DTM, LANDCOVER, SOIL_HSG):
        if not present[name]:
            warnings.append(f"../{name} not found beside this package: the RAS Mapper "
                            f"{'terrain' if name == DEM_DTM else 'land-cover' if name == LANDCOVER else 'soils'} "
                            f"layer has to come from elsewhere")
    return present


# --------------------------------------------------------------------------- #
# lookup tables — transcribed from the derive dicts (CI compares them verbatim)
# --------------------------------------------------------------------------- #
def _write_landcover_table(path: Path) -> Path:
    """NALCMS class → RAS Mapper land-cover layer row: Manning n + % impervious. The
    %imperv column is the same SWMMCanada default `derive` applies (calibration starting
    point); n is the new documented default above."""
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["landcover_code", "landcover_name", "tr55_category", "manning_n",
                    "impervious_pct"])
        for code in sorted(DEFAULT_NALCMS_LEGEND):
            w.writerow([code, DEFAULT_NALCMS_LEGEND[code], NALCMS_CATEGORY.get(code, "grass"),
                        DEFAULT_NALCMS_MANNING_N[code],
                        round(100.0 * DEFAULT_NALCMS_IMPERVIOUS.get(code, 0.0), 1)])
    return path


def _write_scs_cn_table(path: Path) -> Path:
    """Every (NALCMS class × HSG) combination → the TR-55 **pervious-remainder** CN that
    `derive` area-weights per subcatchment. HEC-RAS builds its SCS-CN infiltration layer
    from soils × land cover exactly this way; the % impervious lives on the land-cover
    layer, so this CN must stay the pervious one (round-2 F-021 — no double counting)."""
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["landcover_code", "landcover_name", "tr55_category", "hsg_code", "hsg",
                    "curve_number"])
        for code in sorted(DEFAULT_NALCMS_LEGEND):
            cat = NALCMS_CATEGORY.get(code, "grass")
            for hsg_code in (1, 2, 3, 4):
                hsg = HSG_CODES[hsg_code]
                w.writerow([code, DEFAULT_NALCMS_LEGEND[code], cat, hsg_code, hsg,
                            TR55_CN_TABLE[cat][hsg]])
    return path


def _write_green_ampt_table(path: Path, *, with_texture: bool) -> Path:
    """Green-Ampt by HSG tier (the parameters `derive` assigns when the soil source publishes
    only HSG); the USDA-texture rows are appended when the package carries
    ``soil_texture.tif`` (SoilGrids tier, ADR 0013). Horton has no HEC-RAS 2D counterpart —
    the Horton rows are listed for reference only, flagged ``not_representable``."""
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["layer", "code", "class", "psi_mm", "ksat_mm_h", "imd",
                    "horton_f0_mm_h", "horton_fc_mm_h", "horton_decay_1_h", "note"])
        for hsg_code in (1, 2, 3, 4):
            hsg = HSG_CODES[hsg_code]
            psi, ksat, imd = green_ampt_for_hsg(hsg)
            f0, fc, k = HSG_HORTON[hsg]
            w.writerow(["hsg", hsg_code, hsg, psi, ksat, imd, f0, fc, k,
                        f"GA via representative texture '{HSG_REPRESENTATIVE_TEXTURE[hsg]}'; "
                        f"Horton columns not_representable in HEC-RAS 2D (reference only)"])
        if with_texture:
            for texture, (psi, ksat, imd) in GA_BY_TEXTURE.items():
                w.writerow(["texture", TEXTURE_CODE[texture], texture, psi, ksat, imd,
                            "", "", "", "USDA texture class from ../soil_texture.tif"])
    return path


# --------------------------------------------------------------------------- #
# forcing
# --------------------------------------------------------------------------- #
def precipitation_interval(timestamps: Sequence) -> str:
    """The HEC-RAS unsteady-flow ``Interval=`` token for this series (``1HOUR``, ``1DAY``,
    ``15MIN`` …), from the median step — what ``ras_flow_set_hydrograph`` takes."""
    if len(timestamps) < 2:
        return "1HOUR"
    steps = [int((b - a).total_seconds()) for a, b in zip(timestamps, timestamps[1:])]
    step = int(statistics.median(steps)) or 3600
    if step % 86400 == 0:
        n = step // 86400
        return "1DAY" if n == 1 else f"{n}DAY"
    if step % 3600 == 0:
        n = step // 3600
        return "1HOUR" if n == 1 else f"{n}HOUR"
    return f"{max(1, step // 60)}MIN"


def _warn_if_daily(ds, interval: str, warnings: List[str]) -> None:
    """ADR 0033 Q6 (R1): a daily series never activates a 2D pluvial model — say so out
    loud, from provenance when it says so and from the series itself regardless."""
    resolution = ((ds.provenance or {}).get("forcing") or {}).get("rainfall_resolution")
    if resolution == "daily" or interval.endswith("DAY"):
        warnings.append(
            "rain.csv is a DAILY series (rainfall_resolution=%s, Interval=%s): a 2D rain-on-grid "
            "run will not produce meaningful overland flow from daily depths — rebuild in "
            "design-storm mode (ADR 0018) or with hourly forcing before running HEC-RAS"
            % (resolution or "unknown", interval))


def _write_tide_csv(path: Path, tide) -> Path:
    """The CHS stage series for External-node ``Stage Hydrograph`` boundaries — levels in
    the model's geodetic frame, timestamps local standard time (ADR 0024 §1)."""
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["datetime", "stage_m"])
        for ts, lvl in zip(tide.timestamps, tide.level_m):
            w.writerow([ts.isoformat(), float(lvl)])
    return path


# --------------------------------------------------------------------------- #
# node supplement + outfall boundary conditions
# --------------------------------------------------------------------------- #
def _sample_dem(dem_path: Path, network) -> Dict[str, Optional[float]]:
    """Terrain elevation at every node of the view (DEM CRS ← lon/lat), None where the
    DEM has no data. Never load-bearing: a broken raster leaves the column empty."""
    try:
        import rasterio
        from pyproj import Transformer

        nodes = list(network.junctions) + list(network.outfalls)
        with rasterio.open(dem_path) as src:
            tf = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            pts = [tf.transform(float(n.x), float(n.y)) for n in nodes]
            nodata = src.nodata
            out: Dict[str, Optional[float]] = {}
            for n, val in zip(nodes, src.sample(pts)):
                v = float(val[0])
                out[n.name] = None if (nodata is not None and v == nodata) or v != v else v
            return out
    except Exception:  # noqa: BLE001 — diagnostic column only
        return {}


def _write_nodes_supplement(path: Path, ds, network, dem_at: Dict[str, Optional[float]],
                            warnings: List[str]) -> Path:
    """What RAS Mapper's SWMM importer does not fill in, precomputed per node: the terrain
    elevation override (our rim), whether the node takes surface water (a drop inlet), the
    drop-inlet and base-area defaults, and the rim-vs-DEM discrepancy — a node whose invert
    sits at or above the terrain becomes an Error node in HEC-RAS, so it is listed here."""
    drains: Dict[str, List] = {}
    for s in ds.subcatchments:
        drains.setdefault(s.outlet_node, []).append(s)
    bad: List[str] = []
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["node", "node_kind", "system", "invert_m", "rim_m", "dem_m",
                    "rim_minus_dem_m", "invert_ge_dem", "n_subcatchments", "drain_area_ha",
                    "drop_inlet", "drop_inlet_elev_m", "drop_inlet_weir_length_m",
                    "drop_inlet_orifice_area_m2", "base_area_m2", "synthesised"])
        rows = ([("junction", j, float(j.invert_m) + float(j.max_depth_m)) for j in network.junctions]
                + [("outfall", o, float(o.invert_m)) for o in network.outfalls])
        for kind, n, rim in rows:
            dem = dem_at.get(n.name)
            subs = drains.get(n.name, [])
            has_inlet = kind == "junction" and bool(subs)
            invert = float(n.invert_m)
            ge = (dem is not None and invert >= dem)
            if ge:
                bad.append(n.name)
            w.writerow([
                n.name, kind, getattr(n, "system", "storm_minor"), invert, rim,
                "" if dem is None else round(dem, 3),
                "" if dem is None else round(rim - dem, 3),
                int(ge) if dem is not None else "",
                len(subs), round(sum(float(s.area_ha) for s in subs), 4),
                int(has_inlet),
                rim if has_inlet else "",
                DROP_INLET_WEIR_LENGTH_M if has_inlet else "",
                DROP_INLET_ORIFICE_AREA_M2 if has_inlet else "",
                MANHOLE_BASE_AREA_M2 if kind == "junction" else 0.0,
                int(bool(getattr(n, "synthesised", False))),
            ])
    if bad:
        warnings.append(f"{len(bad)} node(s) have invert >= DEM terrain (would be Error nodes "
                        f"in HEC-RAS): {', '.join(bad[:10])}{' …' if len(bad) > 10 else ''} — "
                        f"use the rim as Terrain Elevation Override or fix the terrain")
    return path


def _write_boundary_conditions(path: Path, ds, network, warnings: List[str]) -> Path:
    """Every outfall of the view = a HEC-RAS External node, which accepts only a Stage
    Hydrograph or Normal Depth: SWMM ``kind`` → the suggestion, tide series → the stage,
    the last pipe's slope → the normal-depth friction slope."""
    inverts = {n.name: float(n.invert_m)
               for n in list(network.junctions) + list(network.outfalls)}
    incoming: Dict[str, List] = {}
    for c in network.conduits:
        incoming.setdefault(c.to_node, []).append(c)
    has_tide = ds.tide is not None
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["outfall", "system", "swmm_kind", "hecras_bc", "stage_source",
                    "normal_depth_slope", "invert_m", "synthesised", "note"])
        for o in network.outfalls:
            kind = str(getattr(o, "kind", "FREE")).upper()
            slope = _last_pipe_slope(o.name, incoming.get(o.name, []), inverts)
            if kind in ("TIDAL", "TIMESERIES") and has_tide:
                bc, stage, note = "Stage Hydrograph", "tide.csv", "CHS predicted water level (ADR 0024)"
            elif kind == "FIXED":
                bc, stage, note = "Stage Hydrograph", "constant (SWMM FIXED stage; see model.inp)", ""
            else:
                bc, stage, note = "Normal Depth", "", ""
                if kind in ("TIDAL", "TIMESERIES"):
                    note = "SWMM outfall is tidal but no tide series in the datastore"
            if getattr(o, "synthesised", False):
                note = (note + "; " if note else "") + \
                    "synthesised boundary outfall (no published outlet for this component)"
            w.writerow([o.name, getattr(o, "system", "storm_minor"), kind, bc, stage,
                        slope, float(o.invert_m), int(bool(getattr(o, "synthesised", False))),
                        note])
    return path


def _last_pipe_slope(outfall: str, pipes: List, inverts: Dict[str, float]) -> float:
    """Friction slope for Normal Depth: the steepest incoming pipe (end elevations = node
    inverts + offsets, #130), floored so a flat/adverse reading never yields a zero slope."""
    best = 0.0
    for c in pipes:
        us = inverts.get(c.from_node)
        dsl = inverts.get(c.to_node)
        if us is None or dsl is None or float(c.length_m) <= 0:
            continue
        s = ((us + float(getattr(c, "inlet_offset_m", 0.0)))
             - (dsl + float(getattr(c, "outlet_offset_m", 0.0)))) / float(c.length_m)
        best = max(best, s)
    return round(max(best, NORMAL_DEPTH_SLOPE_FLOOR), 5)


# --------------------------------------------------------------------------- #
# lossy report + docs
# --------------------------------------------------------------------------- #
def _hydrology_lossy(ds, network, materials: Dict[str, bool]) -> List[LossyMapping]:
    """The honest list (ADR 0033 Q5): this is a different model class, and every datastore
    field that does not survive the change says so here."""
    lossy = [
        LossyMapping(
            source="width_m / pct_slope / n_imperv / n_perv / s_imperv_mm / s_perv_mm / pct_zero",
            target="—", kind="dropped",
            detail="SWMM subcatchment runoff routing has no HEC-RAS counterpart: the 2D mesh "
                   "routes rain on the terrain itself. Not a loss of information about the "
                   "site — a different model class.",
        ),
        LossyMapping(
            source="cn (per-subcatchment, area-weighted TR-55)",
            target="infiltration_scs_cn.csv (NALCMS class × HSG)", kind="restructured",
            detail="HEC-RAS builds SCS-CN infiltration from soils × land cover; the package "
                   "ships the same TR-55 pervious-remainder table `derive` weights, so the "
                   "grid recovers the per-cell values the subcatchment composites came from.",
        ),
        LossyMapping(
            source="pct_imperv (parcel/building physical imperviousness, ADR 0023)",
            target="landcover_table.csv impervious_pct (per NALCMS class)", kind="approximated",
            detail="RAS Mapper's land-cover layer carries % impervious per class; the "
                   "parcel-level physical share is not carried — the class default is. "
                   "Rasterising buildings/roads into an impervious class is a follow-up (H3).",
        ),
        LossyMapping(
            source="horton_f0_mm_h / horton_fc_mm_h / horton_decay_1_h",
            target="SCS CN or Green-Ampt layer", kind="approximated",
            detail="HEC-RAS 2D has no Horton infiltration. If the SWMM build ran Horton, use "
                   "Green-Ampt from infiltration_green_ampt.csv (same soil tier, also a "
                   "continuous rate) or SCS CN for a design-storm event; the Horton columns "
                   "are listed for reference only.",
        ),
        LossyMapping(
            source="ga_psi_mm / ga_ksat_mm_h / ga_imd (per subcatchment)",
            target="infiltration_green_ampt.csv (HSG tier%s)" % (
                " + USDA texture rows" if materials.get(SOIL_TEXTURE) else ""),
            kind="restructured",
            detail="Green-Ampt parameters travel as the lookup `derive` applied, keyed by "
                   "the soils layer, not per polygon.",
        ),
        LossyMapping(
            source="polygon (surface catchment boundary)",
            target="2D flow area perimeter + breaklines (optional, RAS Mapper SWMM importer)",
            kind="restructured",
            detail="RAS Mapper can merge the SWMM subcatchments into a 2D area and drop "
                   "breaklines on their boundaries; with hundreds to thousands of small "
                   "storm units that over-constrains the mesh, so aoi_2d_area.shp is the "
                   "recommended perimeter and the option is left to the user.",
        ),
        LossyMapping(
            source="Manning n per land-cover class", target="landcover_table.csv manning_n",
            kind="approximated",
            detail="a NEW documented default (SWMM's n_imperv/n_perv are per surface, not per "
                   "cover class): mid-range HEC-RAS 2D manual values mapped onto NALCMS — "
                   "calibration starting point, ASSUMPTIONS.md.",
        ),
    ]
    if any(getattr(c, "shape", "CIRCULAR") != "CIRCULAR" for c in network.conduits):
        lossy.append(LossyMapping(
            source="conduit shape (non-circular, #130)", target="pipe-network conduit shape",
            kind="approximated",
            detail="HEC-RAS 6.6 pipe networks accept circular conduits only; 7.0's SWMM "
                   "importer reads other shapes — verify the imported rise/span for these.",
        ))
    if ds.evaporation is not None:
        lossy.append(LossyMapping(
            source="evaporation series", target="—", kind="dropped",
            detail="no evaporation forcing on a HEC-RAS 2D area; irrelevant at event scale.",
        ))
    if ds.temperature is not None:
        lossy.append(LossyMapping(
            source="temperature series", target="—", kind="dropped",
            detail="no snowmelt/temperature forcing in HEC-RAS 2D.",
        ))
    if ds.tide is not None:
        lossy.append(LossyMapping(
            source="tide series (CHS predicted water level)",
            target="tide.csv → External-node Stage Hydrograph", kind="restructured",
            detail="assigned per outfall by boundary_conditions.csv; the SWMM TIMESERIES "
                   "boundary becomes a HEC-RAS stage hydrograph.",
        ))
    if not materials.get(SOIL_HSG):
        lossy.append(LossyMapping(
            source="soil HSG raster", target="RAS Mapper soils layer", kind="dropped",
            detail="../hsg.tif is not beside this package (constant-HSG fallback or a build "
                   "without derive): use one uniform HSG row of the tables.",
        ))
    return lossy


def _write_field_mapping(path: Path, lossy: List[LossyMapping], view: dict, interval: str,
                         materials: Dict[str, bool]) -> Path:
    def _m(name: str) -> str:
        return "present" if materials.get(name) else "**missing**"

    rows = [
        ("`model.inp` junctions / outfalls / conduits", "Pipe Network nodes / conduits",
         "**RAS Mapper → Import SWMM Geometry** (name, invert, shape, rise/span, n, offsets)"),
        ("`model.inp` [COORDINATES] CRS", "project projection", "`projection.prj` (same CRS — the importer's one hard requirement)"),
        ("`model.inp` outfalls", "External nodes", "boundary per `boundary_conditions.csv`"),
        ("`invert_m + max_depth_m` (rim)", "Terrain Elevation Override", "`nodes_supplement.csv rim_m` (m)"),
        ("nodes with surface catchments", "Drop Inlet (elev / weir length / orifice area)",
         "`nodes_supplement.csv` — elev = rim, defaults 0.9 m / 0.42 m²"),
        ("junctions", "Base Area", "1.13 m² (1,200 mm manhole)"),
        ("`aoi_2d_area.shp`", "2D Flow Area perimeter", "one polygon = the AOI"),
        ("`../dem_dtm.tif`", "Terrain", f"RAS Mapper *Create New Terrain* (reprojects to the project CRS) — {_m(DEM_DTM)}"),
        ("`../landcover.tif` + `landcover_table.csv`", "Land Cover layer (n, % impervious)", f"class → n / %imperv — {_m(LANDCOVER)}"),
        ("`../hsg.tif` + `infiltration_scs_cn.csv`", "Soils layer + SCS CN infiltration layer", f"class × HSG → CN — {_m(SOIL_HSG)}"),
        ("`infiltration_green_ampt.csv`", "Green-Ampt infiltration layer", "HSG tier (+ texture rows when `../soil_texture.tif` ships)"),
        ("`rain.csv`", "2D area Precipitation boundary", f"depth per step (mm), `Interval={interval}`"),
        ("`tide.csv`", "External-node Stage Hydrograph", "m, geodetic frame (ADR 0024)"),
    ]
    lines: List[str] = []
    lines.append("# HEC-RAS import package — field mapping (ADR 0033)\n")
    lines.append(
        "> **Systems in `model.inp`:** {systems} (default {default}; `{never}` never — in HEC-RAS the 2D "
        "terrain is the major system). Models carry every tagged system in one hydraulic model; "
        "this package is a filtered view of it (ADR 0029). {orphan}\n".format(
            systems=", ".join(view.get("systems", [])) or "unknown",
            default=", ".join(view.get("default_systems", [])),
            never=", ".join(view.get("never_systems", [])),
            orphan=(view.get("note") or "No element lost its route to an outfall in this view.")))
    lines.append("> This is **not** the SWMM model in another format: HEC-RAS has no subcatchment. "
                 "The pipe network travels as the filtered `.inp`; the hydrology is rebuilt "
                 "from the 2D raw materials + the lookup tables `derive` itself used.\n")
    lines.append("## Source → HEC-RAS (RAS Mapper)\n")
    lines.append("| source | HEC-RAS | how |")
    lines.append("|---|---|---|")
    for src, tgt, how in rows:
        lines.append(f"| {src} | {tgt} | {how} |")
    lines.append("")
    lines.append("## Lossy / approximated / restructured\n")
    lines.append("| source | target | kind | detail |")
    lines.append("|---|---|---|---|")
    for m in lossy:
        detail = m.detail.replace("|", "\\|")
        lines.append(f"| `{m.source}` | {m.target} | {m.kind} | {detail} |")
    lines.append("")
    path.write_text("\n".join(lines))
    return path


def _write_readme(path: Path, interval: str, materials: Dict[str, bool]) -> Path:
    text = f"""# HEC-RAS import package (ADR 0033)

A **RAS Mapper import package** for a HEC-RAS **2D rain-on-grid + pipe-network** model of this
AOI, produced from the SWMMCanada model-ready datastore. HEC-RAS 7.0.x (pipe networks are
beta in 6.6). SI units throughout.

This is not the SWMM model in another format — HEC-RAS has no subcatchment object. The
storm/combined pipe network comes in through RAS Mapper's own **Import SWMM Geometry**
(reads `model.inp`); the hydrology is rebuilt from the terrain, land cover and soils the
parent package ships, with the lookup tables SWMMCanada itself used. `field_mapping.md`
lists everything that does not translate.

## Contents

- `model.inp` — the SWMM model **filtered to the systems that take surface water**
  (default storm + combined; sanitary excluded, see `field_mapping.md`), rebuilt by the same
  writer as `../model.inp`; `model_build_manifest.json` is its build receipt
- `projection.prj` — the projected CRS the `.inp` coordinates are in; **set it as the RAS
  Mapper projection before importing**
- `aoi_2d_area.shp` — the AOI polygon, for the 2D Flow Area perimeter
- `landcover_table.csv` — NALCMS class → Manning n, % impervious (Land Cover layer table)
- `infiltration_scs_cn.csv` — NALCMS class × HSG → TR-55 curve number (SCS CN layer table)
- `infiltration_green_ampt.csv` — Green-Ampt ψ / Ksat / IMD by HSG (and USDA texture when
  `../soil_texture.tif` ships); Horton listed for reference only (no HEC-RAS counterpart)
- `rain.csv` — precipitation, depth per step (mm); HEC-RAS `Interval={interval}`
- `tide.csv` — CHS stage series for tidal outfalls (when present)
- `nodes_supplement.csv` — per node: rim as Terrain Elevation Override, rim vs DEM, drop-inlet
  and base-area defaults, which nodes take surface water
- `boundary_conditions.csv` — per outfall (External node): Stage Hydrograph vs Normal Depth,
  stage source, friction slope
- `field_mapping.md` — mapping receipt **and** lossy report

Referenced from the package root (one copy per package): `../dem_dtm.tif` ({'present' if materials.get(DEM_DTM) else 'MISSING'}),
`../landcover.tif` ({'present' if materials.get(LANDCOVER) else 'MISSING'}), `../hsg.tif` ({'present' if materials.get(SOIL_HSG) else 'MISSING'}).
Their source / resolution / coverage are stamped in `../manifest.json` (`terrain`).

## How to build the model in RAS Mapper

1. New HEC-RAS project (SI units). RAS Mapper → **Project Settings → Projection** →
   `projection.prj`.
2. **Terrain**: RAS Mapper → *Create a New RAS Terrain* from `../dem_dtm.tif` (RAS Mapper
   reprojects it into the project CRS). Headless alternative on the HEC-RAS machine:
   `RasProcess.exe CreateTerrain units=Meters prj="projection.prj" out="Terrain/terrain.hdf" "../dem_dtm.tif"`.
3. **Land cover**: *Create a New Land Cover Layer* from `../landcover.tif`; fill Manning n and
   % impervious from `landcover_table.csv` (class code = raster value).
4. **Soils + infiltration**: *Create a New Soils Layer* from `../hsg.tif` (1=A … 4=D); then
   *Create a New Infiltration Layer* — SCS CN from `infiltration_scs_cn.csv` (soils × land
   cover), or Green-Ampt from `infiltration_green_ampt.csv`. Pick the method the SWMM build
   used; if it used Horton, take Green-Ampt.
5. **2D Flow Area**: import `aoi_2d_area.shp` as the perimeter (or draw it); generate the
   mesh; make sure Manning n comes from the land-cover layer.
6. **Pipe network**: Geometries → *Import SWMM Geometry* → `model.inp`. Leave *bring in the
   SWMM Sub Catchments as HEC-RAS 2D Areas* **unchecked** (you already have the 2D area;
   the storm units are too small to be useful breaklines).
7. **Nodes**: paste `nodes_supplement.csv` — Terrain Elevation Override = `rim_m`, Drop
   Inlet on every node with `drop_inlet=1` (elevation / weir length / orifice area), Base Area
   on junctions. Nodes flagged `invert_ge_dem=1` need attention (invert at or above the
   terrain → Error node).
8. **Boundaries**: External nodes per `boundary_conditions.csv` (Stage Hydrograph from
   `tide.csv`, or Normal Depth with `normal_depth_slope`); a Normal Depth BC line on the 2D
   perimeter where water leaves the AOI.
9. **Precipitation**: 2D area precipitation boundary from `rain.csv` (`Interval={interval}`).
   With Agentic-HEC-RAS: `ras_flow_set_hydrograph(values=<rain.csv column>, interval="{interval}")`.
10. Plan → run. From here on Agentic-HEC-RAS (run / read / compare / plot) takes over.

## Verification status

CI validates the package structure, the projection, the system filter on `model.inp`, and
that the lookup tables equal the code's own dicts. The first import into RAS Mapper on a
HEC-RAS 7.0.x machine is the manual verification step — see the repo's tracking issue.
"""
    path.write_text(text)
    return path
