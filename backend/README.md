# SWMMCanada — Backend

GUI-free Python service: **AOI → Canadian open data → a complete, runnable SWMM `.inp`**.
Exposed over an async tasks API (FastAPI) and importable as a library.

## Layout

```
swmmcanada/
  geo/         AOI parsing (GeoJSON + shapefile) + station selection
  acquire/     ECCC climate · NRCan DEM · NALCMS land cover · SoilGrids soil · HYDAT flow
  network/     own drainage-network synthesis + Voronoi subcatchments
  derive/      clip + zonal stats → subcatchment parameters
  build/       assemble + validate the SWMM .inp
  datastore/   model-ready datastore (GeoPackage + netCDF + JSON)
  sources/     live data-source adapters (+ cities/ for real municipal networks)
  api/         FastAPI async tasks API
  pipeline.py  build_from_aoi · build_from_victoria · build_from_ottawa
```

## Dev

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                   # offline tests (network mocked)
.venv/bin/uvicorn swmmcanada.api.main:app --port 8000
```

Requires **Python 3.11+**. Key dependencies: geopandas, shapely, pyproj, rasterio, networkx,
swmm-api, swmmio, xarray, netcdf4, fastapi (full list in `pyproject.toml`).
