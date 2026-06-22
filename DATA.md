# Data sources

SWMMCanada builds every model from public, open data — nothing proprietary, and no API keys for the core data path. This page lists each dataset: who publishes it, where it comes from, its licence, and exactly how SWMMCanada uses it.

All data is free. Most is under the **Open Government Licence – Canada** (or a municipal equivalent); SoilGrids is **CC BY 4.0** and OpenStreetMap is **ODbL**. You are responsible for honouring each licence (attribution in particular) in anything you publish from a generated model.

## At a glance

| Dataset | Provider | Used for | Licence |
|---|---|---|---|
| GeoMet climate (daily) | ECCC / MSC | rainfall + temperature (the raingage) | OGL – Canada |
| MRDEM 30 m | NRCan — CanElevation | terrain → slopes, flow direction | OGL – Canada |
| NALCMS 2020 | CEC / NRCan | land cover → imperviousness | free use with attribution |
| SoilGrids / HYSOGs | ISRIC | soil → hydrologic soil group → curve number | CC BY 4.0 |
| HYDAT (hydrometric) | ECCC — Water Survey of Canada | observed streamflow (validation) | OGL – Canada |
| OpenStreetMap | OSM contributors | street graph for synthesized networks | ODbL |
| Storm Drain + Land | City of Victoria Open Data | real storm network, parcels, buildings | OGL – Victoria |
| Wastewater Infrastructure | City of Ottawa Open Data | real storm network | OGL – Ottawa |
| Positron basemap | CARTO + OSM | web-map background (display only) | © OSM, © CARTO |

---

## National open data (used in every build)

### Rainfall and temperature — ECCC GeoMet

- **What:** daily precipitation and temperature from the nearest active climate station, turned into the model's raingage time series.
- **Provider:** Environment and Climate Change Canada (ECCC) / Meteorological Service of Canada (MSC).
- **Browse:** <https://climate.weather.gc.ca/> · API docs: <https://eccc-msc.github.io/open-data/msc-geomet/readme_en/>
- **Endpoint:** `https://api.weather.gc.ca` — OGC API collections `climate-stations` (station selection) and `climate-daily` (daily values), queried by AOI bbox and date range. No scraping, no key.
- **How SWMMCanada uses it:** picks the nearest station with real precipitation over the period, coerces trace (`T`) to 0, and writes it as the SWMM `[TIMESERIES]` + `[RAINGAGES]`.
- **Licence:** Open Government Licence – Canada.

### Terrain (DEM) — NRCan MRDEM 30 m

- **What:** the Medium Resolution Digital Elevation Model (30 m), CanElevation Series — both the digital terrain model (DTM) and surface model (DSM).
- **Provider:** Natural Resources Canada (NRCan).
- **Browse:** open.canada.ca — *CanElevation Series / MRDEM*.
- **Endpoint (Cloud-Optimized GeoTIFF on AWS S3, EPSG:3979):**
  - `https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif`
  - `https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dsm.tif`
- **How SWMMCanada uses it:** clips the DEM to the AOI for ground elevations and slopes, and to orient the synthesized drainage network downhill.
- **Licence:** Open Government Licence – Canada.

### Land cover → imperviousness — NALCMS 2020

- **What:** the North American Land Change Monitoring System (NALCMS) 2020 land-cover raster (30 m).
- **Provider:** Commission for Environmental Cooperation (CEC), distributed through NRCan / geo.ca.
- **Browse:** <https://www.cec.org/north-american-land-change-monitoring-system/>
- **Endpoint:** geo.ca STAC — `https://datacube.services.geo.ca/stac/api/search` (COG assets).
- **How SWMMCanada uses it:** maps each land-cover class to a percent-impervious value (legend `nalcms-2020-v1`, overridable) to estimate subcatchment imperviousness in synthesize mode and as the fallback where a city publishes no buildings.
- **Licence:** free use with attribution (CEC / Government of Canada).

### Soil → curve number — ISRIC SoilGrids (or HYSOGs)

- **What:** soil properties used to assign a hydrologic soil group (HSG: A/B/C/D), which maps to an SCS curve number for infiltration.
- **Provider:** ISRIC – World Soil Information (SoilGrids). A local **HYSOGs** (Hydrologic Soil Groups) raster can be substituted offline.
- **Browse:** <https://soilgrids.org> · map services: <https://maps.isric.org>
- **Endpoint:** ISRIC WCS / MapServer (`https://maps.isric.org/mapserv`), auth-free.
- **How SWMMCanada uses it:** derives HSG over the AOI, then applies a TR-55 / SCS HSG→CN table (default urban: A=77, B=85, C=90, D=92) for the SWMM `[INFILTRATION]` (CURVE_NUMBER).
- **Licence:** CC BY 4.0 (SoilGrids).

### Observed streamflow — ECCC HYDAT / Water Survey of Canada

- **What:** observed daily streamflow (m³/s) at Water Survey of Canada gauges near the AOI.
- **Provider:** ECCC — Water Survey of Canada (HYDAT / GeoMet `hydrometric`).
- **Browse:** National archive (HYDAT) on canada.ca; stations via GeoMet.
- **How SWMMCanada uses it:** optional — for comparing/validating model output against gauged flow. Not required to build a model.
- **Licence:** Open Government Licence – Canada.

---

## Network sources

### Synthesized networks — OpenStreetMap

- **What:** the street network for the AOI, used to synthesize a plausible drainage network anywhere in Canada (no municipal data needed).
- **Provider:** OpenStreetMap contributors, via [osmnx](https://osmnx.readthedocs.io).
- **Browse:** <https://www.openstreetmap.org>
- **Endpoint:** OSM Overpass API through `osmnx.graph_from_bbox(..., network_type="drive")`.
- **How SWMMCanada uses it:** builds a graph from the street centerlines, then lays out conduits and Voronoi subcatchments along it. (Planned: OSM street blocks + building footprints to give no-parcel cities the same parcel-style subcatchments Victoria gets.)
- **Licence:** Open Database License (ODbL) — © OpenStreetMap contributors.

### Real municipal networks

Each supported city has its own adapter under `backend/swmmcanada/sources/cities/`. Adapters read the city's published ArcGIS REST service directly.

#### City of Victoria, BC

- **What:** the real storm-drain network (gravity mains, manholes, fittings, outfalls, catch basins) plus parcels and building footprints.
- **Provider:** City of Victoria Open Data.
- **Browse:** <https://opendata.victoria.ca> · e.g. [Storm Drain Gravity Mains](https://opendata.victoria.ca/datasets/VicMap::storm-drain-gravity-mains/explore)
- **Endpoints (ArcGIS REST):**
  - Storm Drain — `https://maps.victoria.ca/server/rest/services/OpenData/OpenData_StormDrain/MapServer`
    layers: `10` Gravity Mains · `4` Manholes · `3` Fittings · `5` Outfalls · `1` Catch Basins
  - Land — `https://maps.victoria.ca/server/rest/services/OpenData/OpenData_Land/MapServer`
    layers: `5` Parcels (Folio) · `1` Buildings
- **How SWMMCanada uses it:** Victoria publishes explicit pipe topology (upstream/downstream node IDs), so the network is the real pipes with real inverts and diameters. Parcels and buildings drive **parcel-shaped subcatchments** with building-based imperviousness.
- **Licence:** Open Government Licence – City of Victoria.

#### City of Ottawa, ON

- **What:** the real storm network (storm pipes, outfalls, storm inlets / catch basins).
- **Provider:** City of Ottawa Open Data.
- **Browse:** <https://open.ottawa.ca>
- **Endpoint (ArcGIS REST):** `https://maps.ottawa.ca/arcgis/rest/services/WastewaterInfrastructure/MapServer`
  layers: `26` Storm Pipes · `22` Storm Outfalls · `21` Storm Inlets (catch basins). Served as Esri JSON.
- **How SWMMCanada uses it:** Ottawa publishes no explicit node IDs, so topology is inferred from pipe geometry; subcatchments seed on catch basins (Ottawa publishes no parcels, so a catch-basin tessellation is used).
- **Licence:** Open Government Licence – City of Ottawa.

---

## Map display — CARTO basemap

- **What:** the light "Positron" basemap behind the web app's map.
- **Provider:** CARTO, built on OpenStreetMap.
- **Browse:** <https://carto.com/basemaps>
- **How SWMMCanada uses it:** display only — it is not part of the model. Attribution: © OpenStreetMap contributors, © CARTO.

---

## Adding a city

A new real-network city is one adapter in `backend/swmmcanada/sources/cities/<city>.py` (fetch its ArcGIS layers + map fields to the shared schema) plus a one-line `build_from_<city>` wrapper; the shared `cities/base.py` does the SWMM assembly. See the existing `victoria.py` and `ottawa.py` for the two patterns (explicit-topology vs geometry-inferred).
