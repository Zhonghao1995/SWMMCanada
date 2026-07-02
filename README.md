<p align="center">
  <img src="assets/logo-lockup.png" width="520" alt="SWMM Canada: anywhere in Canada, draw and run" />
</p>

<p align="center">
  <a href="https://swmm.h2ox.me/"><img src="https://img.shields.io/badge/demo-live-1F6FEB" alt="Live demo at swmm.h2ox.me" /></a>
  <a href="https://github.com/Zhonghao1995/SWMMCanada/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Zhonghao1995/SWMMCanada/ci.yml?branch=main&label=CI" alt="CI" /></a>
  <a href="https://codecov.io/gh/Zhonghao1995/SWMMCanada"><img src="https://codecov.io/gh/Zhonghao1995/SWMMCanada/branch/main/graph/badge.svg" alt="codecov" /></a>
  <a href="https://github.com/Zhonghao1995/SWMMCanada/releases/latest"><img src="https://img.shields.io/github/v/release/Zhonghao1995/SWMMCanada?label=rel&color=1F6FEB" alt="latest release" /></a>
  <a href="https://doi.org/10.5281/zenodo.21058544"><img src="https://img.shields.io/badge/DOI-Zenodo-1F6FEB" alt="DOI 10.5281/zenodo.21058544" /></a>
  <a href="https://github.com/Zhonghao1995/SWMMCanada/pkgs/container/swmmcanada"><img src="https://img.shields.io/badge/ghcr.io-image-2496ED?logo=docker&logoColor=white" alt="Container image on GHCR" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" /></a>
  <a href="https://zhonghaoz.ca"><img src="https://img.shields.io/badge/built%20by-Zhonghao-FF7139" alt="Built by Zhonghao" /></a>
</p>

<p align="center"><em>🚧 Under construction. This repository is the data-preprocessing and upstream model-building module for <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow"><strong>Agentic SWMM</strong></a> and <strong>Agentic MIKE+</strong>.</em></p>

**Draw an area anywhere in Canada and get a ready-to-run EPA SWMM stormwater model.**

You draw (or upload) a boundary on the map. SWMMCanada pulls the Canadian open data for that spot (rainfall, terrain, land cover, soil, and the city's storm pipes) and assembles a complete `model.inp` you can open and run in EPA SWMM. No hunting across data portals, no manual setup.

> [!TIP]
> **🌐 Try it now. No install, no deployment.** A hosted **beta** is live at **[swmm.h2ox.me](https://swmm.h2ox.me/)**. Draw an area and build a SWMM model right in your browser.
>
> The demo runs on a small server (**~2 GB RAM**), so it works best for **small areas**; large regions can run out of memory and fail. For large-scale modeling, self-host the frontend **and** backend on a bigger machine or an **HPC** cluster. Both run well as shipped in this repo (see **[DEPLOY.md](DEPLOY.md)**).

<p align="center">
  <img src="results/victoria_app.png" width="820" alt="The SWMMCanada web app after building downtown Victoria: real storm network and parcel-shaped subcatchments on the map, with the build mode and model layers in the side panel" />
</p>
<p align="center"><sub>The app after building a downtown Victoria area: real storm network (blue), outfalls (red), and parcel-shaped subcatchments (green), with the model ready to download.</sub></p>

## Two modes (picked automatically)

SWMMCanada chooses how to build the network from **where you draw**. You don't set anything:

| Mode | What it does | Where it kicks in |
|---|---|---|
| **Real network** | uses the city's published storm pipes (real inverts, diameters, manholes, and outfalls) | **7 cities** that publish a storm network: Victoria, Ottawa, Calgary, Surrey, London, Kitchener–Waterloo, Kelowna |
| **Synthesize** | builds a realistic network from the street map + open data | anywhere else in Canada |

Either mode then gives you the same things: subcatchments, rainfall, and a shareable data package. Where a city also publishes parcels (like Victoria), the subcatchments follow real lot lines.

## Try it

**Easiest: the hosted beta (nothing to install).** Open **[swmm.h2ox.me](https://swmm.h2ox.me/)**, draw a *small* area, pick dates, and click **Build SWMM model**. Keep the area small (the demo server has ~2 GB RAM); for anything large, self-host as below.

**Quickest local: pull the prebuilt backend image** ([all tags](https://github.com/Zhonghao1995/SWMMCanada/pkgs/container/swmmcanada)):

```bash
docker run --rm -p 8000:8000 ghcr.io/zhonghao1995/swmmcanada:latest
# API on http://localhost:8000  ·  health: /api/v1/healthz
```

**From source** (for development):

```bash
# backend (Python 3.11)
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn swmmcanada.api.main:app --port 8000

# frontend (another terminal)
cd frontend && npm install && npm run dev
```

Running the whole thing in production (GHCR image + GitHub Pages) is documented in **[DEPLOY.md](DEPLOY.md)**.

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
  export/      model exporters reading the datastore: SWMM · MIKE+ CS import package · ICM (scaffold)
  api/         FastAPI async tasks API
  pipeline.py  build_from_aoi · build_from_<city> (7 real-network cities)

frontend/src/            # React + Vite + MapLibre web app
  components/   MapPanel.tsx (map + draw AOI) · ControlPanel.tsx (build, layers, download)
  lib/api.ts    backend client (submit, poll, preview, download)
  store.ts      Zustand state · types.ts shared types
```

## What you get

Every build ships one result package:

- **`model.inp`** — runs in EPA SWMM 5.2 (plus `manifest.json`);
- **`datastore/`** — the shareable model-ready datastore (GeoPackage network + netCDF/CF forcing + JSON provenance) that every export target reads from;
- **`mikeplus/`** — a **DHI MIKE+ Collection System import package**: `nodes` / `links` / `catchments` shapefiles + `rain.csv` + a field-mapping sheet, with import steps and every approximation documented inside (`README.md` / `field_mapping.md`);
- **`validation.json`** — the model's health report: structural checks, the delineation method used, and its confidence;
- **`preview/`** — map layers for the web UI.

More export targets plug into the same interface (InfoWorks ICM is scaffolded).

## Run, calibrate & quantify uncertainty with Agentic SWMM

SWMMCanada is the **first half** of a closed loop: it turns an area into a complete, runnable model. The **second half**, **[Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow)**, takes that model the rest of the way: it runs EPA SWMM for you, calibrates against observations, post-processes, and does uncertainty analysis. Hand a SWMMCanada package to Agentic SWMM and the loop closes: **open data → model → calibrated results with uncertainty.**

> [!WARNING]
> **Models are not calibrated.** SWMMCanada gets you a complete, runnable model fast, but the parameters (rainfall losses, roughness, curve numbers) are first-pass estimates. Calibrate against observations before using any results for design or decisions, which is exactly what **[Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow)** automates. Tired of tweaking parameters and plotting results by hand? Try Agentic SWMM: it does all the downstream work through plain natural-language chat (https://aiswmm.com/demo/), and every step stays auditable and transparent (https://doi.org/10.3390/aieng1010005).

## More

- **[ASSUMPTIONS.md](ASSUMPTIONS.md)**: what's real, derived, or approximated in a model, layer by layer. Most of it is grounded in real data; the approximations (and the uncalibrated caveat) are called out.
- **[DATA.md](DATA.md)**: every dataset used, with links, licences, and how each one is used (ECCC rainfall, NRCan terrain & land cover, SoilGrids soil, OpenStreetMap, and the seven municipal storm networks). All free / open.
- **[RESULTS.md](RESULTS.md)**: real-city validation, figures, and the EPA SWMM numbers.
- **[DEPLOY.md](DEPLOY.md)**: run the backend as a container (GHCR image) and the frontend as a static site (GitHub Pages), and how the two are wired.
- **Built with** Python (geopandas, swmm-api, FastAPI) and React + MapLibre. Full dependency lists in `backend/pyproject.toml` and `frontend/package.json`.

## Citation

If you use SWMMCanada in your work, please cite it (APA):

> Zhang, Z. (2026). *SWMMCanada: ready-to-run EPA SWMM models anywhere in Canada from open data* (Version 0.1.1) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.21058544

BibTeX and other formats are available via **Cite this repository** in the GitHub sidebar (generated from [`CITATION.cff`](CITATION.cff)). The DOI above is the *concept DOI* — it always resolves to the latest version.

## License

MIT © 2026 Zhonghao Zhang
