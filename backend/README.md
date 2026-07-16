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
  pipeline.py  build_from_aoi · build_city (cities wired in sources/cities/registry.py)
```

## Dev

The `.venv` is git-ignored and **per-machine** — create one on *each* computer you work
from (Mac, Windows, …); it does not sync via git. Use Python **3.11** explicitly: a bare
`python3` may be too old (e.g. macOS system Python is 3.9, below the 3.11 minimum).

**macOS / Linux**

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                   # offline tests (network mocked)
.venv/bin/uvicorn swmmcanada.api.main:app --port 8000
```

**Windows** (PowerShell / cmd — note `Scripts\` instead of `bin/`)

```bat
py -3.11 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest
.venv\Scripts\uvicorn swmmcanada.api.main:app --port 8000
```

Requires **Python 3.11+** (the locked scientific stack — pandas 3 / SciPy 1.17 / xarray 2026.x — does not resolve on 3.10; CI, Docker and the project venvs all run 3.11). Key dependencies: geopandas,
shapely, pyproj, rasterio, networkx, swmm-api, swmmio, xarray, netcdf4, fastapi (full list
in `pyproject.toml`).
