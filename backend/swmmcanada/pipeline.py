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
from swmmcanada.delineation.outlet import ensure_wastewater_outlet
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
from swmmcanada.sources.cities.practice import (
    municipal_practice,
    practice_build_overrides,
    practice_provenance,
)
from swmmcanada.sources.cities.registry import CitySpec, city_for_point, city_spec


def _method_descriptor(sub_diag: Optional[dict]) -> MethodDescriptor:
    """Map a delineation's diagnostics to the honest controlled-vocabulary method label."""
    diag = sub_diag or {}
    method = diag.get("method", "")
    # The DEM delineator reports `junction_dem` whatever it was seeded on. Seeded on real
    # inlets it is a different claim, and the label a reader sees must say which
    # (规划书 §4 priorities 2-3).
    if method == vschema.METHOD_JUNCTION_STREET:
        return MethodDescriptor(
            vschema.METHOD_JUNCTION_STREET,
            "each node takes the street it fronts", "medium")
    if method == vschema.METHOD_JUNCTION_PARCEL_ROW:
        return MethodDescriptor(
            vschema.METHOD_JUNCTION_PARCEL_ROW,
            "each parcel its own unit; road right-of-way one unit per node", "medium")
    if method == vschema.METHOD_JUNCTION_DEM and diag.get("seeded_on") == "catch_basin":
        kerbed = (diag.get("urban_conditioning") or {}).get("applied")
        return MethodDescriptor(
            vschema.METHOD_CATCHBASIN_DEM,
            "terrain routed to real inlets" + (" over kerb-aware ground" if kerbed else ""),
            "high" if kerbed else "medium")
    if "parcel-shaped" in method:
        return MethodDescriptor("catchbasin_parcel", "nearest inlet service area", "medium")
    if "voronoi-shaped" in method:
        return MethodDescriptor(vschema.METHOD_CATCHBASIN_VORONOI,
                                "nearest inlet service area", "low")
    if method == "junction_dem":
        return MethodDescriptor("junction_dem", "DEM D8 basins to manholes", "medium")
    return MethodDescriptor(vschema.METHOD_JUNCTION_VORONOI,
                            "nearest node service area", "low")


def _infiltration_kwargs(infiltration) -> dict:
    """BuildConfig kwargs for an optional infiltration override (ADR 0013): accepts an
    InfiltrationModel or its string value; None keeps the config default (Horton)."""
    if infiltration is None:
        return {}
    from swmmcanada.build.config import InfiltrationModel

    return {"infiltration": InfiltrationModel(str(infiltration).upper())}


def _validate_or_raise(network, subcatchments, aoi, method: MethodDescriptor, ws: Path,
                       delineation: Optional[dict] = None, forcing: Optional[dict] = None,
                       water=None, served=None, city_key=None):
    """Validate the subcatchment model, always write validation.json into the package, and
    raise (stopping the build) if any error-severity check fails — so no untrusted .inp ships."""
    report = validate_model(network, subcatchments, aoi, method=method, delineation=delineation,
                            forcing=forcing, water=water, served=served, city_key=city_key)
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


def _aoi_geojson(aoi) -> Optional[dict]:
    """GeoJSON mapping of the AOI polygon (lon/lat), or None when the AOI carries none."""
    geom = getattr(aoi, "geometry", None)
    if geom is None:
        return None
    try:
        from shapely.geometry import mapping

        return mapping(geom)
    except Exception:  # noqa: BLE001 — provenance is advisory
        return None


def _export_hecras_safe(ws: Path) -> None:
    """Emit the HEC-RAS (RAS Mapper) import package into ``ws/hecras`` (ADR 0033). Same
    contract as MIKE+/ICM: every build, never load-bearing. It reads the 2D raw materials
    beside it (``dem_dtm.tif`` / ``landcover.tif`` / ``hsg.tif`` at the package root)."""
    try:
        from swmmcanada.export import export_hecras

        export_hecras(ws / result_package.DATASTORE_DIR, ws / result_package.HECRAS_DIR,
                      package_root=ws)
    except Exception as exc:  # noqa: BLE001 — HEC-RAS export must never break the build
        target = ws / result_package.HECRAS_DIR
        target.mkdir(parents=True, exist_ok=True)
        (target / "EXPORT_FAILED.txt").write_text(f"HEC-RAS export failed: {exc!r}\n")


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
    # extra_provenance carries "city" only on the real-network pathway — exactly the
    # scoping the cross-check's municipal leg wants (synthesis has no registered table).
    _validate_or_raise(network, subcatchments, aoi, method, ws, delineation=sub_diag,
                       forcing=forcing or None, water=water, served=served,
                       city_key=extra_provenance.get("city"))

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
            # The drawn AOI itself (lon/lat GeoJSON), so a reader can rebuild the boundary
            # — the HEC-RAS package's 2D flow-area perimeter (ADR 0033) — from the datastore.
            "aoi_geojson": _aoi_geojson(aoi),
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
    _export_hecras_safe(ws)  # ADR 0033: HEC-RAS RAS Mapper package — every build, graceful
    _export_observed_safe(ws, aoi, start, end)  # observed flow (HYDAT) — real data when present

    # Map preview: GeoJSON of the model geometry for the frontend's layers.
    preview_path = ws / result_package.PREVIEW_GEOJSON
    preview_path.parent.mkdir(exist_ok=True)
    preview_path.write_text(json.dumps(
        network_geojson(network, subcatchments, service_areas=service_areas)))

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
    # ADR 0029 Q3: accepted for interface symmetry with build_city — the API does not know
    # which pathway an AOI will take. Synthesis produces a storm network only, so a
    # selection cannot change what is built; it is recorded in provenance so a package that
    # was asked for sanitary and could not supply it says so rather than looking complete.
    systems=None,
    landcover_source=None,
    soil_source=None,
    infiltration=None,
    design_storm=None,
    # Spec §G2: accepted for interface symmetry with build_city (same reasoning as
    # `systems` above). Synthesis has no city, so there is never a practice record to
    # follow; the request is recorded honestly rather than erroring or vanishing.
    follow_municipal_practice: bool = False,
    # Spec §G4: Green-Ampt IMD antecedent convention. None/"dry" keeps the table's
    # theta_e (the fleet default, bit-for-bit); "field_capacity" derives theta_e -
    # theta_fc. Applies to the GA parameter superset derive stores.
    ga_antecedent=None,
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
        subcatchments = derive_parameters(subcatchments, dem.path, landcover, soil,
                                          ga_antecedent=ga_antecedent or "dry")
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
            "systems": {"requested": list(systems) if systems else None,
                        "produced": ["storm"],
                        "note": ("synthesis builds a storm network only; any other system "
                                 "requested was not available from open data for this AOI")},
            "sources": {
                "dem": type(dem_source).__name__,
                "climate": type(climate_client).__name__,
                "streets": "OSM",
            },
            "subcatchment_diagnostics": sub_diag,
            "pipe_sizing": sizing_diag,
            # Spec §G4: the IMD antecedent convention the GA parameter superset used.
            "ga_antecedent": ga_antecedent or "dry",
            # Spec §G2: which municipal-practice items this build had on record (none on
            # the synthesis pathway — there is no city) and whether following was asked.
            "municipal_practice": practice_provenance(None, follow=follow_municipal_practice),
        },
        climate_client=climate_client, climate_buffer_deg=climate_buffer_deg, report=report,
        sub_diag=sub_diag, dem=dem, water=water, served=None, design_storm=design_storm,
    )


def _outlet_agreement_provenance(spec, bbox, client, subcatchments, network) -> dict:
    """Score this build's outlet resolution against the city's own declaration (#129).

    Additive and non-blocking: the yardstick is a nice-to-have, and a municipal server being
    down must never fail a model. A city that publishes nothing usable says so explicitly —
    silence would be indistinguishable from "not measured yet".
    """
    from swmmcanada.validate.outlet_agreement import official_outlet_agreement

    if getattr(spec, "official_catchments", None) is None:
        return {"rate_pct": None,
                "reason": f"{spec.key} publishes no catchment layer with a joinable "
                          f"outlet key"}
    try:
        official = spec.official_catchments(bbox, client)
        rate, diag = official_outlet_agreement(subcatchments, network, official)
    except Exception as exc:  # noqa: BLE001 — additive metric, degrade with a note
        return {"rate_pct": None, "reason": f"{type(exc).__name__}: {exc}"}
    return {"rate_pct": (round(rate * 100, 1) if rate is not None else None), **diag}


def _subcatchments_from_user_layer(features, network, crs, laterals=None):
    """Turn an uploaded layer into subcatchments (resolver priority 0).

    The boundaries are the user's and are used verbatim — no reshaping, no merging, no
    sliver discipline. Outlets are resolved the way every other path resolves them unless
    the layer names one, because a polygon file rarely carries our node ids.

    Geometry is repaired if invalid, since a broken ring would crash downstream exactly as
    a broken parcel did, and repairs are counted rather than hidden.
    """
    from shapely.geometry import shape as shp_shape
    from shapely.ops import transform as _tf

    from swmmcanada.build.models import SurfaceCatchment
    from swmmcanada.geo.crs import lonlat_projector

    to_m = lonlat_projector(crs)
    outlet_of = base._outlet_resolver(network, crs, laterals)
    known = {n.name for n in list(network.junctions) + list(network.outfalls)}

    subs, n_repaired, n_named_outlet, n_dropped = [], 0, 0, 0
    for i, f in enumerate(features or []):
        geom = (f or {}).get("geometry")
        if not geom:
            n_dropped += 1
            continue
        try:
            poly = shp_shape(geom)
        except Exception:  # noqa: BLE001 — one bad feature is not a failed upload
            n_dropped += 1
            continue
        if not poly.is_valid:
            poly = poly.buffer(0)
            n_repaired += 1
        if poly.is_empty or poly.geom_type not in ("Polygon", "MultiPolygon"):
            n_dropped += 1
            continue
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)

        props = f.get("properties") or {}
        name = str(props.get("name") or props.get("NAME") or f"U{i + 1}")
        declared = props.get("outlet") or props.get("OUTLET") or props.get("outlet_node")
        rep = poly.representative_point()
        if declared and str(declared) in known:
            outlet = str(declared)
            n_named_outlet += 1
        else:
            outlet = outlet_of((rep.x, rep.y))

        ring = [(float(x), float(y)) for x, y in poly.exterior.coords]
        poly_m = _tf(to_m, poly)
        area_m2 = poly_m.area
        subs.append(SurfaceCatchment(
            name=name, outlet_node=outlet, area_ha=area_m2 / 1e4,
            # Placeholders: `derive` overwrites imperviousness, slope and CN from the DEM,
            # land cover and soil exactly as it does for cells we drew. Only the boundary
            # is the user's.
            pct_imperv=50.0,
            width_m=area_m2 / base.characteristic_flow_length_m(poly_m, (rep.x, rep.y)),
            pct_slope=1.0, polygon=ring))

    return subs, {
        "method": "user_supplied",
        "n_subcatchments": len(subs),
        "n_geometry_repaired": n_repaired,
        "n_dropped_invalid": n_dropped,
        "n_outlet_declared_by_user": n_named_outlet,
        "note": ("boundaries are the user's and are used verbatim; outlets are resolved "
                 "here unless the layer names one this network contains"),
    }


def _apply_official_boundary(subcatchments, official_features, crs):
    """Trim cells to the published basin they drain to (规划书 §4, ADR 0029 Q2).

    Cells arrive as EPSG:4326 rings and the cut has to happen in metres, so each is
    projected, clipped, and projected back. Only cells the clip actually changed are
    rebuilt; the rest are passed through untouched.
    """
    from pyproj import Transformer
    from shapely.geometry import Polygon, shape as shp_shape
    from shapely.ops import transform as _tf

    from swmmcanada.delineation.boundary import clip_to_official_basins
    from swmmcanada.geo.crs import lonlat_projector

    if not official_features or not subcatchments:
        return subcatchments, {"applied": False, "reason": "no official layer or no cells"}

    to_m = lonlat_projector(crs)
    basins = []
    for f in official_features:
        g = (f or {}).get("geometry")
        if not g:
            continue
        try:
            poly = _tf(to_m, shp_shape(g))
        except Exception:  # noqa: BLE001 — a broken basin is not a build failure
            continue
        if poly.is_valid and not poly.is_empty:
            basins.append({"geometry": poly,
                           "outlet": (f.get("properties") or {}).get("OUTLET")})

    class _View:
        """Mutable stand-in the clipper can rewrite; carries its cell and its original."""

        def __init__(self, polygon, seed, src):
            self.polygon, self.seed, self.src, self.original = polygon, seed, src, polygon

    views = []
    for sub in subcatchments:
        if not sub.polygon or len(sub.polygon) < 4:
            continue
        poly_m = _tf(to_m, Polygon(sub.polygon))
        rep = poly_m.representative_point()
        views.append(_View(poly_m, (rep.x, rep.y), sub))

    _clipped, diag = clip_to_official_basins(views, basins)
    back = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    rebuilt = {}
    for v in views:
        if v.polygon is v.original:
            continue
        ring = _tf(back, v.polygon)
        if ring.is_empty or ring.geom_type != "Polygon":
            continue
        rebuilt[id(v.src)] = replace(
            v.src, area_ha=v.polygon.area / 1e4,
            polygon=[(float(x), float(y)) for x, y in ring.exterior.coords])

    return [rebuilt.get(id(s), s) for s in subcatchments], diag


def _dem_crs_geoms(features, dem):
    """Reproject GeoJSON features into the DEM's grid, or ``[]``. The conditioner edits a
    raster and needs its geometry in the same frame."""
    if not features or dem is None:
        return []
    import rasterio
    from shapely.geometry import shape as shp_shape
    from shapely.ops import transform as _tf

    from swmmcanada.geo.crs import lonlat_projector

    try:
        with rasterio.open(dem.path) as src:
            to_dem = lonlat_projector(str(src.crs))
    except Exception:  # noqa: BLE001 — an unreadable surface costs the refinement only
        return []
    out = []
    for f in features:
        g = (f or {}).get("geometry")
        if not g:
            continue
        try:
            out.append(_tf(to_dem, shp_shape(g)))
        except Exception:  # noqa: BLE001 — one broken feature is not a failed build
            continue
    return out


def _plan_delineation(spec, bbox, client, network, derive: bool, subcatchment_method: str,
                      dem_resolution_m=None, user_layer=None, streets=None):
    """Fetch the land evidence and let the resolver choose. Returns ``(land, plan)``.

    Extracted so the decision is testable without a full offline build: this is the seam
    where ADR 0029 Q11 is either honoured or quietly broken, and it needs coverage that does
    not depend on a DEM, a climate service and four open-data hosts being reachable.
    """
    if subcatchment_method not in ("parcel", "parcel_row"):
        # An explicit caller override short-circuits the resolver — but it is still a
        # decision, so it is recorded in the same shape. The land fetch is skipped: the plan
        # is already fixed, and paying for evidence nobody will read is waste.
        return {}, DelineationPlan(
            method=vschema.METHOD_JUNCTION_VORONOI, boundary="aoi",
            anchors="junction",
            shaping="voronoi", confidence="low",
            reason=f"caller override subcatchment_method={subcatchment_method!r}",
            gates={"caller_override": True}, evidence={})
    # "parcel_row" is NOT an override: it is a request the resolver must weigh against the
    # AOI's evidence (no parcels here means the mode cannot run, and that verdict belongs
    # with the recorded reason, not in this function).
    requested = (vschema.METHOD_JUNCTION_PARCEL_ROW
                 if subcatchment_method == "parcel_row" else None)
    land = spec.land(bbox, client)
    # Phase 0 measured every published catchment layer in the fleet as macro, so a city that
    # publishes one publishes a Level 2 boundary. Additive: a municipal server being down
    # must not fail a model, it just means no hard edge.
    official_level = None
    if getattr(spec, "official_catchments", None) is not None:
        try:
            if spec.official_catchments(bbox, client):
                official_level = "level_2"
        except Exception:  # noqa: BLE001 — the boundary is a bonus, never a blocker
            official_level = None

    return land, resolve(Evidence(
        n_catchbasins=len(land.get("catchbasins") or []),
        n_parcels=len(land.get("parcels") or []),
        n_buildings=len(land.get("buildings") or []),
        n_kerbs=len(land.get("kerbs") or []),
        n_user_units=len(user_layer or []),
        n_streets=(streets.number_of_edges() if streets is not None else 0),
        n_junctions=len(network.junctions),
        dem_available=bool(derive),
        dem_resolution_m=dem_resolution_m,
        official_basin_level=official_level,
        city=spec.key, system="storm",
    ), requested_method=requested)


def build_city(
    city, aoi, start: date, end: date, workspace, *,
    client=None,
    dem_source=None, climate_client=None, climate_buffer_deg: float = 0.3, derive: bool = True,
    landcover_source=None, soil_source=None,
    #: "parcel" lets the resolver choose from evidence; "parcel_row" REQUESTS the
    #: municipal-reproduction units (parcels whole + road right-of-way per node), which
    #: the resolver honours when the AOI has nodes and parcels and otherwise records as an
    #: unmet request; anything else is a caller override straight to nearest-node.
    subcatchment_method: str = "parcel",
    infiltration=None, design_storm=None,
    #: Spec §G2: follow this city's registered municipal practice. Where the record
    #: states a convention the build can consume (infiltration method, GA conventions,
    #: surface parameter set), it generates the parameters for THIS build; everything
    #: else stated stays information-only. Fleet defaults are untouched for every build
    #: that does not select it, and provenance lists consumed vs information-only.
    follow_municipal_practice: bool = False,
    #: Spec §G4: Green-Ampt IMD antecedent convention. None/"dry" keeps the table's
    #: theta_e (the fleet default, bit-for-bit); "field_capacity" derives theta_e -
    #: theta_fc. A registered practice's stated antecedent wins this knob under follow.
    ga_antecedent=None,
    report=None, systems=None,
    #: A subcatchment layer the user uploaded (GeoJSON features). Resolver priority 0 —
    #: their boundaries override every method here, because every choice this module makes
    #: is a judgement call and theirs is the one with local knowledge behind it.
    subcatchment_layer=None,
) -> BuildResult:
    """Build a SWMM model from a real municipal network (ADR 0004/0005/0006). ``city`` is a
    registry key ("victoria") or a ``CitySpec``; the spec supplies the city's fetch/build
    composition, metric CRS and provenance. Everything else — subcatchments (catch-basin +
    parcel/building, Voronoi-of-nodes fallback), derive, climate, build, datastore — is
    city-agnostic. ``client`` is passed to the spec's fetchers (tests inject fixtures here)."""
    spec: CitySpec = city_spec(city) if isinstance(city, str) else city
    bbox = tuple(aoi.bbox)

    # "Follow municipal practice" consumption (spec §G2/§V4): where the registered
    # record states a convention, it wins that knob — the option means "build it the
    # way this city models it", and the frontend always posts an infiltration choice,
    # so a caller-wins precedence would leave the option permanently dead through the
    # UI. Resolution touches this call's locals only; fleet defaults stay untouched for
    # every build that does not select it.
    practice_overrides = practice_build_overrides(
        municipal_practice(spec.key) if follow_municipal_practice else None)
    if practice_overrides["infiltration"] is not None:
        infiltration = practice_overrides["infiltration"]
    if practice_overrides["ga_antecedent"] is not None:
        ga_antecedent = practice_overrides["ga_antecedent"]

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
    # The surface is acquired BEFORE planning (规划书 §4): a plan that prefers terrain has
    # to know the terrain's posting, and acquiring it afterwards left the resolver choosing
    # between methods with `dem_resolution_m=None` — the terrain path existed and was
    # unreachable, which is worse than not having it.
    dem = None
    if derive:
        _r("ACQUIRING_DEM", 30)
        dem = acquire_dem(tuple(aoi.bbox), ws, source=dem_source)

    # Streets, before planning: frontage splitting is the municipal unit and the plan cannot
    # prefer it without knowing whether the streets are there. Additive — OSM being
    # unreachable costs the shape, never the build.
    streets = None
    try:
        streets = fetch_street_graph(tuple(aoi.bbox))
    except Exception:  # noqa: BLE001
        streets = None

    _r("SUBCATCHMENTS", 35)
    imperv_map: dict = {}
    sub_diag: dict = {}
    land, plan = _plan_delineation(spec, bbox, client, network, derive, subcatchment_method,
                                   dem_resolution_m=(dem.resolution_m if dem else None),
                                   user_layer=subcatchment_layer, streets=streets)

    subcatchments = []
    if plan.shaping == "user":
        # Priority 0: used verbatim. Nothing below runs, and the official boundary is not
        # applied either — clipping someone else's boundaries to our reading of a municipal
        # layer would be overriding the choice we just said outranks us.
        subcatchments, sub_diag = _subcatchments_from_user_layer(
            subcatchment_layer, network, spec.sub_crs, land.get("laterals"))
    if not subcatchments and plan.shaping == "street_segment":
        # The municipal unit: each node takes the land draining to its own reach — the
        # street segment plus the lots fronting it, back to the rear-lot line. Nearest-POINT
        # assignment carves a block into a triangle fan meeting at its centre, which is
        # nothing a city would draw.
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        inlet_xy = [tuple(((f.get("geometry") or {}).get("coordinates") or [])[:2])
                    for f in (land.get("catchbasins") or [])
                    if len((f.get("geometry") or {}).get("coordinates") or []) >= 2]
        subcatchments, sub_diag = delineate_junction_subcatchments(
            junction_xy, aoi, dem_path=(dem.path if dem else None), streets=streets,
            service_mask=aoi.geometry, min_cell_ha=MIN_CELL_HA, inlets=inlet_xy)
    if not subcatchments and plan.shaping == "dem_d8":
        # No streets published: land follows the ground to its node instead of the frontage
        # it cannot see. Same delineator, one input fewer.
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        subcatchments, sub_diag = delineate_junction_subcatchments(
            junction_xy, aoi, dem_path=(dem.path if dem else None),
            kerbs=_dem_crs_geoms(land.get("kerbs"), dem),
            openings=_dem_crs_geoms(land.get("kerb_openings"), dem),
            buildings=_dem_crs_geoms(land.get("buildings"), dem),
            snap_pour_points=True)
    if not subcatchments and plan.shaping == "parcel":
        # Neither streets nor a usable surface: the boundary between neighbouring nodes
        # follows real lot lines rather than a bisector through the middle of a garden.
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        subcatchments, imperv_map, sub_diag = base.delineate_catchbasin_subcatchments(
            network, [{"type": "Feature", "properties": {"AssetID": n},
                       "geometry": {"type": "Point", "coordinates": list(xy)}}
                      for n, xy in junction_xy.items()],
            land.get("parcels") or [], land.get("buildings") or [], aoi,
            crs=spec.sub_crs, laterals=land.get("laterals"),
        )
    if not subcatchments and plan.shaping == "parcel_row":
        # Requested municipal reproduction: every parcel is its own unit draining to the
        # pipe of the street it fronts, and the road right-of-way (AOI minus parcels)
        # forms one unit per node — the way a municipal drawing is organised, kept apart
        # instead of dissolved per node, so a build can be compared against one
        # unit-for-unit.
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        subcatchments, imperv_map, sub_diag = base.delineate_parcel_row_subcatchments(
            network, junction_xy, land.get("parcels") or [], land.get("buildings") or [],
            aoi, crs=spec.sub_crs, laterals=land.get("laterals"),
        )
    water = None
    landcover = None
    if not subcatchments:  # plan said junctions, or the inlet pass yielded nothing
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        subcatchments, sub_diag = delineate_junction_subcatchments(
            junction_xy, aoi, dem_path=(dem.path if dem else None))
        imperv_map = {}
        if derive:  # water masking for the junction fallback (ADR 0016; parcel cells skip it)
            landcover = acquire_landcover(tuple(aoi.bbox), ws, source=landcover_source or NRCanLandcoverSource())
            water = water_union(landcover.raster_path, aoi)
            subcatchments, water_diag = subtract_water(subcatchments, water, junction_xy, aoi)
            sub_diag = {**(sub_diag or {}), "water": water_diag}

    # The hard edge the plan asked for. Applied to what WE drew, never to an uploaded layer.
    boundary_diag = {"applied": False, "reason": "plan did not ask for one"}
    if plan.boundary == "official_basin" and plan.shaping != "user" and subcatchments:
        try:
            subcatchments, boundary_diag = _apply_official_boundary(
                subcatchments, spec.official_catchments(bbox, client), spec.sub_crs)
        except Exception as exc:  # noqa: BLE001 — a bonus edge, never a blocker
            boundary_diag = {"applied": False, "reason": f"{type(exc).__name__}: {exc}"}
    sub_diag = {**(sub_diag or {}), "official_boundary": boundary_diag}

    if derive:
        if dem is None:
            _r("ACQUIRING_DEM", 45)
            dem = acquire_dem(tuple(aoi.bbox), ws, source=dem_source)
        _r("LANDCOVER_SOIL", 60)
        if landcover is None:
            landcover = acquire_landcover(tuple(aoi.bbox), ws, source=landcover_source or NRCanLandcoverSource())
        soil = _acquire_soil_auto(tuple(aoi.bbox), ws, soil_source)
        _r("DERIVE", 70)
        subcatchments = derive_parameters(subcatchments, dem.path, landcover, soil,
                                          ga_antecedent=ga_antecedent or "dry")
        if imperv_map:  # restore parcel/building imperviousness (derive overwrote it)
            subcatchments = [
                replace(s, pct_imperv=imperv_map[s.name]) if s.name in imperv_map else s
                for s in subcatchments
            ]

    # Followed-practice parameter transforms (spec §V4): the stated "Ksat not halved"
    # convention recovers the source-table value from the halved fleet table (x2 — see
    # practice_build_overrides), and the stated surface set replaces the fleet SUBAREAS
    # defaults. Plain field transforms, applied with or without derive; both are no-ops
    # unless follow_municipal_practice found a record stating them.
    if practice_overrides["ga_ksat_scale"] != 1.0:
        subcatchments = [
            replace(s, ga_ksat_mm_h=s.ga_ksat_mm_h * practice_overrides["ga_ksat_scale"])
            for s in subcatchments
        ]
    if practice_overrides["surface_parameters"]:
        subcatchments = [replace(s, **practice_overrides["surface_parameters"])
                         for s in subcatchments]

    # Sanitary tracer (ADR 0011): where the city publishes a sanitary layer, graft it in
    # as a tagged, disconnected subgraph — AFTER subcatchments (they are storm-seeded) and
    # with graceful degradation (a sanitary fetch failure never blocks the storm build).
    san_diag = {"included": False, "reason": "not_published"}
    service_areas: list = []
    # ADR 0029 Q3: a selection the user made is honoured at BUILD time, not just at export.
    # Grafting a system nobody asked for and filtering it out later would still pay for the
    # fetch and still put it in the datastore.
    if systems is not None and "sanitary" not in systems:
        san_diag = {"included": False, "reason": "not selected"}
    elif spec.sanitary is not None:
        _r("SANITARY", 78)
        try:
            _tok = base.SURFACE_SAMPLER.set(surface_sampler)  # same DEM tier as storm
            try:
                sanres = spec.sanitary(bbox, client)
            finally:
                base.SURFACE_SAMPLER.reset(_tok)
            network = base.merge_secondary_system(
                network, sanres.network, prefix="SAN_", system="sanitary")
            # ADR 0029 Q4: give the wastewater system a destination of its own. No
            # supported city publishes a CSO structure (Phase 0), so this is almost always
            # a synthetic interceptor/WWTP boundary — labelled as one, never a borrowed
            # storm outfall.
            network, outlet_diag = ensure_wastewater_outlet(network, system="sanitary")
            san_diag = {"included": True, "terminal_outlet": outlet_diag,
                        "n_junctions": len(sanres.network.junctions),
                        "n_conduits": len(sanres.network.conduits)}
            # ADR 0031: a sanitary network with no inflow is a drawing, not a model. Derive
            # the service areas and load them, on the SAN_-prefixed subgraph so the areas
            # address the grafted node names rather than the pre-merge ones.
            service_areas, sa_diag = derive_service_areas(
                filter_system(network, "sanitary"), land.get("parcels") or [], aoi,
                laterals=land.get("sanitary_laterals") or land.get("laterals"),
                crs=spec.sub_crs, buildings=land.get("buildings"))
            # Ticket 10: a followed practice's stated DWF pattern structure configures
            # the loading layer's pattern group; None keeps the single hourly default.
            loaded = load_service_areas(
                service_areas,
                pattern_structure=practice_overrides["dwf_pattern_structure"])
            service_areas = loaded.areas
            san_diag["service_areas"] = {**sa_diag, **loaded.diagnostics}
        except Exception as exc:  # noqa: BLE001 — additive system, degrade with a note
            san_diag = {"included": False, "reason": f"{type(exc).__name__}: {exc}"}
            service_areas = []

    # Combined DWF (ADR 0029 Q1: combined is dual-source). Combined mains arrive inside
    # the storm network wearing their own tag (Q3) — the runoff side is already theirs;
    # what was missing is the wastewater the same land sends them in dry weather. Reuse
    # the sanitary service-area pipeline on the combined subgraph: seeds fall back to the
    # combined manholes where the city publishes no lateral layer (Vancouver). No graft,
    # no namespace wall, no extra outlet — the trunk stays wired into the storm graph and
    # drains where it always drained.
    comb_diag: dict = {"included": False, "reason": "no combined mains in this model"}
    if systems is not None and "combined" not in systems:
        comb_diag = {"included": False, "reason": "not selected"}
    elif any(c.system == "combined" for c in network.conduits):
        try:
            comb_areas, csa_diag = derive_service_areas(
                filter_system(network, "combined"), land.get("parcels") or [], aoi,
                laterals=land.get("sanitary_laterals") or land.get("laterals"),
                crs=spec.sub_crs, system="combined", buildings=land.get("buildings"))
            # Same loading configuration as the sanitary leg: one build, one pattern
            # group (the writer refuses a split).
            comb_loaded = load_service_areas(
                comb_areas, pattern_structure=practice_overrides["dwf_pattern_structure"])
            service_areas = list(service_areas) + comb_loaded.areas
            comb_diag = {"included": True,
                         "service_areas": {**csa_diag, **comb_loaded.diagnostics}}
        except Exception as exc:  # noqa: BLE001 — additive load, degrade with a note
            comb_diag = {"included": False, "reason": f"{type(exc).__name__}: {exc}"}

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
            "official_outlet_agreement": _outlet_agreement_provenance(
                spec, bbox, client, subcatchments, network),
            "sanitary": san_diag,
            "combined": comb_diag,
            # Spec §G4: the IMD antecedent convention the GA parameter superset used
            # (a followed practice's stated antecedent already resolved into this).
            "ga_antecedent": ga_antecedent or "dry",
            # Spec §G2: the practice inventory for THIS city — which registered items
            # this build consumed (only under "follow") and which it merely had on
            # record; the block separates the two explicitly.
            "municipal_practice": practice_provenance(
                spec.key, follow=follow_municipal_practice),
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
