# SWMMCanada

<p align="center">
  <a href="https://github.com/Zhonghao1995/SWMMCanada/actions/workflows/ci.yml"><img src="https://github.com/Zhonghao1995/SWMMCanada/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license" /></a>
  <img src="https://img.shields.io/badge/status-early%20development-orange" alt="Status: early development" />
  <img src="https://img.shields.io/badge/EPA%20SWMM-5.2-1F6FEB" alt="EPA SWMM 5.2" />
</p>

<p align="center"><em>🚧 Under construction — this repository serves as the data-preprocessing and upstream model-building module for <strong>Agentic SWMM</strong> and <strong>Agentic MIKE+</strong>.</em></p>

**Draw an area anywhere in Canada and get a ready-to-run EPA SWMM stormwater model.**

You draw (or upload) a boundary on the map. SWMMCanada pulls the Canadian open data for that spot — rainfall, terrain, land cover, soil, and the city's storm pipes — and assembles a complete `model.inp` you can open and run in EPA SWMM. No hunting across data portals, no manual setup.

<p align="center">
  <img src="results/victoria_app.png" width="820" alt="The SWMMCanada web app after building downtown Victoria: real storm network and parcel-shaped subcatchments on the map, with the build mode and model layers in the side panel" />
</p>
<p align="center"><sub>The app after building a downtown Victoria area: real storm network (blue), outfalls (red), and parcel-shaped subcatchments (green), with the model ready to download.</sub></p>

## Two ways it builds the network

- **Real city pipes** — draw inside a city that publishes its storm network (right now **Victoria** and **Ottawa**) and it uses the real pipes, manholes, and outfalls. Where the city also publishes parcels (like Victoria), the subcatchments follow real lot lines.
- **Synthesized** — anywhere else in Canada, it builds a realistic network from the street map and open data.

Either way you also get subcatchments, rainfall, and a shareable data package. These are starting-point models: they run clean in EPA SWMM 5.2, but the parameters aren't calibrated yet.

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

## What you get

A `model.inp` that runs in EPA SWMM 5.2, a `datastore/` package you can share (GeoPackage + netCDF + JSON), and map layers.

## More

- **[RESULTS.md](RESULTS.md)** — real-city validation, figures, and the EPA SWMM numbers.
- **Data** (all free / open): rainfall from ECCC, terrain and land cover from NRCan, soil from SoilGrids, storm pipes from city ArcGIS.
- **Built with** Python (geopandas, swmm-api, FastAPI) and React + MapLibre. Full dependency lists in `backend/pyproject.toml` and `frontend/package.json`.

## License

MIT © 2026 Zhonghao Zhang
