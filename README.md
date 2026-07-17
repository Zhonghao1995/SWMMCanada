<p align="center">
  <img src="assets/logo-lockup.png" width="520" alt="SWMM Canada: anywhere in Canada, draw and run" />
</p>

<p align="center">
  <a href="https://www.h2ox.me/"><img src="https://img.shields.io/badge/website-h2ox.me-1F6FEB" alt="Project website at www.h2ox.me" /></a>
  <a href="https://swmm.h2ox.me/"><img src="https://img.shields.io/badge/demo-live-1F6FEB" alt="Live demo at swmm.h2ox.me" /></a>
  <a href="https://github.com/Zhonghao1995/SWMMCanada/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Zhonghao1995/SWMMCanada/ci.yml?branch=main&label=CI" alt="CI" /></a>
  <a href="https://codecov.io/gh/Zhonghao1995/SWMMCanada"><img src="https://codecov.io/gh/Zhonghao1995/SWMMCanada/branch/main/graph/badge.svg" alt="codecov" /></a>
  <a href="https://github.com/Zhonghao1995/SWMMCanada/releases/latest"><img src="https://img.shields.io/github/v/release/Zhonghao1995/SWMMCanada?label=rel&color=1F6FEB" alt="latest release" /></a>
  <a href="https://doi.org/10.5281/zenodo.21058544"><img src="https://img.shields.io/badge/DOI-Zenodo-1F6FEB" alt="DOI 10.5281/zenodo.21058544" /></a>
  <a href="https://doi.org/10.31223/X5NR31"><img src="https://img.shields.io/badge/preprint-EarthArXiv-4c9a2a" alt="Preprint on EarthArXiv (DOI 10.31223/X5NR31)" /></a>
  <a href="https://github.com/Zhonghao1995/SWMMCanada/pkgs/container/swmmcanada"><img src="https://img.shields.io/badge/ghcr.io-image-2496ED?logo=docker&logoColor=white" alt="Container image on GHCR" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" /></a>
  <a href="https://zhonghaoz.ca"><img src="https://img.shields.io/badge/built%20by-Zhonghao-FF7139" alt="Built by Zhonghao" /></a>
</p>

<p align="center"><em>🚧 Under construction. This repository is the data-preprocessing and upstream model-building module for <a href="https://github.com/Zhonghao1995/agentic-swmm-workflow"><strong>Agentic SWMM</strong></a> and <strong>Agentic MIKE+</strong>.</em></p>

**Draw an area anywhere in Canada and get a ready-to-run EPA SWMM stormwater model.**

You draw (or upload) a boundary on the map. SWMMCanada pulls the Canadian open data for that spot (rainfall, terrain — 1 m LiDAR where available, land cover, soil, ECCC design-storm intensities, and the city's storm + sanitary pipes) and assembles a complete `model.inp` you can open and run in EPA SWMM. No hunting across data portals, no manual setup.

> [!TIP]
> **🌐 Try it now. No install, no deployment.** A hosted **beta** is live at **[swmm.h2ox.me](https://swmm.h2ox.me/)**. Draw an area and build a SWMM model right in your browser.
>
> The demo runs on a small server (**~2 GB RAM**), so it works best for **small areas**; large regions can run out of memory and fail. For large-scale modeling, self-host the frontend **and** backend on a bigger machine or an **HPC** cluster. Both run well as shipped in this repo (see **[DEPLOY.md](DEPLOY.md)**).

<p align="center">
  <img src="results/victoria_app.png" width="820" alt="The SWMMCanada web app after building downtown Victoria: storm (blue) and sanitary (brick) networks with flow arrows and diameter-scaled pipes, floating layer toggles, and a click-to-inspect card showing subcatchment attributes" />
</p>
<p align="center"><sub>The app after building a downtown Victoria area: storm (blue) and sanitary (brick) networks with flow arrows, pipe width scaled by diameter, per-system layer toggles, and click-to-inspect attributes, with the model package (SWMM, MIKE+, ICM) ready to download.</sub></p>

## Two modes (picked automatically)

SWMMCanada chooses how to build the network from **where you draw**. You don't set anything:

| Mode | What it does | Where it kicks in |
|---|---|---|
| **Real network** | uses the city's published storm pipes (real inverts, diameters, manholes, and outfalls) | **10 cities** that publish a storm network: Victoria, Ottawa, Calgary, Surrey, London, Kitchener–Waterloo, Kelowna, Regina, Vancouver, and Reykjavík (IS) — the first international city, on Iceland's national *fitjuskrá* schema |
| **Synthesize** | builds a realistic network from the street map + open data: DEM-delineated subcatchments where the terrain earns it, pipes sized by the rational method with real ECCC IDF intensities | anywhere else in Canada |

Either mode then gives you the same things: subcatchments, rainfall, and a shareable data package. Where a city also publishes parcels (like Victoria), the subcatchments follow real lot lines. Where a city publishes its **sanitary sewer** too (Regina), the model carries it as a second tagged system in the same `.inp` — the foundation for dual-drainage and separated-sewer studies.

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

**Verify the install offline** — no live data, no external APIs, a good first check right after cloning:

```bash
backend/.venv/bin/python backend/scripts/smoke_build.py   # builds a tiny runnable model.inp
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
  acquire/     ECCC climate · NRCan DEM (MRDEM + HRDEM LiDAR) · NALCMS land cover · SoilGrids soil · HYDAT flow
  sources/     live data adapters (climate, DEM incl. 1 m LiDAR auto-select, ECCC IDF design storms, land cover, soil, OSM streets)
    cities/    base.py (shared assembler) + 10 real-network adapters (victoria · ottawa · calgary · surrey · london · kitchener · kelowna · regina · vancouver · reykjavik)
  network/     street-graph synthesis · DEM subcatchments behind a terrain honesty gate (Voronoi fallback) · rational-method pipe sizing
  derive/      clip + zonal stats -> subcatchment parameters
  build/       assemble + validate the SWMM .inp
  datastore/   model-ready datastore (GeoPackage + netCDF + JSON)
  export/      model exporters reading the datastore: SWMM · MIKE+ CS import package · InfoWorks ICM ODIC package
  api/         FastAPI async tasks API
  pipeline.py  build_from_aoi · build_from_<city> (9 real-network cities)

frontend/src/            # React + Vite + MapLibre web app
  components/   MapPanel.tsx (map + draw AOI) · ControlPanel.tsx (build, layers, download)
  lib/api.ts    backend client (submit, poll, preview, download)
  store.ts      Zustand state · types.ts shared types
```

## What you get

Every build ships one result package:

- **`model.inp`** — runs in EPA SWMM 5.2, with snowmelt whenever temperature data exists (plus `manifest.json`);
- **`datastore/`** — the shareable model-ready datastore (GeoPackage network + netCDF/CF forcing + JSON provenance) that every export target reads from;
- **`mikeplus/`** — a **DHI MIKE+ Collection System import package**: `nodes` / `links` / `catchments` shapefiles + `rain.csv` + a field-mapping sheet, with import steps and every approximation documented inside (`README.md` / `field_mapping.md`);
- **`icm/`** — an **InfoWorks ICM Open Data Import Centre package**: `nodes.csv` / `conduits.csv` named for ODIC **Auto-Map**, `subcatchments.shp`, an InfoWorks-format rainfall event CSV (+ plain fallback), and the same field-mapping/lossy-report convention — the SWMM curve number transfers losslessly (`curve_number`);
- **`validation.json`** — the model's health report: structural checks, the delineation method used, and its confidence;
- **`preview/`** — map layers for the web UI.

More export targets plug into the same interface.

## Run, calibrate & quantify uncertainty with Agentic SWMM

SWMMCanada is the **first half** of a closed loop: it turns an area into a complete, runnable model. The **second half**, **[Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow)**, takes that model the rest of the way: it runs EPA SWMM for you, calibrates against observations, post-processes, and does uncertainty analysis. Hand a SWMMCanada package to Agentic SWMM and the loop closes: **open data → model → calibrated results with uncertainty.**

> [!WARNING]
> **Models are not calibrated — SWMMCanada builds runnable _first-pass_ models from open data, not a calibrated design tool by itself.** It gets you a complete, runnable model fast, but the parameters (rainfall losses, roughness, curve numbers) are first-pass estimates. Calibrate against observations before using any results for design or decisions, which is exactly what **[Agentic SWMM](https://github.com/Zhonghao1995/agentic-swmm-workflow)** automates. Tired of tweaking parameters and plotting results by hand? Try Agentic SWMM: it does all the downstream work through plain natural-language chat (https://aiswmm.com/demo/), and every step stays auditable and transparent (https://doi.org/10.3390/aieng1010005).

## More

- **[ASSUMPTIONS.md](ASSUMPTIONS.md)**: what's real, derived, or approximated in a model, layer by layer. Most of it is grounded in real data; the approximations (and the uncalibrated caveat) are called out.
- **[DATA.md](DATA.md)**: every dataset used, with links, licences, and how each one is used (ECCC rainfall, NRCan terrain & land cover, SoilGrids soil, OpenStreetMap, and the nine municipal storm networks). All free / open.
- **[RESULTS.md](RESULTS.md)**: real-city validation, figures, and the EPA SWMM numbers.
- **[DEPLOY.md](DEPLOY.md)**: run the backend as a container (GHCR image) and the frontend as a static site (GitHub Pages), and how the two are wired.
- **Built with** Python (geopandas, swmm-api, FastAPI) and React + MapLibre. Full dependency lists in `backend/pyproject.toml` and `frontend/package.json`.

## Citation

If you use SWMMCanada in your work, please cite the preprint (APA):

> Zhang, Z. (2026). *SWMMCanada: An open-source service for generating ready-to-run urban drainage models across Canada* [Preprint]. EarthArXiv. https://doi.org/10.31223/X5NR31

To cite a specific version of the software itself, also cite the archived release:

> Zhang, Z. (2026). *SWMMCanada: ready-to-run EPA SWMM models anywhere in Canada from open data* (Version 0.4.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.21058544

BibTeX and other formats are available via **Cite this repository** in the GitHub sidebar (generated from [`CITATION.cff`](CITATION.cff)) — it resolves to the preprint above. The Zenodo DOI is the *concept DOI* — it always resolves to the latest software version.

## License

MIT © 2026 Zhonghao Zhang
