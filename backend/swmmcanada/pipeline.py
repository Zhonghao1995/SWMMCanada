"""End-to-end pipeline: an AOI → a complete SWMM .inp, wiring the real modules.

  geo.AOI → acquire.dem (clip MRDEM) → OSM streets + DEM elevations → network synthesis
          → acquire.climate (raingage) → build (.inp + round-trip)

Sources default to the live adapters but are injectable for testing / alternate sources.
This is the function the future tasks-api worker will call (run_pipeline).
"""
import json
import os
from dataclasses import replace
from datetime import date
from functools import partial
from pathlib import Path
from typing import Optional

from swmmcanada.acquire.climate import (
    fetch_climate,
    to_evaporation_series,
    to_rainfall_series,
    to_temperature_series,
)
from swmmcanada.acquire.dem import acquire_dem
from swmmcanada.acquire.landcover import acquire_landcover
from swmmcanada.acquire.soil import acquire_soil
from swmmcanada.build import BuildConfig, BuildResult
from swmmcanada.datastore import build_from_datastore, write_datastore
from swmmcanada import result_package
from swmmcanada.derive.core import derive_parameters
from swmmcanada.geo.crs import utm_crs_for
from swmmcanada.network import synthesise_network
from swmmcanada.network.delineate_dem import delineate_junction_subcatchments
from swmmcanada.network.sizing import size_conduits
from swmmcanada.network.service_area import MIN_CELL_HA, block_aware_service_area
from swmmcanada.network.water import subtract_water, water_union
from swmmcanada.preview import network_geojson
from swmmcanada.validate import (
    MethodDescriptor,
    SubcatchmentValidationError,
    validate_model,
)
from swmmcanada.validate import schema as vschema
from swmmcanada.sources.climate_geomet import GeoMetClient
from swmmcanada.sources.dem_nrcan import NRCanDemSource
from swmmcanada.sources.landcover_nrcan import NRCanLandcoverSource
from swmmcanada.sources.soil_constant import ConstantHsgSoilSource
from swmmcanada.sources.soil_hysogs import HysogsSoilSource
from swmmcanada.sources.soil_soilgrids import SoilGridsSource
from swmmcanada.sources.streets_osm import (
    fetch_building_footprints, fetch_street_graph, sample_elevations,
)
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.registry import CitySpec, city_for_point, city_spec


def _method_descriptor(sub_diag: Optional[dict]) -> MethodDescriptor:
    """Map a delineation's diagnostics to the honest controlled-vocabulary method label."""
    method = (sub_diag or {}).get("method", "")
    if "parcel-shaped" in method:
        return MethodDescriptor("catchbasin_parcel", "nearest inlet service area", "medium")
    if "voronoi-shaped" in method:
        return MethodDescriptor("catchbasin_voronoi", "nearest inlet service area", "low")
    if method == "junction_dem":
        return MethodDescriptor("junction_dem", "DEM D8 basins to manholes", "medium")
    return MethodDescriptor("junction_voronoi", "nearest node service area", "low")


def _infiltration_kwargs(infiltration) -> dict:
    """BuildConfig kwargs for an optional infiltration override (ADR 0013): accepts an
    InfiltrationModel or its string value; None keeps the config default (Horton)."""
    if infiltration is None:
        return {}
    from swmmcanada.build.config import InfiltrationModel

    return {"infiltration": InfiltrationModel(str(infiltration).upper())}


def _validate_or_raise(network, subcatchments, aoi, method: MethodDescriptor, ws: Path,
                       delineation: Optional[dict] = None, forcing: Optional[dict] = None,
                       water=None, served=None):
    """Validate the subcatchment model, always write validation.json into the package, and
    raise (stopping the build) if any error-severity check fails — so no untrusted .inp ships."""
    report = validate_model(network, subcatchments, aoi, method=method, delineation=delineation,
                            forcing=forcing, water=water, served=served)
    (Path(ws) / vschema.VALIDATION_JSON).write_text(json.dumps(report.to_dict(), indent=2))
    if not report.ok:
        detail = "; ".join(f"{c.id}: {c.message}" for c in report.errors)
        raise SubcatchmentValidationError(f"Subcatchment validation failed — {detail}")
    return report


def _dem_source_auto(dem_source):
    """DEM source selection (#51 decision): explicit override > SWMMCANADA_DEM_SOURCE=mrdem
    (forces the 30 m national fallback) > **auto default** — HRDEM LiDAR where a sampled
    read proves coverage, MRDEM-30 everywhere else. Safe to default because the delineation
    gate is resolution-aware (4.0 % at ≥10 m posting, 1.0 % under LiDAR) and a bad DEM
    result still falls back to Voronoi through the posterior validation gate."""
    if dem_source is not None:
        return dem_source
    if os.environ.get("SWMMCANADA_DEM_SOURCE") == "mrdem":
        return NRCanDemSource()
    from swmmcanada.sources.dem_hrdem import AutoDemSource

    return AutoDemSource()


def _design_intensity_fn(aoi):
    """``(intensity_fn, diagnostics)`` for rational-method pipe sizing (#56): the nearest
    ECCC IDF station's fitted curve at T=5 yr, degrading to a documented 30 mm/h constant
    when IDF is unreachable — sizing is additive and must never fail a build."""
    lat = (aoi.bbox[1] + aoi.bbox[3]) / 2
    lon = (aoi.bbox[0] + aoi.bbox[2]) / 2
    try:
        from swmmcanada.sources.idf_eccc import (
            design_intensity_mm_h,
            fetch_idf_table,
            nearest_idf_station,
        )

        station = nearest_idf_station(lat, lon)
        table = fetch_idf_table(station)
        diag = {"intensity_source": f"eccc-idf:{station.station_id}",
                "idf_station_name": station.name, "return_period_yr": 5}
        return (lambda tc_min: design_intensity_mm_h(table, tc_min, return_period=5)), diag
    except Exception:  # noqa: BLE001 — degrade to the documented constant, never raise
        return (lambda tc_min: 30.0), {
            "intensity_source": "fallback-constant", "intensity_mm_h": 30.0,
            "return_period_yr": 5, "reason": "idf_unavailable"}


def _design_storm_event(aoi, start: date, choice=None):
    """An alternating-block design storm from the nearest ECCC IDF station, serving two
    paths: ``choice=None`` is the tier-3 fallback (ADR 0015 — no usable gauge, T=5
    defaults, ``fallback_reason``); a ``DesignStormChoice`` is the user-selected mode
    (ADR 0018 — the chosen T × duration, ``requested``). Returns (RainfallSeries,
    forcing dict); raises RuntimeError when the IDF source is unreachable — rain is
    never invented from nothing."""
    from swmmcanada.acquire.design_storm import (
        DEFAULT_DURATION_H, DEFAULT_RETURN_PERIOD_YR, alternating_block_series,
    )

    return_period = choice.return_period_yr if choice else DEFAULT_RETURN_PERIOD_YR
    duration_h = choice.duration_h if choice else DEFAULT_DURATION_H
    lat = (aoi.bbox[1] + aoi.bbox[3]) / 2
    lon = (aoi.bbox[0] + aoi.bbox[2]) / 2
    try:
        from swmmcanada.sources.idf_eccc import fetch_idf_table, nearest_idf_station

        station = nearest_idf_station(lat, lon)
        table = fetch_idf_table(station)
        rain = alternating_block_series(table, start, return_period=return_period,
                                        duration_h=duration_h)
    except Exception as exc:
        what = (f"The requested T={return_period} yr design storm needs the ECCC IDF source, which"
                if choice else
                "No climate station with usable rainfall for this AOI/period, and the ECCC "
                "IDF design-storm fallback")
        raise RuntimeError(
            f"{what} is unreachable ({type(exc).__name__}). Try a different "
            "area or period.") from exc
    forcing = {
        "rainfall_resolution": "design_storm",
        "idf_station": station.station_id, "idf_station_name": station.name,
        "return_period_yr": return_period, "duration_h": duration_h,
        "timestep_min": 60, "method": "alternating-block from ECCC IDF table",
        "total_mm": round(sum(rain.precip_mm), 1),
    }
    if choice:
        forcing["requested"] = True
        forcing["note"] = ("user-selected design storm "
                           "(synthetic single-event storm — not for continuous hydrology)")
    else:
        forcing["fallback_reason"] = ("no climate station with usable rainfall within reach "
                                      "(synthetic single-event storm — not for continuous hydrology)")
    return rain, forcing


def _export_observed_safe(ws: Path, aoi, start: date, end: date) -> None:
    """CONTEXT deliverable: observed streamflow CSV (the calibration/validation target),
    written when the offline HYDAT database is present (SWMMCANADA_HYDAT_PATH) and a WSC
    station falls inside the AOI. Real data when available, a recorded absence otherwise
    (north star) — and never load-bearing: any failure leaves a note, not a dead build."""
    hydat = os.environ.get("SWMMCANADA_HYDAT_PATH")
    if not hydat or not Path(hydat).exists():
        return
    try:
        from swmmcanada.acquire.hydro import fetch_hydro

        res = fetch_hydro(aoi, start, end, hydat_path=hydat)
        if res.flows.empty:
            (ws / "observed_flow_NOTE.txt").write_text(
                "HYDAT present but no hydrometric station with data inside this AOI/period.\n")
            return
        res.flows.to_csv(ws / "observed_flow.csv", index=False)
    except Exception as exc:  # noqa: BLE001 — optional deliverable, degrade with a note
        (ws / "observed_flow_NOTE.txt").write_text(f"HYDAT observed-flow export failed: {exc!r}\n")


def _export_mikeplus_safe(ws: Path) -> None:
    """Emit the MIKE+ CS import package into ``ws/mikeplus`` alongside the .inp (ADR 0008).

    Additive and produced on every build, but never load-bearing: a failure is caught and
    noted into the folder so the primary SWMM .inp / datastore are never blocked by a
    secondary exporter's bug (ADR 0008 §5, graceful degradation)."""
    try:
        from swmmcanada.export import export_mikeplus

        export_mikeplus(ws / result_package.DATASTORE_DIR, ws / result_package.MIKEPLUS_DIR)
    except Exception as exc:  # noqa: BLE001 — MIKE+ export must never break the build
        target = ws / result_package.MIKEPLUS_DIR
        target.mkdir(parents=True, exist_ok=True)
        (target / "EXPORT_FAILED.txt").write_text(f"MIKE+ export failed: {exc!r}\n")


def _export_icm_safe(ws: Path) -> None:
    """Emit the InfoWorks ICM ODIC import package into ``ws/icm`` (ADR 0012). Same contract
    as the MIKE+ exporter: produced on every build, never load-bearing — a failure is noted
    into the folder, the primary SWMM .inp / datastore are never blocked."""
    try:
        from swmmcanada.export import export_icm

        export_icm(ws / result_package.DATASTORE_DIR, ws / result_package.ICM_DIR)
    except Exception as exc:  # noqa: BLE001 — ICM export must never break the build
        target = ws / result_package.ICM_DIR
        target.mkdir(parents=True, exist_ok=True)
        (target / "EXPORT_FAILED.txt").write_text(f"ICM export failed: {exc!r}\n")


def _finish_build(
    ws: Path, aoi, network, subcatchments, *, start: date, end: date, method,
    config: BuildConfig, extra_provenance: dict, climate_client, climate_buffer_deg: float,
    report=None, sub_diag: Optional[dict] = None, dem=None, water=None, served=None,
    design_storm=None,
) -> BuildResult:
    """The build spine (CONTEXT "Build spine") — the single shared tail of every build path.

    Network producers differ upstream (OSM synthesis vs a real-city adapter + catch-basin
    delineation); from here on all paths run ONE sequence: climate forcing → validation gate
    → datastore write (the primary build path, ADR 0007) → `.inp` via build_from_datastore
    → exports (ADR 0008) → map preview. A new stage is added here exactly once."""
    def _r(stage: str, pct: int):
        if report:
            report(stage, pct)

    _r("CLIMATE", 80)
    if design_storm is not None:
        # User-selected design-storm mode (ADR 0018): skip the gauge hunt entirely — the
        # chosen T × duration event from the nearest IDF station, same honesty labels as
        # the fallback tier; temperature/evaporation are honestly absent (single synthetic
        # event, not continuous hydrology).
        rain, forcing = _design_storm_event(aoi, start, choice=design_storm)
        evaporation = None
        temperature = None
    else:
        climate = fetch_climate(aoi, start, end, client=climate_client, near_buffer_deg=climate_buffer_deg)
        series = next((s for s in climate.series if not s.frame.empty), None)
        forcing = dict(climate.forcing)
        if series is not None:
            # Rainfall tiers 1-2 (ADR 0014): hourly series when a usable one was found, else the
            # daily station; temperature/evaporation stay on the daily station either way.
            rain = to_rainfall_series(climate.hourly_rain or series)
            evaporation = to_evaporation_series(series)
            temperature = to_temperature_series(series)
        else:
            # Tier 3 (ADR 0015): no usable gauge at all -> IDF design storm, honestly labelled;
            # temperature/evaporation are honestly absent (no station to derive them from).
            rain, forcing = _design_storm_event(aoi, start)
            evaporation = None
            temperature = None

    # Coastal outfall boundary (#130 gap 3): predicted tides from the nearest CHS station
    # (<=15 km) become a TIMESERIES stage on the outfalls the water can physically reach
    # (invert <= max predicted level + 0.5 m). Inland AOIs no-op; any CHS failure degrades
    # to today's FREE outfalls with an honest note — the boundary is additive, never
    # load-bearing.
    tide = None
    try:
        from dataclasses import replace as _dc_replace

        from swmmcanada.build.models import NetworkIn as _NetworkIn
        from swmmcanada.sources.tides_chs import (
            fetch_tide_predictions, nearest_tide_station, tidal_outfall_names)

        _st = nearest_tide_station((aoi.bbox[1] + aoi.bbox[3]) / 2,
                                   (aoi.bbox[0] + aoi.bbox[2]) / 2)
        if _st is not None:
            _t = fetch_tide_predictions(_st, start, end)
            _names = set(tidal_outfall_names(network.outfalls, max(_t.level_m)))
            if _names:
                network = _NetworkIn(
                    junctions=network.junctions,
                    outfalls=[_dc_replace(o, kind="TIMESERIES") if o.name in _names else o
                              for o in network.outfalls],
                    conduits=network.conduits)
                tide = _t
                forcing = {**(forcing or {}), "tide_boundary": {
                    "station": _st.name, "n_tidal_outfalls": len(_names),
                    "level_range_m": [round(min(_t.level_m), 2), round(max(_t.level_m), 2)],
                    "source": "CHS IWLS predicted water levels (wlp)"}}
    except Exception as _exc:  # noqa: BLE001 — degrade to FREE, never block the build
        forcing = {**(forcing or {}),
                   "tide_boundary_note": f"CHS tide boundary unavailable ({type(_exc).__name__}); "
                                         "outfalls stay FREE"}

    _r("VALIDATING", 85)
    _validate_or_raise(network, subcatchments, aoi, method, ws, delineation=sub_diag,
                       forcing=forcing or None, water=water, served=served)

    _r("BUILDING", 90)
    # Datastore is the PRIMARY build path (ADR 0007): write it, then build the .inp from it.
    write_datastore(
        ws / result_package.DATASTORE_DIR, network=network, subcatchments=subcatchments, rain=rain,
        config=config, evaporation=evaporation, temperature=temperature, tide=tide,
        provenance={
            "aoi_bbox": list(aoi.bbox), "crs": "EPSG:4326",
            "start": start.isoformat(), "end": end.isoformat(),
            "subcatchment_method": method.method,
            "physical_basis": method.physical_basis,
            "confidence": method.confidence,
            **extra_provenance,
        },
    )
    result = build_from_datastore(ws / result_package.DATASTORE_DIR, ws)
    if dem is not None:  # 2D-overland raw materials are promised deliverables — stamp the
        result_package.record_terrain(  # terrain source/resolution into the manifest
            ws, source=dem.source, resolution_m=dem.resolution_m, coverage=dem.coverage)
    if forcing:  # rainfall tier record (ADR 0014/0015) rides beside the terrain block
        result_package.record_forcing(ws, forcing)
    _export_mikeplus_safe(ws)  # ADR 0008: MIKE+ CS package — every build, graceful
    _export_icm_safe(ws)  # ADR 0012: ICM ODIC package — every build, graceful
    _export_observed_safe(ws, aoi, start, end)  # observed flow (HYDAT) — real data when present

    # Map preview: GeoJSON of the model geometry for the frontend's layers.
    preview_path = ws / result_package.PREVIEW_GEOJSON
    preview_path.parent.mkdir(exist_ok=True)
    preview_path.write_text(json.dumps(network_geojson(network, subcatchments)))

    _r("DONE", 100)
    return result


def build_from_aoi(
    aoi,
    start: date,
    end: date,
    workspace,
    *,
    dem_source=None,
    climate_client=None,
    climate_buffer_deg: float = 0.3,
    derive: bool = True,
    landcover_source=None,
    soil_source=None,
    infiltration=None,
    design_storm=None,
    report=None,
) -> BuildResult:
    def _r(stage: str, pct: int):
        if report:
            report(stage, pct)

    dem_source = _dem_source_auto(dem_source)
    climate_client = climate_client or GeoMetClient()
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    _r("ACQUIRING_DEM", 10)
    dem = acquire_dem(tuple(aoi.bbox), ws, source=dem_source)

    _r("STREETS", 30)
    streets = fetch_street_graph(tuple(aoi.bbox))
    sample_elevations(streets, dem.path)

    # Open-water layer (ADR 0016) from the landcover clip — needed BEFORE synthesis so the
    # network can discharge at the water instead of one global low point. derive=False has
    # no landcover, so the whole water story honestly absent (v1 behaviour).
    landcover = None
    water = None
    if derive:
        _r("LANDCOVER", 45)
        landcover = acquire_landcover(tuple(aoi.bbox), ws, source=landcover_source or NRCanLandcoverSource())
        water = water_union(landcover.raster_path, aoi)

    _r("NETWORK", 55)
    synth = synthesise_network(streets, aoi=aoi, water=water)
    # Municipal worldview (ADR 0017): the street service corridor bounds what the network
    # serves; inside it, ADR 0010's gate still picks DEM basins vs Voronoi as the referee.
    buildings = fetch_building_footprints(tuple(aoi.bbox))   # evidence for block interiors
    corridor = block_aware_service_area(streets, aoi, buildings=buildings)
    junction_xy = {j.name: (j.x, j.y) for j in synth.network.junctions}
    subcatchments, sub_diag = delineate_junction_subcatchments(
        junction_xy, aoi, dem_path=dem.path, streets=streets,
        service_mask=corridor, min_cell_ha=MIN_CELL_HA)
    subcatchments, water_diag = subtract_water(subcatchments, water, junction_xy, aoi)
    sub_diag = {**(sub_diag or {}), "water": water_diag}
    if corridor is not None:  # the service promise, quantified (ADR 0017)
        from shapely.ops import transform as _shpt
        from swmmcanada.geo.crs import lonlat_projector as _llp, utm_crs_for as _utm
        _to_m = _llp(_utm(aoi))
        corr_ha = _shpt(_to_m, corridor).area / 10_000.0
        sub_diag.setdefault("service", {}).update(
            corridor_ha=round(corr_ha, 1),
            unserved_frac=round(max(0.0, 1.0 - corr_ha / (aoi.area_km2 * 100.0)), 3),
            lot_depth_m=50.0)

    if derive:
        _r("SOIL", 62)
        soil = _acquire_soil_auto(tuple(aoi.bbox), ws, soil_source)
        _r("DERIVE", 70)
        subcatchments = derive_parameters(subcatchments, dem.path, landcover, soil)

    # Pipe sizing (#56): rational method over the derived subcatchments, design intensity
    # from the nearest ECCC IDF station (falls back to a documented constant). Runs after
    # derive so the runoff coefficients see real imperviousness.
    _r("SIZING", 74)
    intensity_fn, idf_diag = _design_intensity_fn(aoi)
    network, sizing_diag = size_conduits(synth.network, subcatchments, intensity_fn)
    sizing_diag.update(idf_diag)

    # Head done (network producer = OSM synthesis); the shared build spine does the rest.
    method = _method_descriptor(sub_diag)
    config = BuildConfig(out_dir=ws, start=start, end=end, coordinate_crs=utm_crs_for(aoi),
                         **_infiltration_kwargs(infiltration))
    return _finish_build(
        ws, aoi, network, subcatchments,
        start=start, end=end, method=method, config=config,
        extra_provenance={
            "sources": {
                "dem": type(dem_source).__name__,
                "climate": type(climate_client).__name__,
                "streets": "OSM",
            },
            "subcatchment_diagnostics": sub_diag,
            "pipe_sizing": sizing_diag,
        },
        climate_client=climate_client, climate_buffer_deg=climate_buffer_deg, report=report,
        sub_diag=sub_diag, dem=dem, water=water, served=corridor, design_storm=design_storm,
    )


def build_city(
    city, aoi, start: date, end: date, workspace, *,
    client=None,
    dem_source=None, climate_client=None, climate_buffer_deg: float = 0.3, derive: bool = True,
    landcover_source=None, soil_source=None, subcatchment_method: str = "parcel",
    infiltration=None, design_storm=None, report=None,
) -> BuildResult:
    """Build a SWMM model from a real municipal network (ADR 0004/0005/0006). ``city`` is a
    registry key ("victoria") or a ``CitySpec``; the spec supplies the city's fetch/build
    composition, metric CRS and provenance. Everything else — subcatchments (catch-basin +
    parcel/building, Voronoi-of-nodes fallback), derive, climate, build, datastore — is
    city-agnostic. ``client`` is passed to the spec's fetchers (tests inject fixtures here)."""
    spec: CitySpec = city_spec(city) if isinstance(city, str) else city
    bbox = tuple(aoi.bbox)

    def _r(stage: str, pct: int):
        if report:
            report(stage, pct)

    dem_source = _dem_source_auto(dem_source)
    climate_client = climate_client or GeoMetClient()
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    _r("FETCH_NETWORK", 15)
    netres = spec.storm(bbox, client)
    network = netres.network

    # Subcatchments: catch-basin + parcel/building (ADR 0005), else Voronoi-of-nodes fallback.
    _r("SUBCATCHMENTS", 35)
    imperv_map: dict = {}
    sub_diag: dict = {}
    if subcatchment_method == "parcel":
        land = spec.land(bbox, client)
        subcatchments, imperv_map, sub_diag = base.delineate_catchbasin_subcatchments(
            network, land["catchbasins"], land["parcels"], land["buildings"], aoi, crs=spec.sub_crs
        )
    else:
        subcatchments = []
    dem = None
    water = None
    landcover = None
    if not subcatchments:  # no catch-basin data -> junction delineation (DEM-gated, ADR 0010)
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        if derive:  # the DEM is needed for derive anyway; without derive there is none → "no_dem"
            _r("ACQUIRING_DEM", 40)
            dem = acquire_dem(tuple(aoi.bbox), ws, source=dem_source)
        subcatchments, sub_diag = delineate_junction_subcatchments(
            junction_xy, aoi, dem_path=(dem.path if dem else None))
        imperv_map = {}
        if derive:  # water masking for the junction fallback (ADR 0016; parcel cells skip it)
            landcover = acquire_landcover(tuple(aoi.bbox), ws, source=landcover_source or NRCanLandcoverSource())
            water = water_union(landcover.raster_path, aoi)
            subcatchments, water_diag = subtract_water(subcatchments, water, junction_xy, aoi)
            sub_diag = {**(sub_diag or {}), "water": water_diag}

    if derive:
        if dem is None:
            _r("ACQUIRING_DEM", 45)
            dem = acquire_dem(tuple(aoi.bbox), ws, source=dem_source)
        _r("LANDCOVER_SOIL", 60)
        if landcover is None:
            landcover = acquire_landcover(tuple(aoi.bbox), ws, source=landcover_source or NRCanLandcoverSource())
        soil = _acquire_soil_auto(tuple(aoi.bbox), ws, soil_source)
        _r("DERIVE", 70)
        subcatchments = derive_parameters(subcatchments, dem.path, landcover, soil)
        if imperv_map:  # restore parcel/building imperviousness (derive overwrote it)
            subcatchments = [
                replace(s, pct_imperv=imperv_map[s.name]) if s.name in imperv_map else s
                for s in subcatchments
            ]

    # Sanitary tracer (ADR 0011): where the city publishes a sanitary layer, graft it in
    # as a tagged, disconnected subgraph — AFTER subcatchments (they are storm-seeded) and
    # with graceful degradation (a sanitary fetch failure never blocks the storm build).
    san_diag = {"included": False, "reason": "not_published"}
    if spec.sanitary is not None:
        _r("SANITARY", 78)
        try:
            sanres = spec.sanitary(bbox, client)
            network = base.merge_secondary_system(
                network, sanres.network, prefix="SAN_", system="sanitary")
            san_diag = {"included": True,
                        "n_junctions": len(sanres.network.junctions),
                        "n_conduits": len(sanres.network.conduits)}
        except Exception as exc:  # noqa: BLE001 — additive system, degrade with a note
            san_diag = {"included": False, "reason": f"{type(exc).__name__}: {exc}"}

    # Head done (network producer = the city adapter); the shared build spine does the rest.
    method = _method_descriptor(sub_diag)
    config = BuildConfig(out_dir=ws, start=start, end=end,
                         title=f"SWMMCanada ({spec.key} real network)",
                         coordinate_crs=spec.sub_crs, **_infiltration_kwargs(infiltration))
    return _finish_build(
        ws, aoi, network, subcatchments,
        start=start, end=end, method=method, config=config,
        extra_provenance={
            "city": spec.key, "network_source": spec.network_source,
            "network_diagnostics": netres.diagnostics,
            "subcatchment_diagnostics": sub_diag,
            "sanitary": san_diag,
        },
        climate_client=climate_client, climate_buffer_deg=climate_buffer_deg, report=report,
        sub_diag=sub_diag, dem=dem, water=water, design_storm=design_storm,
    )


def pipeline_for_aoi(aoi):
    """Pick the build pathway for an AOI: a real-municipal-network city adapter when the AOI
    centre falls inside a supported city's coverage (the city registry decides), else
    synthesize a network from open data. Returns ``(build_fn, mode_label)``."""
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    spec = city_for_point((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)
    if spec is not None:
        return partial(build_city, spec), f"Real municipal network: {spec.label}"
    return build_from_aoi, ("Synthetic network from open data: streets-based routing, "
                            "not municipal pipe records")


def _acquire_soil_auto(bbox, ws, soil_source):
    """Soil source selection: explicit override > cached HYSOGs250m (real HSG, EPSG:4326)
    when SWMMCANADA_HYSOGS_PATH points at the one-time download > documented HSG-B stand-in."""
    if soil_source is not None:
        return acquire_soil(bbox, ws, source=soil_source)
    hysogs = os.environ.get("SWMMCANADA_HYSOGS_PATH")
    if hysogs and Path(hysogs).exists():
        return acquire_soil(bbox, ws, source=HysogsSoilSource(hysogs), out_crs="EPSG:4326")
    try:
        # Auth-free default: ISRIC SoilGrids (live texture → HSG), no login, no download.
        return acquire_soil(bbox, ws, source=SoilGridsSource(), out_crs="EPSG:4326")
    except Exception:
        return acquire_soil(bbox, ws, source=ConstantHsgSoilSource())
