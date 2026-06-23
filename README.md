# SWMMCanada

<p align="center">
  <a href="https://github.com/Zhonghao1995/SWMMCanada/actions/workflows/ci.yml"><img src="https://github.com/Zhonghao1995/SWMMCanada/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://github.com/Zhonghao1995/SWMMCanada/releases/latest"><img src="https://img.shields.io/github/v/release/Zhonghao1995/SWMMCanada?label=release&color=1F6FEB" alt="latest release" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" /></a>
  <img src="https://img.shields.io/badge/status-early%20development-orange" alt="Status: early development" />
  <img src="https://img.shields.io/badge/EPA%20SWMM-5.2-1F6FEB" alt="EPA SWMM 5.2" />
</p>

<p align="center"><em>🚧 Under construction — this repository serves as the data-preprocessing and upstream model-building module for <strong>Agentic SWMM</strong> and <strong>Agentic MIKE+</strong>.</em></p>

**Draw an area anywhere in Canada and get a ready-to-run EPA SWMM stormwater model.**

You draw (or upload) a boundary on the map. SWMMCanada pulls the Canadian open data for that spot — rainfall, terrain, land cover, soil, and the city's storm pipes — and assembles a complete `model.inp` you can open and run in EPA SWMM. No hunting across data portals, no manual setup.

> [!WARNING]
> **Models are not calibrated.** SWMMCanada gets you a complete, runnable model fast — but the parameters (rainfall losses, roughness, curve numbers) are first-pass estimates. Calibrate against observations before using any results for design or decisions.

<p align="center">
  <img src="results/victoria_app.png" width="820" alt="The SWMMCanada web app after building downtown Victoria: real storm network and parcel-shaped subcatchments on the map, with the build mode and model layers in the side panel" />
</p>
<p align="center"><sub>The app after building a downtown Victoria area: real storm network (blue), outfalls (red), and parcel-shaped subcatchments (green), with the model ready to download.</sub></p>

## Two modes (picked automatically)

SWMMCanada chooses how to build the network from **where you draw** — you don't set anything:

| Mode | What it does | Where it kicks in |
|---|---|---|
| **Real network** | uses the city's published storm pipes — real inverts, diameters, manholes, and outfalls | **7 cities** that publish a storm network: Victoria, Ottawa, Calgary, Surrey, London, Kitchener–Waterloo, Kelowna |
| **Synthesize** | builds a realistic network from the street map + open data | anywhere else in Canada |

Either mode then gives you the same things: subcatchments, rainfall, and a shareable data package. Where a city also publishes parcels (like Victoria), the subcatchments follow real lot lines.

## Try it

```bash
# backend (Python 3.11)
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn swmmcanada.api.main:app --port 8000

# frontend (another terminal)
cd frontend && npm install && npm run dev
```

Open the app, draw a box over Victoria, pick dates, and click **Build SWMM model**. Or from Python:

```python
from datetime import date
from swmmcanada.geo import aoi_from_geojson
from swmmcanada.pipeline import build_from_aoi

aoi = aoi_from_geojson({"type": "Polygon", "coordinates": [...]})
build_from_aoi(aoi, date(2022, 6, 1), date(2022, 6, 7), "out/")
```

## Project structure

```
backend/swmmcanada/      # Python pipeline: open data -> SWMM model
  geo/         AOI parsing, station selection, CRS
  acquire/     ECCC climate · NRCan DEM · NALCMS land cover · SoilGrids soil · HYDAT flow
  sources/     live data adapters (climate, DEM, land cover, soil, OSM streets)
    cities/    base.py (shared assembler) + 7 real-network adapters (victoria · ottawa · calgary · surrey · london · kitchener · kelowna)
  network/     street-graph synthesis + Voronoi subcatchments (synthesize mode)
  derive/      clip + zonal stats -> subcatchment parameters
  build/       assemble + validate the SWMM .inp
  datastore/   model-ready datastore (GeoPackage + netCDF + JSON)
  api/         FastAPI async tasks API
  pipeline.py  build_from_aoi · build_from_<city> (7 real-network cities)

frontend/src/            # React + Vite + MapLibre web app
  components/   MapPanel.tsx (map + draw AOI) · ControlPanel.tsx (build, layers, download)
  lib/api.ts    backend client (submit, poll, preview, download)
  store.ts      Zustand state · types.ts shared types
```

## What you get

A `model.inp` that runs in EPA SWMM 5.2, a `datastore/` package you can share (GeoPackage + netCDF + JSON), and map layers.

## More

- **[ASSUMPTIONS.md](ASSUMPTIONS.md)** — what's real, derived, or approximated in a model, layer by layer. Most of it is grounded in real data; the approximations (and the uncalibrated caveat) are called out.
- **[DATA.md](DATA.md)** — every dataset used, with links, licences, and how each one is used (ECCC rainfall, NRCan terrain & land cover, SoilGrids soil, OpenStreetMap, and the seven municipal storm networks). All free / open.
- **[RESULTS.md](RESULTS.md)** — real-city validation, figures, and the EPA SWMM numbers.
- **Built with** Python (geopandas, swmm-api, FastAPI) and React + MapLibre. Full dependency lists in `backend/pyproject.toml` and `frontend/package.json`.

## License

MIT © 2026 Zhonghao Zhang
