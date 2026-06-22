"""Model-ready datastore: the standardized intermediate layer between data-acquisition
and model-build (spec 11 / ADR 0003).

Each data source converts to ONE on-disk standard and the model builder reads from it, so
adapter work is additive (N+M) instead of pairwise (N×M). The datastore is multi-carrier:

  * ``network.gpkg``   — GeoPackage (EPSG:4326): junctions/outfalls/conduits/subcatchments.
  * ``forcing.nc``     — netCDF / CF-1.8: the rainfall forcing timeseries.
  * ``datastore.json`` — config + provenance + carrier file list (the citable header).

The round-trip guarantee: ``read_datastore(write_datastore(...))`` reconstructs the exact
input dataclasses (floats may differ only by float64 precision; ``polygon=None`` and the
name ordering of junctions/conduits/subcatchments are preserved). ``build_from_datastore``
then proves the datastore is *sufficient* to build a model — it reads the datastore back
and feeds it straight into ``build_model``.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import geopandas as gpd
import pandas as pd
import xarray as xr
from shapely.geometry import LineString, Point, Polygon

from swmmcanada.build import (
    BuildConfig,
    BuildResult,
    ConduitIn,
    FlowUnits,
    InfiltrationModel,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
    build_model,
)
from swmmcanada.datastore import schema


@dataclass
class ModelReadyDatastore:
    """In-memory view of a datastore directory, reconstructed by :func:`read_datastore`."""

    network: NetworkIn
    subcatchments: List[SubcatchmentIn]
    rain: RainfallSeries
    config: dict
    provenance: dict


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
def write_datastore(
    out_dir,
    *,
    network: NetworkIn,
    subcatchments: List[SubcatchmentIn],
    rain: RainfallSeries,
    config: BuildConfig,
    provenance: Optional[dict] = None,
) -> Path:
    """Write the three carrier files into ``out_dir`` and return ``out_dir``.

    ``config.out_dir`` is deliberately NOT persisted: it is a runtime build target, not
    part of the shareable/citable artifact.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    _write_network_gpkg(out / schema.NETWORK_GPKG, network, subcatchments)
    _write_forcing_nc(out / schema.FORCING_NC, rain)
    _write_datastore_json(out / schema.DATASTORE_JSON, config, provenance or {})

    return out


def _node_coords(network: NetworkIn) -> dict:
    """name → (x, y) over junctions + outfalls (the conduit endpoint lookup; see preview)."""
    coords = {}
    for n in list(network.junctions) + list(network.outfalls):
        coords[n.name] = (float(n.x), float(n.y))
    return coords


def _write_network_gpkg(
    path: Path, network: NetworkIn, subcatchments: List[SubcatchmentIn]
) -> None:
    coords = _node_coords(network)

    junctions = gpd.GeoDataFrame(
        {
            "name": [j.name for j in network.junctions],
            "invert_m": [float(j.invert_m) for j in network.junctions],
            "max_depth_m": [float(j.max_depth_m) for j in network.junctions],
        },
        geometry=[Point(float(j.x), float(j.y)) for j in network.junctions],
        crs=schema.CRS,
    )

    outfalls = gpd.GeoDataFrame(
        {
            "name": [o.name for o in network.outfalls],
            "invert_m": [float(o.invert_m) for o in network.outfalls],
            "kind": [o.kind for o in network.outfalls],
        },
        geometry=[Point(float(o.x), float(o.y)) for o in network.outfalls],
        crs=schema.CRS,
    )

    conduit_geoms = []
    for c in network.conduits:
        if c.from_node in coords and c.to_node in coords:
            conduit_geoms.append(LineString([coords[c.from_node], coords[c.to_node]]))
        else:  # endpoint missing — keep the row so attrs round-trip, with null geometry
            conduit_geoms.append(None)
    conduits = gpd.GeoDataFrame(
        {
            "name": [c.name for c in network.conduits],
            "from_node": [c.from_node for c in network.conduits],
            "to_node": [c.to_node for c in network.conduits],
            "length_m": [float(c.length_m) for c in network.conduits],
            "diameter_m": [float(c.diameter_m) for c in network.conduits],
            "roughness_n": [float(c.roughness_n) for c in network.conduits],
        },
        geometry=conduit_geoms,
        crs=schema.CRS,
    )

    sub_geoms = []
    for s in subcatchments:
        if s.polygon:
            sub_geoms.append(Polygon([(float(x), float(y)) for x, y in s.polygon]))
        else:  # None → null geometry, so it round-trips back to polygon=None
            sub_geoms.append(None)
    subs = gpd.GeoDataFrame(
        {f: [getattr(s, f) for s in subcatchments] for f in schema.SUBCATCHMENT_FIELDS},
        geometry=sub_geoms,
        crs=schema.CRS,
    )

    # First layer writes the file; subsequent layers append into the same GeoPackage.
    junctions.to_file(path, layer=schema.LAYER_JUNCTIONS, driver="GPKG")
    outfalls.to_file(path, layer=schema.LAYER_OUTFALLS, driver="GPKG")
    conduits.to_file(path, layer=schema.LAYER_CONDUITS, driver="GPKG")
    subs.to_file(path, layer=schema.LAYER_SUBCATCHMENTS, driver="GPKG")


def _write_forcing_nc(path: Path, rain: RainfallSeries) -> None:
    ds = xr.Dataset(
        {schema.PRECIP_VAR: (schema.TIME_DIM, [float(p) for p in rain.precip_mm])},
        coords={schema.TIME_DIM: pd.to_datetime(list(rain.timestamps))},
    )
    ds[schema.PRECIP_VAR].attrs["units"] = schema.PRECIP_UNITS
    ds[schema.PRECIP_VAR].attrs["long_name"] = "precipitation depth per interval"
    ds[schema.PRECIP_VAR].attrs["gage_name"] = rain.gage_name
    ds[schema.PRECIP_VAR].attrs["ts_name"] = rain.ts_name
    ds.attrs["Conventions"] = schema.CF_CONVENTIONS
    ds.to_netcdf(path)
    ds.close()


def _write_datastore_json(path: Path, config: BuildConfig, provenance: dict) -> None:
    import json

    meta = {
        "datastore_version": schema.DATASTORE_VERSION,
        "config": {
            "title": config.title,
            "start": config.start.isoformat(),
            "end": config.end.isoformat(),
            "flow_units": config.flow_units.value,
            "infiltration": config.infiltration.value,
            "routing_model": config.routing_model,
            "rain_interval_s": int(config.rain_interval.total_seconds()),
            "rain_format": config.rain_format,
        },
        "provenance": provenance,
        "files": list(schema.DATA_FILES),
    }
    path.write_text(json.dumps(meta, indent=2, sort_keys=False))


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
def read_datastore(path) -> ModelReadyDatastore:
    """Reconstruct the input dataclasses from a datastore directory."""
    base = Path(path)
    network = _read_network(base / schema.NETWORK_GPKG)
    subcatchments = _read_subcatchments(base / schema.NETWORK_GPKG)
    rain = _read_forcing(base / schema.FORCING_NC)
    config, provenance = _read_datastore_json(base / schema.DATASTORE_JSON)
    return ModelReadyDatastore(
        network=network,
        subcatchments=subcatchments,
        rain=rain,
        config=config,
        provenance=provenance,
    )


def _read_network(gpkg: Path) -> NetworkIn:
    jdf = gpd.read_file(gpkg, layer=schema.LAYER_JUNCTIONS)
    junctions = [
        JunctionIn(
            name=str(r["name"]),
            invert_m=float(r["invert_m"]),
            x=float(geom.x),
            y=float(geom.y),
            max_depth_m=float(r["max_depth_m"]),
        )
        for geom, r in zip(jdf.geometry, jdf.to_dict("records"))
    ]

    odf = gpd.read_file(gpkg, layer=schema.LAYER_OUTFALLS)
    outfalls = [
        OutfallIn(
            name=str(r["name"]),
            invert_m=float(r["invert_m"]),
            x=float(geom.x),
            y=float(geom.y),
            kind=str(r["kind"]),
        )
        for geom, r in zip(odf.geometry, odf.to_dict("records"))
    ]

    cdf = gpd.read_file(gpkg, layer=schema.LAYER_CONDUITS)
    conduits = [
        ConduitIn(
            name=str(r["name"]),
            from_node=str(r["from_node"]),
            to_node=str(r["to_node"]),
            length_m=float(r["length_m"]),
            diameter_m=float(r["diameter_m"]),
            roughness_n=float(r["roughness_n"]),
        )
        for r in cdf.to_dict("records")
    ]

    return NetworkIn(junctions=junctions, outfalls=outfalls, conduits=conduits)


def _read_subcatchments(gpkg: Path) -> List[SubcatchmentIn]:
    sdf = gpd.read_file(gpkg, layer=schema.LAYER_SUBCATCHMENTS)
    subs = []
    for geom, r in zip(sdf.geometry, sdf.to_dict("records")):
        polygon = _polygon_from_geometry(geom)
        subs.append(
            SubcatchmentIn(
                name=str(r["name"]),
                outlet_node=str(r["outlet_node"]),
                area_ha=float(r["area_ha"]),
                pct_imperv=float(r["pct_imperv"]),
                width_m=float(r["width_m"]),
                pct_slope=float(r["pct_slope"]),
                cn=float(r["cn"]),
                n_imperv=float(r["n_imperv"]),
                n_perv=float(r["n_perv"]),
                s_imperv_mm=float(r["s_imperv_mm"]),
                s_perv_mm=float(r["s_perv_mm"]),
                pct_zero=float(r["pct_zero"]),
                polygon=polygon,
            )
        )
    return subs


def _polygon_from_geometry(geom) -> Optional[List[tuple]]:
    """Polygon exterior → list of (x, y); null/empty geometry → None.

    The stored ring is the polygon's exterior; we drop the closing vertex that shapely
    repeats so the result matches the original (unclosed) input ring.
    """
    if geom is None or getattr(geom, "is_empty", False):
        return None
    coords = list(geom.exterior.coords)
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return [(float(x), float(y)) for x, y in coords]


def _read_forcing(nc: Path) -> RainfallSeries:
    ds = xr.open_dataset(nc)
    try:
        times = pd.to_datetime(ds[schema.TIME_DIM].values)
        timestamps = [pd.Timestamp(t).to_pydatetime() for t in times]
        precip = [float(v) for v in ds[schema.PRECIP_VAR].values]
        attrs = ds[schema.PRECIP_VAR].attrs
        gage_name = str(attrs.get("gage_name", "RG1"))
        ts_name = str(attrs.get("ts_name", "rain"))
    finally:
        ds.close()
    return RainfallSeries(
        timestamps=timestamps, precip_mm=precip, gage_name=gage_name, ts_name=ts_name
    )


def _read_datastore_json(path: Path):
    import json

    meta = json.loads(path.read_text())
    return meta.get("config", {}), meta.get("provenance", {})


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build_from_datastore(datastore_dir, out_dir) -> BuildResult:
    """Read a datastore and build a SWMM model from it — proves the datastore is a
    sufficient hand-off artifact. ``out_dir`` is the build target (the datastore's stored
    config carries everything except the runtime out_dir)."""
    ds = read_datastore(datastore_dir)
    config = _build_config_from_dict(ds.config, out_dir)
    return build_model(
        network=ds.network,
        subcatchments=ds.subcatchments,
        rain=ds.rain,
        config=config,
    )


def _build_config_from_dict(cfg: dict, out_dir) -> BuildConfig:
    return BuildConfig(
        out_dir=out_dir,
        start=date.fromisoformat(cfg["start"]),
        end=date.fromisoformat(cfg["end"]),
        title=cfg.get("title", "SWMMCanada model"),
        flow_units=FlowUnits(cfg.get("flow_units", FlowUnits.CMS.value)),
        infiltration=InfiltrationModel(
            cfg.get("infiltration", InfiltrationModel.CURVE_NUMBER.value)
        ),
        routing_model=cfg.get("routing_model", "DYNWAVE"),
        rain_interval=timedelta(seconds=int(cfg.get("rain_interval_s", 3600))),
        rain_format=cfg.get("rain_format", "VOLUME"),
    )
