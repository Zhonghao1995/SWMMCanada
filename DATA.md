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
| Storm network + land | City of Calgary Open Data | real storm network, parcels, buildings | OGL – Calgary |
| Drainage + Lot/Buildings | City of Surrey Open Data | real storm network, parcels, buildings | OGL – Surrey |
| Sewer + land (BaseMaps) | City of London Open Data | real storm network, parcels, buildings | City of London ToU |
| Storm (Region of Waterloo) | Kitchener / Region of Waterloo | real storm network, buildings | OGL – Kitchener |
| Storm utilities + land | City of Kelowna Open Data | real storm network, parcels, buildings | OGL – Kelowna |
| Storm Sewer Network + land | City of Regina Open Data | real storm network, parcels, buildings | OGL – Regina |
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
- **How SWMMCanada uses it:** clips the DEM to the AOI for ground elevations and slopes, to orient the synthesized drainage network downhill, and to delineate DEM subcatchments where the terrain honesty gate allows.
- **Licence:** Open Government Licence – Canada.

### Terrain (DEM) — NRCan HRDEM LiDAR 1–2 m (optional)

- **What:** the High Resolution DEM (LiDAR projects, 1 m / 2 m), CanElevation Series — DTM + DSM per acquisition project.
- **Provider:** Natural Resources Canada (NRCan).
- **Discovery:** NRCan datacube STAC — `https://datacube.services.geo.ca/stac/api` (collection `hrdem-lidar`); COGs on AWS S3, EPSG:3979.
- **How SWMMCanada uses it:** **default**: where a sampled read proves the LiDAR actually covers the AOI, the 1–2 m DTM replaces MRDEM; anywhere else it falls back to MRDEM-30 automatically (`SWMMCANADA_DEM_SOURCE=mrdem` forces the 30 m national fallback). The subcatchment-delineation gate is resolution-aware (4.0 % at 30 m posting, 1.0 % under LiDAR).
- **Licence:** Open Government Licence – Canada.

### Design rainfall intensities — ECCC Engineering Climate IDF

- **What:** Intensity-Duration-Frequency tables (2–100 yr return periods, 5 min–24 h durations, with fitted power-law coefficients) for 662 ECCC stations, from the Engineering Climate Dataset (v3.20/v3.10/v3.00 per-province archives — the newest directly fetchable per-station distribution).
- **Provider:** Environment and Climate Change Canada.
- **Endpoint:** `https://collaboration.cmc.ec.gc.ca/cmc/climate/Engineer_Climate/IDF/` — per-station `.txt` extracted from the province archive via HTTP-Range partial reads (~200 KB per station, not the full zip). A bundled 662-station index (id/name/coordinates) ships with the package.
- **How SWMMCanada uses it:** synthesis-mode pipe sizing (rational method): design intensity at the pipe's time of concentration from the **nearest station's** fitted curve, **T = 5 yr** default. If IDF is unreachable, sizing degrades to a documented 30 mm/h constant with a provenance note — never failing the build.
- **Licence:** Environment and Climate Change Canada Data Servers End-use Licence (open).

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
  - Sewer — `https://maps.victoria.ca/server/rest/services/OpenData/OpenData_Sewer/MapServer`
    layer: `4` Sewer Gravity Mains, `WaterType='SEW'` + `LifecycleStatus='ACT'` (separated **sanitary** skeleton, second tagged system)
  - Land — `https://maps.victoria.ca/server/rest/services/OpenData/OpenData_Land/MapServer`
    layers: `5` Parcels (Folio) · `1` Buildings
- **How SWMMCanada uses it:** Victoria publishes explicit pipe topology (upstream/downstream node IDs), so the network is the real pipes with real inverts and diameters. Parcels and buildings drive **parcel-shaped subcatchments** with building-based imperviousness.
- **Licence:** Open Government Licence – City of Victoria.

#### City of Ottawa, ON

- **What:** the real storm network (storm pipes, outfalls, storm inlets / catch basins).
- **Provider:** City of Ottawa Open Data.
- **Browse:** <https://open.ottawa.ca>
- **Endpoint (ArcGIS REST):** `https://maps.ottawa.ca/arcgis/rest/services/WastewaterInfrastructure/MapServer`
  layers: `26` Storm Pipes · `22` Storm Outfalls · `21` Storm Inlets (catch basins) · `7` Sanitary Pipes, `LIFE_CYCLE_STATUS='IN_SERVICE'` (separated **sanitary** skeleton, second tagged system). Served as Esri JSON.
- **How SWMMCanada uses it:** Ottawa publishes no explicit node IDs, so topology is inferred from pipe geometry; subcatchments seed on catch basins (Ottawa publishes no parcels, so a catch-basin tessellation is used). Storm Manholes (`23`) carry no rim/ground elevation field, so node max depths keep the assembler default.
- **Licence:** Open Government Licence – City of Ottawa.

#### More real-network cities (BC · ON · AB · SK)

Six more cities have been added via the same adapter pattern (read the
city's ArcGIS REST layers → shared `cities/base.py` assembler). Each clears the bar: published
storm pipes **with invert elevations** plus resolvable topology. All endpoints verified live
2026-06-22 (Regina: 2026-07-02); coverage is gated by a non-overlapping per-city bbox in
`pipeline._REAL_NETWORK_CITIES`.

| City | ArcGIS REST service | Key storm layers (invert field) | Topology | Parcels / buildings | Licence |
|---|---|---|---|---|---|
| **Calgary, AB** | `services1.arcgis.com/AVP60cs0Q9PEA8rH/.../FeatureServer` | `Storm_pipe_DMAP` (UP/DN_INVERT) · `Storm_Manholes_DMAP` (RIM_ELEV → node max depths) · Inlet/Outfall · Catch basin · `Sanitary_pipes_DMAP`, ACTIVE `MAIN`/`TL` (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `Parcel_with_Roll_2026` · `Buildings_from_Digital_Aerial_Survey` | OGL – Calgary |
| **Surrey, BC** | `gisservices.surrey.ca/arcgis/rest/services/OpenData/MapServer` | `18` Drn Mains (UP/DOWN_ELEVATION) · `23` Manholes (RIM_ELEVATION → node max depths) · `25` Devices=Outlet · `24` Catch Basins · `41` San Mains, Gravity + In Service (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `148` Lot · `155` Buildings | OGL – Surrey |
| **London, ON** | `maps.london.ca/server/rest/services/OpenData/OpenData_Environment/MapServer` | `5` Sewer Pipes `FlowType='STM'` (Upstream/DownstreamInvert) · `2/3` Nodes · `4` Outfalls · `1` Catch Basins · same layer `FlowType='SAN'` + `ConstructedStatus='Built'` (separated **sanitary** skeleton, second tagged system) | explicit node IDs | BaseMaps `53` Parcels · `3` Buildings | City of London ToU |
| **Kitchener–Waterloo, ON** | `services1.arcgis.com/qAo1OsXi67t7XgmS/.../FeatureServer` | `Storm_Pipes` (UP/DN_INVERT) · `Storm_Manholes` · `Storm_Outlets` · `Storm_Catchbasins` · `Sanitary_Pipes`, ACTIVE GRAVITY (separated **sanitary** skeleton, second tagged system) | explicit integer node IDs | `Building_Outlines` only (no parcel polygons) | OGL – Kitchener |
| **Kelowna, BC** | `geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Utilities_Storm/MapServer` | `22` Storm Main (INVERT_IN_Z/OUT_Z) · `7` Manholes · `4` Outfalls · `19` Catch Basins · `OpenData_Utilities_Sanitary` `11` Sanitary Main, `STATUS='A'` (separated **sanitary** skeleton, second tagged system) | geometry-inferred | Planning `3` Legal Parcel · `17` Building Outlines | OGL – Kelowna |
| **Regina, SK** | `opengis.regina.ca/arcgis/rest/services/OpenData` ([open.regina.ca](https://open.regina.ca)) | StormSewerNetwork `5` Storm Sewer Line, `STATUS='ACTIVE'` non-Force (START/ENDELEVATION) · `2` Manholes · `4` Outfalls · `3` Catch Basins · DomesticSewerNetwork `3` Domestic Sewer Line (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `Parcels` (ASSESSMENT_REGIONS lots) · `BuildingFootprint` | [OGL – Regina](https://www.regina.ca/city-government/open-data/open-government-licence/) |

One feed covers the whole **Region of Waterloo** (Kitchener / Waterloo / Cambridge). How each
city's data turns into a model — and which parts are real vs derived vs synthesized — is in
**[ASSUMPTIONS.md](ASSUMPTIONS.md)**.

---

## Map display — CARTO basemap

- **What:** the light "Positron" basemap behind the web app's map.
- **Provider:** CARTO, built on OpenStreetMap.
- **Browse:** <https://carto.com/basemaps>
- **How SWMMCanada uses it:** display only — it is not part of the model. Attribution: © OpenStreetMap contributors, © CARTO.

---

## Adding a city

A new real-network city is one adapter in `backend/swmmcanada/sources/cities/<city>.py` (fetch its ArcGIS layers + map fields to the shared schema) plus a one-line `build_from_<city>` wrapper; the shared `cities/base.py` does the SWMM assembly. See the existing `victoria.py` and `ottawa.py` for the two patterns (explicit-topology vs geometry-inferred).
