# SWMMCanada

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" /></a>
  <img src="https://img.shields.io/badge/status-early%20development-orange" alt="Status: early development" />
  <img src="https://img.shields.io/badge/EPA%20SWMM-5.2-1F6FEB" alt="EPA SWMM 5.2" />
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/node-18%2B-339933" alt="Node 18+" />
</p>

> **Early development (WIP)** · generated models run clean in EPA SWMM 5.2.

**SWMMCanada — runnable EPA SWMM models from Canadian open data**<br>
*Draw or upload an area anywhere in Canada; SWMMCanada assembles open data and the municipal storm network into a complete, EPA-SWMM-valid `model.inp` plus a shareable, model-ready datastore.*

## Project Overview

SWMMCanada turns a map polygon — or an uploaded boundary — into a complete, EPA-SWMM-valid stormwater model for anywhere in Canada. It fetches Canadian open data (terrain, land cover, soil, and rainfall), derives the subcatchment parameters, builds the drainage network, and writes a `model.inp` together with a shareable, framework-independent datastore.

**The goal is not to replace EPA SWMM or the modeller, but to remove the data-gathering and assembly work that stands between a study area and a first runnable model.** Where a municipality publishes its storm network, SWMMCanada ingests the real pipes — real inverts, diameters, and topology; everywhere else it synthesizes a plausible network from the street graph and open data. Both paths produce the same kind of artifacts — an INP, map layers, and a provenance-tracked datastore — that a modeller can inspect, run, and refine.

These are baseline models meant as a starting point. The real-network builds reproduce the published pipe geometry and run clean in EPA SWMM 5.2, but parameters such as rainfall losses and surface roughness are first-pass estimates, not calibrated values.

Author: **Zhonghao Zhang**  
License: **MIT**

## Why this project exists

Building a SWMM model from scratch is rarely one step. A typical project means locating the municipal storm network, a DEM, land cover, soil, and rainfall; delineating subcatchments; assigning parameters; assembling the INP; and checking that it runs. For Canada those inputs exist as open data, but they live across different portals, formats, and coordinate systems.

SWMMCanada provides a single path from a drawn area to a runnable model: it knows where the Canadian open data lives, how to align it, and how to assemble it into an EPA-SWMM-valid model with explicit provenance.

## What makes it different

- **Two network modes, auto-selected by location.** Draw inside a city that publishes its storm network — currently Victoria and Ottawa — and SWMMCanada ingests the real pipes. Draw anywhere else in Canada and it synthesizes a network from the street graph and open data.
- **Subcatchments shaped by real parcels.** Where a city publishes parcel and building footprints, subcatchment cells follow real lot lines and impervious area is computed from real roofs and roads, rather than a generic grid. Cities without parcels fall back to a catch-basin tessellation.
- **Canadian open data, end to end.** Terrain (NRCan MRDEM), land cover (NALCMS), soil (ISRIC SoilGrids), rainfall (ECCC GeoMet), and municipal storm networks (city ArcGIS) — all free or under the Open Government Licence.
- **Runnable and self-checking.** Every model round-trips through swmm-api and swmmio before it is returned, and the generated `model.inp` runs in EPA SWMM 5.2 with zero errors.
- **A model-ready datastore, not just an INP.** Each build also writes a framework-independent hand-off: a GeoPackage (network and spatial layers), a netCDF/CF file (rainfall forcing), and a JSON file (configuration and provenance).
- **Web app and library.** Draw or upload a boundary in a React and MapLibre interface backed by a FastAPI service, or call the same pipeline directly from Python.

<p align="center">
  <img src="results/victoria_app.png" width="820" alt="The SWMMCanada web app after building downtown Victoria: real storm network and parcel-shaped subcatchments on the map, with the build mode and model layers in the side panel" />
</p>
<p align="center"><sub>The SWMMCanada web app after building a downtown Victoria area: the city's real storm network (pipes and junctions in blue, outfalls in red) and parcel-shaped subcatchments (green), with the auto-selected build mode, model-layer toggles, and download ready.</sub></p>

## Two ways to get a network

| Mode | How | Where | Fidelity |
|---|---|---|---|
| **Synthesize** | own street-graph + Voronoi synthesis from open data | anywhere in Canada | approximate |
| **Real municipal network** | ingest the city's published storm pipes (real inverts, diameters, topology) | cities that publish it | high |

Both paths then share the same downstream: derive parameters, fetch ECCC rainfall, build the `.inp`, and write the datastore. Supported real-network cities:

| City | Topology | Subcatchments | EPA SWMM result |
|---|---|---|---|
| Victoria, BC | explicit node IDs | catch-basin + real parcels | 0 errors, continuity −0.05% |
| Ottawa, ON | inferred from geometry | catch-basin tessellation | 0 errors, flow routing −5% |

Full validation, figures, and EPA SWMM continuity are in **[RESULTS.md](RESULTS.md)**.

## Quickstart

```bash
# backend (Python 3.11)
cd backend
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn swmmcanada.api.main:app --port 8000

# frontend (proxies /api -> :8000)
cd frontend && npm install && npm run dev
```

Build a model in code:

```python
from datetime import date
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_from_aoi, build_from_ottawa

aoi = aoi_from_geojson({"type": "Polygon", "coordinates": [...]})
build_from_aoi(aoi, date(2022, 6, 1), date(2022, 6, 7), "out/")         # synthesize anywhere
build_from_ottawa(aoi, date(2022, 6, 1), date(2022, 6, 7), "out_ott/")  # real Ottawa network
```

Adding a city is roughly one `sources/cities/<city>.py` (fetch and field mapping) plus a one-line `build_from_<city>` wrapper; the shared `cities/base.py` does the SWMM assembly.

## What a build produces

- `model.inp` — an EPA SWMM 5.2 model: junctions, conduits, outfalls, subcatchments, and a raingage.
- `datastore/` — the framework-independent hand-off: `network.gpkg` (GeoPackage), `forcing.nc` (netCDF/CF), and `datastore.json` (configuration and provenance).
- `preview/network.geojson` (map layers), DEM / land-cover / soil rasters, and `manifest.json`.

## Data sources

Canadian open data, free or under the Open Government Licence.

| Data | Source | Interface |
|---|---|---|
| Rainfall / temperature | ECCC GeoMet | `api.weather.gc.ca` (OGC, bbox) |
| DEM / elevation | NRCan MRDEM 30 m | AWS S3 COG (EPSG:3979) |
| Land cover -> imperviousness | NRCan NALCMS 2020 | geo.ca STAC COG |
| Soil -> HSG / curve number | ISRIC SoilGrids | WCS (auth-free) |
| Observed streamflow | ECCC HYDAT | SQLite |
| Municipal storm networks | City ArcGIS REST | per-city adapter (`sources/cities/`) |

## Project structure

```
backend/swmmcanada/
  geo/         AOI parsing, station selection, CRS
  acquire/     ECCC climate · NRCan DEM · NALCMS land cover · SoilGrids soil · HYDAT flow
  network/     own drainage-network synthesis + Voronoi subcatchments (open-data mode)
  derive/      clip + zonal stats -> subcatchment parameters
  build/       assemble + validate the SWMM .inp
  datastore/   model-ready datastore (GeoPackage + netCDF + JSON)
  sources/     live data-source adapters
    cities/    base.py (shared assembler) + victoria.py + ottawa.py  (real-network cities)
  api/         FastAPI async tasks API
  pipeline.py  build_from_aoi · build_from_victoria · build_from_ottawa
frontend/      React 19 + Vite + MapLibre + Tailwind + Zustand
```

## Requirements

- **Backend** — Python 3.11+; geopandas, shapely, pyproj, rasterio, networkx, swmm-api, swmmio, xarray, netcdf4, fastapi (full list in [`backend/pyproject.toml`](backend/pyproject.toml)).
- **Frontend** — Node 18+; React 19, Vite, MapLibre GL, Zustand, Tailwind (full list in [`frontend/package.json`](frontend/package.json)).
- A working **EPA SWMM 5** engine is only needed to run the generated `.inp`, not to build it.

## License

[MIT](LICENSE) © 2026 Zhonghao Zhang
