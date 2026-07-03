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
from swmmcanada.sources.streets_osm import fetch_street_graph, sample_elevations
from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.ottawa import build_ottawa_network, fetch_ottawa_land, fetch_ottawa_storm
from swmmcanada.sources.cities.victoria import (
    build_victoria_network,
    fetch_victoria_land,
    fetch_victoria_storm,
)
from swmmcanada.sources.cities.london import (
    build_london_network,
    fetch_london_land,
    fetch_london_storm,
)
from swmmcanada.sources.cities.kitchener import (
    build_kitchener_network,
    fetch_kitchener_land,
    fetch_kitchener_storm,
)
from swmmcanada.sources.cities.calgary import (
    build_calgary_network,
    fetch_calgary_land,
    fetch_calgary_storm,
)
from swmmcanada.sources.cities.surrey import (
    build_surrey_network,
    fetch_surrey_land,
    fetch_surrey_storm,
)
from swmmcanada.sources.cities.kelowna import (
    build_kelowna_network,
    fetch_kelowna_land,
    fetch_kelowna_storm,
)
from swmmcanada.sources.cities.regina import (
    build_regina_network,
    fetch_regina_land,
    fetch_regina_sanitary,
    fetch_regina_storm,
)


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


def _validate_or_raise(network, subcatchments, aoi, method: MethodDescriptor, ws: Path,
                       delineation: Optional[dict] = None):
    """Validate the subcatchment model, always write validation.json into the package, and
    raise (stopping the build) if any error-severity check fails — so no untrusted .inp ships."""
    report = validate_model(network, subcatchments, aoi, method=method, delineation=delineation)
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


def _finish_build(
    ws: Path, aoi, network, subcatchments, *, start: date, end: date, method,
    config: BuildConfig, extra_provenance: dict, climate_client, climate_buffer_deg: float,
    report=None, sub_diag: Optional[dict] = None,
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
    climate = fetch_climate(aoi, start, end, client=climate_client, near_buffer_deg=climate_buffer_deg)
    series = next((s for s in climate.series if not s.frame.empty), None)
    if series is None:
        raise RuntimeError("No climate data available for this AOI/period.")
    rain = to_rainfall_series(series)
    evaporation = to_evaporation_series(series)
    temperature = to_temperature_series(series)

    _r("VALIDATING", 85)
    _validate_or_raise(network, subcatchments, aoi, method, ws, delineation=sub_diag)

    _r("BUILDING", 90)
    # Datastore is the PRIMARY build path (ADR 0007): write it, then build the .inp from it.
    write_datastore(
        ws / result_package.DATASTORE_DIR, network=network, subcatchments=subcatchments, rain=rain,
        config=config, evaporation=evaporation, temperature=temperature,
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
    _export_mikeplus_safe(ws)  # ADR 0008: MIKE+ CS package — every build, graceful
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

    _r("NETWORK", 55)
    synth = synthesise_network(streets, aoi=aoi)
    # Delineation v2 (ADR 0010): DEM D8 basins (street-burned) behind the terrain honesty
    # gate; flat/noisy terrain falls back to junction-Voronoi with the reading recorded.
    junction_xy = {j.name: (j.x, j.y) for j in synth.network.junctions}
    subcatchments, sub_diag = delineate_junction_subcatchments(
        junction_xy, aoi, dem_path=dem.path, streets=streets)

    if derive:
        _r("LANDCOVER_SOIL", 62)
        landcover = acquire_landcover(tuple(aoi.bbox), ws, source=landcover_source or NRCanLandcoverSource())
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
    config = BuildConfig(out_dir=ws, start=start, end=end, coordinate_crs=utm_crs_for(aoi))
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
        sub_diag=sub_diag,
    )


def _build_real_network(
    aoi, start: date, end: date, workspace, *,
    network_fn, land_fn, sub_crs: str, city: str, network_source: str,
    dem_source=None, climate_client=None, climate_buffer_deg: float = 0.3, derive: bool = True,
    landcover_source=None, soil_source=None, subcatchment_method: str = "parcel", report=None,
    sanitary_fn=None,
) -> BuildResult:
    """Shared real-municipal-network pipeline (ADR 0006). ``network_fn(aoi)`` assembles the
    city's real pipes (returns an object with ``.network`` + ``.diagnostics``); ``land_fn(aoi)``
    supplies ``{catchbasins, parcels, buildings}``. Everything else — subcatchments
    (catch-basin + parcel/building, Voronoi-of-nodes fallback), derive, climate, build,
    datastore — is city-agnostic. ``sub_crs`` is the city's metric CRS."""
    def _r(stage: str, pct: int):
        if report:
            report(stage, pct)

    dem_source = _dem_source_auto(dem_source)
    climate_client = climate_client or GeoMetClient()
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    _r("FETCH_NETWORK", 15)
    netres = network_fn(aoi)
    network = netres.network

    # Subcatchments: catch-basin + parcel/building (ADR 0005), else Voronoi-of-nodes fallback.
    _r("SUBCATCHMENTS", 35)
    imperv_map: dict = {}
    sub_diag: dict = {}
    if subcatchment_method == "parcel":
        land = land_fn(aoi)
        subcatchments, imperv_map, sub_diag = base.delineate_catchbasin_subcatchments(
            network, land["catchbasins"], land["parcels"], land["buildings"], aoi, crs=sub_crs
        )
    else:
        subcatchments = []
    dem = None
    if not subcatchments:  # no catch-basin data -> junction delineation (DEM-gated, ADR 0010)
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        if derive:  # the DEM is needed for derive anyway; without derive there is none → "no_dem"
            _r("ACQUIRING_DEM", 40)
            dem = acquire_dem(tuple(aoi.bbox), ws, source=dem_source)
        subcatchments, sub_diag = delineate_junction_subcatchments(
            junction_xy, aoi, dem_path=(dem.path if dem else None))
        imperv_map = {}

    if derive:
        if dem is None:
            _r("ACQUIRING_DEM", 45)
            dem = acquire_dem(tuple(aoi.bbox), ws, source=dem_source)
        _r("LANDCOVER_SOIL", 60)
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
    if sanitary_fn is not None:
        _r("SANITARY", 78)
        try:
            sanres = sanitary_fn(aoi)
            network = base.merge_secondary_system(
                network, sanres.network, prefix="SAN_", system="sanitary")
            san_diag = {"included": True,
                        "n_junctions": len(sanres.network.junctions),
                        "n_conduits": len(sanres.network.conduits)}
        except Exception as exc:  # noqa: BLE001 — additive system, degrade with a note
            san_diag = {"included": False, "reason": f"{type(exc).__name__}: {exc}"}

    # Head done (network producer = the city adapter); the shared build spine does the rest.
    method = _method_descriptor(sub_diag)
    config = BuildConfig(out_dir=ws, start=start, end=end, title=f"SWMMCanada ({city} real network)",
                         coordinate_crs=sub_crs)
    return _finish_build(
        ws, aoi, network, subcatchments,
        start=start, end=end, method=method, config=config,
        extra_provenance={
            "city": city, "network_source": network_source,
            "network_diagnostics": netres.diagnostics,
            "subcatchment_diagnostics": sub_diag,
            "sanitary": san_diag,
        },
        climate_client=climate_client, climate_buffer_deg=climate_buffer_deg, report=report,
        sub_diag=sub_diag,
    )


def build_from_victoria(aoi, start: date, end: date, workspace, *, victoria_client=None,
                        subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL City of Victoria storm network (ADR 0004/0005)."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_victoria_network(**fetch_victoria_storm(tuple(a.bbox), client=victoria_client)),
        land_fn=lambda a: fetch_victoria_land(tuple(a.bbox), client=victoria_client),
        sub_crs="EPSG:32610", city="victoria",
        network_source="City of Victoria storm drain (real municipal network)",
        subcatchment_method=subcatchment_method, report=report, **kwargs)


def build_from_ottawa(aoi, start: date, end: date, workspace, *, ottawa_client=None,
                      subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL City of Ottawa storm network (ADR 0006). Ottawa has no
    public parcels/buildings, so subcatchments seed on real catch basins with land-cover
    imperviousness (the parcel/building override is unavailable there)."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_ottawa_network(fetch_ottawa_storm(tuple(a.bbox), client=ottawa_client)),
        land_fn=lambda a: fetch_ottawa_land(tuple(a.bbox), client=ottawa_client),
        sub_crs="EPSG:32618", city="ottawa",
        network_source="City of Ottawa storm sewer (real municipal network)",
        subcatchment_method=subcatchment_method, report=report, **kwargs)


def build_from_london(aoi, start: date, end: date, workspace, *, london_client=None,
                      subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL City of London (ON) storm network (ADR 0004/0005/0006).
    Explicit node-id topology (UpstreamID/DownstreamID -> GIS_FeatureKey); parcels + buildings."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_london_network(**fetch_london_storm(tuple(a.bbox), client=london_client)),
        land_fn=lambda a: fetch_london_land(tuple(a.bbox), client=london_client),
        sub_crs="EPSG:32617", city="london",
        network_source="City of London storm sewer (real municipal network)",
        subcatchment_method=subcatchment_method, report=report, **kwargs)


def build_from_kitchener(aoi, start: date, end: date, workspace, *, kitchener_client=None,
                         subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL Region of Waterloo storm network (Kitchener/Waterloo/
    Cambridge, ADR 0006). Explicit integer manhole-id topology; no parcel polygons published, so
    subcatchments fall back to catch-basin Voronoi (buildings are available)."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_kitchener_network(**fetch_kitchener_storm(tuple(a.bbox), client=kitchener_client)),
        land_fn=lambda a: fetch_kitchener_land(tuple(a.bbox), client=kitchener_client),
        sub_crs="EPSG:32617", city="kitchener",
        network_source="Region of Waterloo storm sewer (real municipal network)",
        subcatchment_method=subcatchment_method, report=report, **kwargs)


def build_from_calgary(aoi, start: date, end: date, workspace, *, calgary_client=None,
                       subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL City of Calgary storm network (ADR 0006). Geometry-inferred
    topology; parcels + buildings published."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_calgary_network(fetch_calgary_storm(tuple(a.bbox), client=calgary_client)),
        land_fn=lambda a: fetch_calgary_land(tuple(a.bbox), client=calgary_client),
        sub_crs="EPSG:32611", city="calgary",
        network_source="City of Calgary storm sewer (real municipal network)",
        subcatchment_method=subcatchment_method, report=report, **kwargs)


def build_from_surrey(aoi, start: date, end: date, workspace, *, surrey_client=None,
                      subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL City of Surrey storm network (ADR 0006). Geometry-inferred
    topology (gravity mains); parcels (Lot) + buildings published."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_surrey_network(fetch_surrey_storm(tuple(a.bbox), client=surrey_client)),
        land_fn=lambda a: fetch_surrey_land(tuple(a.bbox), client=surrey_client),
        sub_crs="EPSG:32610", city="surrey",
        network_source="City of Surrey storm drainage (real municipal network)",
        subcatchment_method=subcatchment_method, report=report, **kwargs)


def build_from_kelowna(aoi, start: date, end: date, workspace, *, kelowna_client=None,
                       subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL City of Kelowna storm network (ADR 0006). Geometry-inferred
    topology (node inverts back-filled from pipe ends); parcels + buildings published."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_kelowna_network(fetch_kelowna_storm(tuple(a.bbox), client=kelowna_client)),
        land_fn=lambda a: fetch_kelowna_land(tuple(a.bbox), client=kelowna_client),
        sub_crs="EPSG:32611", city="kelowna",
        network_source="City of Kelowna storm sewer (real municipal network)",
        subcatchment_method=subcatchment_method, report=report, **kwargs)


def build_from_regina(aoi, start: date, end: date, workspace, *, regina_client=None,
                      subcatchment_method: str = "parcel", report=None, **kwargs) -> BuildResult:
    """Build a SWMM model from the REAL City of Regina storm network (ADR 0006). Geometry-inferred
    topology (active gravity lines; node inverts back-filled from pipe ends); parcels + building
    footprints published."""
    return _build_real_network(
        aoi, start, end, workspace,
        network_fn=lambda a: build_regina_network(fetch_regina_storm(tuple(a.bbox), client=regina_client)),
        land_fn=lambda a: fetch_regina_land(tuple(a.bbox), client=regina_client),
        sub_crs="EPSG:32613", city="regina",
        network_source="City of Regina storm sewer (real municipal network)",
        sanitary_fn=lambda a: build_regina_network(fetch_regina_sanitary(tuple(a.bbox), client=regina_client)),
        subcatchment_method=subcatchment_method, report=report, **kwargs)


# Cities with a real-network adapter, gated by a coarse coverage bbox
# (min_lon, min_lat, max_lon, max_lat). Order: first match wins; boxes must not overlap.
_REAL_NETWORK_CITIES = [
    ("Victoria, BC", (-123.43, 48.40, -123.33, 48.47), build_from_victoria),
    ("Ottawa, ON", (-76.05, 45.15, -75.40, 45.55), build_from_ottawa),
    ("London, ON", (-81.38, 42.86, -81.12, 43.06), build_from_london),
    ("Kitchener–Waterloo, ON", (-80.70, 43.30, -80.20, 43.60), build_from_kitchener),
    ("Calgary, AB", (-114.32, 50.84, -113.86, 51.21), build_from_calgary),
    ("Surrey, BC", (-123.00, 49.00, -122.69, 49.22), build_from_surrey),
    ("Kelowna, BC", (-119.60, 49.77, -119.28, 50.05), build_from_kelowna),
    ("Regina, SK", (-104.80, 50.35, -104.45, 50.55), build_from_regina),
]


def pipeline_for_aoi(aoi):
    """Pick the build pathway for an AOI: a real-municipal-network city adapter when the AOI
    centre falls inside a supported city's coverage, else synthesize a network from open data.
    Returns ``(build_fn, mode_label)``."""
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    clon, clat = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
    for name, (lo1, la1, lo2, la2), build in _REAL_NETWORK_CITIES:
        if lo1 <= clon <= lo2 and la1 <= clat <= la2:
            return build, f"Real municipal network — {name}"
    return build_from_aoi, "Synthesized from open data"


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
