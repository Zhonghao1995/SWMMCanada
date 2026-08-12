"""End-to-end pipeline: an AOI → a complete SWMM .inp, wiring the real modules.

  geo.AOI → acquire.dem (clip MRDEM) → OSM streets + DEM elevations → network synthesis
          → acquire.climate (raingage) → build (.inp + round-trip)

Sources default to the live adapters but are injectable for testing / alternate sources.
This is the function the future tasks-api worker will call (run_pipeline).
"""
import json
import math
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
from swmmcanada.build.models import filter_system
from swmmcanada.delineation import DelineationPlan, Evidence, resolve
from swmmcanada.delineation.service_area import derive_service_areas
from swmmcanada.loading import load_service_areas
from swmmcanada import result_package
from swmmcanada.derive.core import derive_parameters
from swmmcanada.geo.crs import utm_crs_for
from swmmcanada.network import synthesise_network
from swmmcanada.network.delineate_dem import delineate_junction_subcatchments
from swmmcanada.network.sizing import size_conduits
from swmmcanada.network.service_area import MIN_CELL_HA
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


def _make_surface_sampler(aoi_bbox, ws, dem_source):
    """DEM-backed surface sampler for the invert gap-fill (base.SURFACE_SAMPLER seam):
    lon/lat coords -> one elevation (or None) per coord. Lazy — the DEM is acquired and
    opened only if the assembler actually has last-resort nodes to fill, so cities with
    complete inverts never pay for it. Any failure (no coverage — e.g. outside Canada —
    network trouble, nodata cells) degrades to None per coord and the assembler falls
    through to the counted global minimum, exactly as before."""
    state: dict = {}

    def sample(coords):
        if "ds" not in state:
            try:
                import rasterio
                from pyproj import Transformer

                dem = acquire_dem(tuple(aoi_bbox), ws, source=dem_source)
                state["ds"] = rasterio.open(dem.path)
                state["tr"] = Transformer.from_crs("EPSG:4326", state["ds"].crs, always_xy=True)
            except Exception:  # noqa: BLE001 — sampler is best-effort by contract
                state["ds"] = None
        ds = state.get("ds")
        if ds is None:
            return [None] * len(coords)
        xs, ys = state["tr"].transform([c[0] for c in coords], [c[1] for c in coords])
        out = []
        for (val,) in ds.sample(zip(xs, ys)):
            e = float(val)
            bad = (ds.nodata is not None and e == ds.nodata) or not math.isfinite(e) \
                or not (-450.0 < e < 6000.0)
            out.append(None if bad else e)
        return out

    return sample


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
    design_storm=None, network_kind: str = "synthesis", service_areas=None,
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
        if series is None and climate.hourly_rain is not None:
            # Round-2 F-001: a usable HOURLY station must not be discarded because the
            # DAILY gate found no station — rainfall availability is not hostage to the
            # temperature/evaporation record.
            rain = to_rainfall_series(climate.hourly_rain)
            evaporation = None
            temperature = None
            forcing["daily_station_note"] = (
                "no daily station passed the completeness gate; hourly rainfall stands "
                "alone (temperature/evaporation absent)")
        elif series is not None:
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
            # Target datum follows the network's vertical frame (round-2): synthesis
            # inverts derive from MRDEM/HRDEM (CGVD2013 spec); municipal as-builts are
            # predominantly CGVD28. Still an ASSUMPTION about the network side — recorded
            # as such until producers declare their datum (queued deepening).
            _pref = (("CGVD28", "CGVD2013") if network_kind == "city"
                     else ("CGVD2013", "CGVD28"))
            _t = fetch_tide_predictions(_st, start, end, datum_preference=_pref)
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
                    "network_datum_assumption": (
                        "CGVD28 (municipal as-builts)" if network_kind == "city"
                        else "CGVD2013 (MRDEM/HRDEM spec)"),
                    "datum": _t.datum, "datum_offset_m": _t.datum_offset_m,
                    "clock_utc_offset_h": _t.clock_utc_offset_h,
                    "source": "CHS IWLS predicted water levels (wlp), datum-converted "
                              "and clock-aligned (ADR 0024)"}}
    except Exception as _exc:  # noqa: BLE001 — degrade to FREE, never block the build
        forcing = {**(forcing or {}),
                   "tide_boundary_note": f"CHS tide boundary unavailable ({type(_exc).__name__}); "
                                         "outfalls stay FREE"}

    _r("VALIDATING", 85)
    _validate_or_raise(network, subcatchments, aoi, method, ws, delineation=sub_diag,
                       forcing=forcing or None, water=water, served=served)

    _r("BUILDING", 90)
    # Datastore is the PRIMARY build path (ADR 0007): write it, then build the .inp from it.
    if forcing:   # round-2 F-018: the forcing evidence is part of the citable artifact,
        #             not just the outer manifest
        extra_provenance = {**extra_provenance, "forcing": forcing}
    write_datastore(
        ws / result_package.DATASTORE_DIR, network=network, subcatchments=subcatchments, rain=rain,
        config=config, evaporation=evaporation, temperature=temperature, tide=tide,
        service_areas=service_areas,
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

    # Integrity block LAST (F-019): sha-256 + size for every member, so a shipped
    # package can be verified file by file.
    result_package.record_checksums(ws)

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
    # Full-coverage semantics (ADR 0022, #118): every piece of AOI land is a subcatchment —
    # forests and yards participate with landcover-driven parameters (low imperviousness,
    # high infiltration) instead of being deleted by the ADR 0017 corridor, whose exclusion
    # biased runoff low in suburbs. The municipal LOOK stays: passing the whole AOI as the
    # mask keeps the nearest-street-segment frontage split shaping the cells; open water is
    # still carved out afterwards (ADR 0016 — lakes are receiving waters, not land).
    junction_xy = {j.name: (j.x, j.y) for j in synth.network.junctions}
    # Synthesis has one family of options, but it goes through the resolver anyway
    # (ADR 0029 Q11): a second place that picks a method is a second place the recorded
    # reason can drift from the real one, and provenance must have the same shape on both
    # build paths so a reader never has to know which one produced the model.
    plan = resolve(Evidence(n_junctions=len(junction_xy), dem_available=dem is not None,
                            system="storm"))
    subcatchments, sub_diag = delineate_junction_subcatchments(
        junction_xy, aoi, dem_path=dem.path, streets=streets,
        service_mask=aoi.geometry, min_cell_ha=MIN_CELL_HA)
    # Cadastral cell boundaries (ADR 0023 cut 2, #138): where an open parcel fabric
    # exists (ParcelMap BC), cells reshape onto real lot lines — each lot joins the
    # junction whose geometric cell it most overlaps. No cadastre -> geometric cells
    # stand, honestly labelled in diagnostics.
    from swmmcanada.network.parcels import snap_subcatchments_to_parcels
    from swmmcanada.sources.parcels_bc import fetch_bc_parcels

    parcels, parcel_status = fetch_bc_parcels(tuple(aoi.bbox))
    subcatchments, parcel_diag = snap_subcatchments_to_parcels(subcatchments, parcels, aoi)
    sub_diag["cadastre"] = {**parcel_diag, "acquisition": parcel_status}
    subcatchments, water_diag = subtract_water(subcatchments, water, junction_xy, aoi)
    sub_diag = {**(sub_diag or {}), "water": water_diag,
                "synthesis": dict(synth.diagnostics)}   # round-2: cover violations reach
    #                                                     validation.json, not just logs
    sub_diag.setdefault("service", {}).update(
        semantics="full-coverage (ADR 0022): AOI minus open water; pervious land "
                  "contributes via parameters, not exclusion")

    if derive:
        _r("SOIL", 62)
        soil = _acquire_soil_auto(tuple(aoi.bbox), ws, soil_source)
        _r("DERIVE", 70)
        subcatchments = derive_parameters(subcatchments, dem.path, landcover, soil)
        # Physical imperviousness (ADR 0023 cut 1, #138): mapped roofs + road band replace
        # the 30 m land-cover mean wherever buildings are actually mapped; unmapped cells
        # keep the raster value. Buildings are additive — failure means fallback, not a
        # blocked build (the fetcher already degrades to []).
        from swmmcanada.derive.physical import refine_imperviousness

        buildings = fetch_building_footprints(tuple(aoi.bbox))
        subcatchments, phys_diag = refine_imperviousness(subcatchments, buildings, streets, aoi)
        sub_diag["physical_imperviousness"] = phys_diag

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
            "delineation_plan": plan.as_dict(),
            "sources": {
                "dem": type(dem_source).__name__,
                "climate": type(climate_client).__name__,
                "streets": "OSM",
            },
            "subcatchment_diagnostics": sub_diag,
            "pipe_sizing": sizing_diag,
        },
        climate_client=climate_client, climate_buffer_deg=climate_buffer_deg, report=report,
        sub_diag=sub_diag, dem=dem, water=water, served=None, design_storm=design_storm,
    )


def _plan_delineation(spec, bbox, client, network, derive: bool, subcatchment_method: str):
    """Fetch the land evidence and let the resolver choose. Returns ``(land, plan)``.

    Extracted so the decision is testable without a full offline build: this is the seam
    where ADR 0029 Q11 is either honoured or quietly broken, and it needs coverage that does
    not depend on a DEM, a climate service and four open-data hosts being reachable.
    """
    if subcatchment_method != "parcel":
        # An explicit caller override short-circuits the resolver — but it is still a
        # decision, so it is recorded in the same shape. The land fetch is skipped: the plan
        # is already fixed, and paying for evidence nobody will read is waste.
        return {}, DelineationPlan(
            method="junction_voronoi", boundary="aoi", anchors="junction",
            shaping="voronoi", confidence="low",
            reason=f"caller override subcatchment_method={subcatchment_method!r}",
            gates={"caller_override": True}, evidence={})
    land = spec.land(bbox, client)
    return land, resolve(Evidence(
        n_catchbasins=len(land.get("catchbasins") or []),
        n_parcels=len(land.get("parcels") or []),
        n_buildings=len(land.get("buildings") or []),
        n_junctions=len(network.junctions),
        dem_available=bool(derive),
        city=spec.key, system="storm",
    ))


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
    # DEM-as-rim for the invert gap-fill: cities with no rim layer (e.g. North Van
    # District) get terrain-anchored inverts instead of the AOI-wide minimum. Lazy — only
    # fires if the assembler actually has last-resort nodes. Reset so no sampler leaks
    # into other builds in this process.
    surface_sampler = _make_surface_sampler(tuple(aoi.bbox), ws, dem_source)
    _tok = base.SURFACE_SAMPLER.set(surface_sampler)
    try:
        netres = spec.storm(bbox, client)
    finally:
        base.SURFACE_SAMPLER.reset(_tok)
    network = netres.network

    # Subcatchments. The RESOLVER is the sole method-selection entry point (ADR 0029
    # Q10/Q11): it is handed what this AOI actually contains and returns a plan carrying the
    # reason, gates and evidence. This function only executes that plan — it must not branch
    # on data availability itself, or the decision splits across two places and the recorded
    # reason stops being the real one.
    _r("SUBCATCHMENTS", 35)
    imperv_map: dict = {}
    sub_diag: dict = {}
    land, plan = _plan_delineation(spec, bbox, client, network, derive, subcatchment_method)

    subcatchments = []
    if plan.anchors == "catch_basin":
        subcatchments, imperv_map, sub_diag = base.delineate_catchbasin_subcatchments(
            network, land["catchbasins"], land["parcels"], land["buildings"], aoi, crs=spec.sub_crs
        )
    dem = None
    water = None
    landcover = None
    if not subcatchments:  # plan said junctions, or the inlet pass yielded nothing
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
    service_areas: list = []
    if spec.sanitary is not None:
        _r("SANITARY", 78)
        try:
            _tok = base.SURFACE_SAMPLER.set(surface_sampler)  # same DEM tier as storm
            try:
                sanres = spec.sanitary(bbox, client)
            finally:
                base.SURFACE_SAMPLER.reset(_tok)
            network = base.merge_secondary_system(
                network, sanres.network, prefix="SAN_", system="sanitary")
            san_diag = {"included": True,
                        "n_junctions": len(sanres.network.junctions),
                        "n_conduits": len(sanres.network.conduits)}
            # ADR 0031: a sanitary network with no inflow is a drawing, not a model. Derive
            # the service areas and load them, on the SAN_-prefixed subgraph so the areas
            # address the grafted node names rather than the pre-merge ones.
            service_areas, sa_diag = derive_service_areas(
                filter_system(network, "sanitary"), land.get("parcels") or [], aoi,
                laterals=land.get("sanitary_laterals") or land.get("laterals"),
                crs=spec.sub_crs, buildings=land.get("buildings"))
            loaded = load_service_areas(service_areas)
            service_areas = loaded.areas
            san_diag["service_areas"] = {**sa_diag, **loaded.diagnostics}
        except Exception as exc:  # noqa: BLE001 — additive system, degrade with a note
            san_diag = {"included": False, "reason": f"{type(exc).__name__}: {exc}"}
            service_areas = []

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
            "delineation_plan": plan.as_dict(),
            "sanitary": san_diag,
        },
        climate_client=climate_client, climate_buffer_deg=climate_buffer_deg, report=report,
        sub_diag=sub_diag, dem=dem, water=water, design_storm=design_storm, network_kind="city",
        service_areas=service_areas,
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
