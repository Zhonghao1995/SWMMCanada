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

from swmmcanada.acquire.climate import fetch_climate, to_rainfall_series
from swmmcanada.acquire.dem import acquire_dem
from swmmcanada.acquire.landcover import acquire_landcover
from swmmcanada.acquire.soil import acquire_soil
from swmmcanada.build import BuildConfig, BuildResult, build_model
from swmmcanada.datastore import write_datastore
from swmmcanada.derive.core import derive_parameters
from swmmcanada.network import synthesise_network
from swmmcanada.network.synth import NetworkConfig, _build_subcatchments
from swmmcanada.preview import network_geojson
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


def _utm_crs_for(aoi) -> str:
    """The UTM zone CRS (metres, northern hemisphere) covering the AOI — used for the .inp's
    display coordinates so SWMM/PCSWMM render the model undistorted rather than as lon/lat."""
    min_lon, _, max_lon, _ = aoi.bbox
    zone = int(((min_lon + max_lon) / 2 + 180) / 6) + 1
    return f"EPSG:{32600 + zone}"


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

    dem_source = dem_source or NRCanDemSource()
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
    subcatchments = synth.subcatchments

    if derive:
        _r("LANDCOVER_SOIL", 62)
        landcover = acquire_landcover(tuple(aoi.bbox), ws, source=landcover_source or NRCanLandcoverSource())
        soil = _acquire_soil_auto(tuple(aoi.bbox), ws, soil_source)
        _r("DERIVE", 70)
        subcatchments = derive_parameters(subcatchments, dem.path, landcover, soil)

    _r("CLIMATE", 75)
    climate = fetch_climate(aoi, start, end, client=climate_client, near_buffer_deg=climate_buffer_deg)
    series = next((s for s in climate.series if not s.frame.empty), None)
    if series is None:
        raise RuntimeError("No climate data available for this AOI/period.")
    rain = to_rainfall_series(series)

    _r("BUILDING", 90)
    config = BuildConfig(out_dir=ws, start=start, end=end, coordinate_crs=_utm_crs_for(aoi))
    result = build_model(
        network=synth.network,
        subcatchments=subcatchments,
        rain=rain,
        config=config,
        observed=None,
    )

    # Map preview: GeoJSON of the model geometry for the frontend's layers.
    preview_dir = ws / "preview"
    preview_dir.mkdir(exist_ok=True)
    (preview_dir / "network.geojson").write_text(
        json.dumps(network_geojson(synth.network, subcatchments))
    )

    # Model-ready datastore: the framework-independent, citable hand-off artifact
    # (GeoPackage network + netCDF forcing + JSON config/provenance). Additive — it ships
    # alongside model.inp inside the result package (ADR 0003 / spec 11).
    write_datastore(
        ws / "datastore",
        network=synth.network,
        subcatchments=subcatchments,
        rain=rain,
        config=config,
        provenance={
            "aoi_bbox": list(aoi.bbox),
            "crs": "EPSG:4326",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "sources": {
                "dem": type(dem_source).__name__,
                "climate": type(climate_client).__name__,
                "streets": "OSM",
            },
        },
    )

    _r("DONE", 100)
    return result


def _build_real_network(
    aoi, start: date, end: date, workspace, *,
    network_fn, land_fn, sub_crs: str, city: str, network_source: str,
    dem_source=None, climate_client=None, climate_buffer_deg: float = 0.3, derive: bool = True,
    landcover_source=None, soil_source=None, subcatchment_method: str = "parcel", report=None,
) -> BuildResult:
    """Shared real-municipal-network pipeline (ADR 0006). ``network_fn(aoi)`` assembles the
    city's real pipes (returns an object with ``.network`` + ``.diagnostics``); ``land_fn(aoi)``
    supplies ``{catchbasins, parcels, buildings}``. Everything else — subcatchments
    (catch-basin + parcel/building, Voronoi-of-nodes fallback), derive, climate, build,
    datastore — is city-agnostic. ``sub_crs`` is the city's metric CRS."""
    def _r(stage: str, pct: int):
        if report:
            report(stage, pct)

    dem_source = dem_source or NRCanDemSource()
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
    if not subcatchments:  # no catch-basin data -> Voronoi around the network nodes
        junction_xy = {j.name: (j.x, j.y) for j in network.junctions}
        subcatchments = _build_subcatchments(junction_xy, aoi, NetworkConfig())
        imperv_map = {}
        sub_diag = {"method": "voronoi-of-nodes", "n_subcatchments": len(subcatchments)}

    if derive:
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

    _r("CLIMATE", 80)
    climate = fetch_climate(aoi, start, end, client=climate_client, near_buffer_deg=climate_buffer_deg)
    series = next((s for s in climate.series if not s.frame.empty), None)
    if series is None:
        raise RuntimeError("No climate data available for this AOI/period.")
    rain = to_rainfall_series(series)

    _r("BUILDING", 90)
    config = BuildConfig(out_dir=ws, start=start, end=end, title=f"SWMMCanada ({city} real network)",
                         coordinate_crs=sub_crs)
    result = build_model(network=network, subcatchments=subcatchments, rain=rain, config=config, observed=None)

    preview_dir = ws / "preview"
    preview_dir.mkdir(exist_ok=True)
    (preview_dir / "network.geojson").write_text(json.dumps(network_geojson(network, subcatchments)))

    write_datastore(
        ws / "datastore", network=network, subcatchments=subcatchments, rain=rain, config=config,
        provenance={
            "aoi_bbox": list(aoi.bbox), "crs": "EPSG:4326", "city": city,
            "network_source": network_source, "network_diagnostics": netres.diagnostics,
            "subcatchment_diagnostics": sub_diag,
            "start": start.isoformat(), "end": end.isoformat(),
        },
    )
    _r("DONE", 100)
    return result


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
