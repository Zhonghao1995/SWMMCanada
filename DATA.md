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
| Tide predictions (IWLS wlp) | DFO — Canadian Hydrographic Service | stage boundary for tide-affected outfalls | OGL – Canada |
| OpenStreetMap | OSM contributors | street graph for synthesized networks | ODbL |
| Storm Drain + Land | City of Victoria Open Data | real storm network, parcels, buildings | OGL – Victoria |
| Wastewater Infrastructure | City of Ottawa Open Data | real storm + combined network, buildings | OGL – Ottawa |
| Storm network + land | City of Calgary Open Data | real storm network, parcels, buildings | OGL – Calgary |
| Drainage + Lot/Buildings | City of Surrey Open Data | real storm network, parcels, buildings | OGL – Surrey |
| Sewer + land (BaseMaps) | City of London Open Data | real storm network, parcels, buildings | City of London ToU |
| Storm (Region of Waterloo) | Kitchener / Region of Waterloo | real storm network, buildings | OGL – Kitchener |
| Storm utilities + land | City of Kelowna Open Data | real storm network, parcels, buildings | OGL – Kelowna |
| Storm Sewer Network + land | City of Regina Open Data | real storm network, parcels, buildings | OGL – Regina |
| Sewer network (VanMap) + land | City of Vancouver (VanMap public services + Open Data) | real storm+combined network, parcels, buildings | OGL – Vancouver (land); VanMap public services (network) |
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
the city registry (`sources/cities/registry.py`).

| City | ArcGIS REST service | Key storm layers (invert field) | Topology | Parcels / buildings | Licence |
|---|---|---|---|---|---|
| **Calgary, AB** | `services1.arcgis.com/AVP60cs0Q9PEA8rH/.../FeatureServer` | `Storm_pipe_DMAP` (UP/DN_INVERT) · `Storm_Manholes_DMAP` (RIM_ELEV → node max depths) · Inlet/Outfall · Catch basin · `Sanitary_pipes_DMAP`, ACTIVE `MAIN`/`TL` (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `Parcel_with_Roll_2026` · `Buildings_from_Digital_Aerial_Survey` | OGL – Calgary |
| **Surrey, BC** | `gisservices.surrey.ca` — `Public/Drainage/MapServer` (network) + `OpenData/MapServer` (land) | `Public/Drainage 14` Mains, Gravity + In Service (UP/DOWN_ELEVATION, **UP_NODE/DOWN_NODE 100%** — the OpenData view strips them) · nodes `4` Manholes / `2` Catch Basins / `3` Devices (NODE_NO, RIM_ELEVATION; Devices 'Outlet' = outfalls) · OpenData `41` San Mains, Gravity + In Service (separated **sanitary** skeleton, second tagged system) | **explicit node IDs** (audit 2026-07-14) | `148` Lot · `155` Buildings | OGL – Surrey; Public/Drainage published token-free |
| **London, ON** | `maps.london.ca/server/rest/services/OpenData/OpenData_Environment/MapServer` | `5` Sewer Pipes `FlowType='STM'` (Upstream/DownstreamInvert) · `2/3` Nodes · `4` Outfalls · `1` Catch Basins · same layer `FlowType='SAN'` + `ConstructedStatus='Built'` (separated **sanitary** skeleton, second tagged system) | explicit node IDs | BaseMaps `53` Parcels · `3` Buildings | City of London ToU |
| **Kitchener–Waterloo, ON** | `services1.arcgis.com/qAo1OsXi67t7XgmS/.../FeatureServer` | `Storm_Pipes` (UP/DN_INVERT) · `Storm_Manholes` · `Storm_Outlets` · `Storm_Catchbasins` · `Sanitary_Pipes`, ACTIVE GRAVITY (separated **sanitary** skeleton, second tagged system) | explicit integer node IDs | `Building_Outlines` only (no parcel polygons) | OGL – Kitchener |
| **Kelowna, BC** | `geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Utilities_Storm/MapServer` | `22` Storm Main (INVERT_IN_Z/OUT_Z) · `7` Manholes · `4` Outfalls · `19` Catch Basins · `OpenData_Utilities_Sanitary` `11` Sanitary Main, `STATUS='A'` (separated **sanitary** skeleton, second tagged system) | geometry-inferred | Planning `3` Legal Parcel · `17` Building Outlines | OGL – Kelowna |
| **Regina, SK** | `opengis.regina.ca/arcgis/rest/services/OpenData` ([open.regina.ca](https://open.regina.ca)) | StormSewerNetwork `5` Storm Sewer Line, `STATUS='ACTIVE'` non-Force (START/ENDELEVATION) · `2` Manholes · `4` Outfalls · `3` Catch Basins · DomesticSewerNetwork `3` Domestic Sewer Line (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `Parcels` (ASSESSMENT_REGIONS lots) · `BuildingFootprint` | [OGL – Regina](https://www.regina.ca/city-government/open-data/open-government-licence/) |
| **Coquitlam, BC** | `services2.arcgis.com/Q6Lq3evZUGfPrN7o` ([data.coquitlam.ca](https://data.coquitlam.ca)) | `Drainage Utility/16` Drainage Mains, `STATUS='OPERATING'` (UP/DN_ELEVATION pipe-end inverts, UP/DN_TERM_ID node labels) · `6` Manholes (RIM_ELEVATION) · `10` Outfalls · `11` Catchbasins · `Sanitary Utility/10` Sanitary Mains (UP/DN_ELEV; separated **sanitary** skeleton, second tagged system) | geometry topology, labelled ends | `Cadastral/13` Parcels · `15` Buildings | OGL – Coquitlam |
| **Barrie, ON** | `gispublic.barrie.ca/arcgis/rest/services/Open_Data` | `StormInfrastructure/1` Storm Linear, piped TYPEs + `STATUS='ACTIVE'` (INV_UP/DN_ELV pipe-end inverts, FROM/TO_ID labels, PIPESHP real sections) · `0` Storm Device (TOPELEV rims; OUTFALL/outlet family; catch-basin family seeds) · `SanitaryInfrastructure/2`, LOCAL/TRUNK (separated **sanitary** skeleton, second tagged system) | geometry topology, labelled ends | `ParcelPublishing/2` Parcel · `FacilitiesStreets/36` Buildings | Open Data – Barrie |
| **Abbotsford, BC** | `services8.arcgis.com/ZYlQy38aWlfDG1Qh` ([opendata-abbotsford.hub.arcgis.com](https://opendata-abbotsford.hub.arcgis.com)) | `Engineering_Layers_External_Feature/207` Drainage Mains, `LIFECYCLE_STATUS=0` (UPSTREAM/DOWNSTREAM_INVERT; 0 and -1 both missing sentinels; UPLINK/DOWNLINK labels, 'N/A'=absent; MATERIAL coded domain) · `204` Manholes (RIM_ELEVATION) · `198` Outlets · `205` Catchbasins · `214` Sanitary Mains (separated **sanitary** skeleton, second tagged system) | geometry topology, labelled ends | `Parcel_Layers_External_Feature/0` Parcels (no public building footprints) | Open Government Licence – Abbotsford |
| **Saskatoon, SK** | `gisext.saskatoon.ca` — network: `Core/WSSTreatment_AGOL` (public token-free city service, **not in the OD folder; licence unstamped** — recorded honestly) · parcels: `arcgisod .../OD/LandSurface` (official open data) | `5` Storm Main, `STATUS='A1'` + PIPETYPE Main/Trunk/Bypass (UPELEV/DOWNELEV inverts; join verified: pipe UPELEV = manhole INVERTELEV) · `6` Storm Manhole (RIMELEV) · `7` CatchBasins · `1`/`2` Sanitary Main/Manhole (separated **sanitary** skeleton, second tagged system) | explicit FROMMH/TOMH ids (used as labels) | `OD/LandSurface/1` Parcel (no building footprints) | parcels: OGL – Saskatoon; network: unstamped |
| **Toronto, ON** | `services5.arcgis.com/MFwjjnaTnj9B3bil` (Toronto Water external views; same data as the [open.toronto.ca](https://open.toronto.ca) daily-updated Sewer packages) | `TW_Sewer_Gravity_Main_Ext_View`, `WATERTYPE IN ('Storm','Combined')` — **Combined joins the storm system**, ADR 0021 (UPELEV/DOWNELEV inverts, FROMMH/TOMH labels, DIAMETER mm) · `TW_Sewer_Manhole_Ext_View` (RIMELEV) · `TW_Sewer_Discharge_Point_Ext_View` outfalls · `TW_Sewer_Inlet_Ext_View` seeds · `WATERTYPE='SAN'` (separated **sanitary** skeleton, second tagged system; CSO/SCSO/EO/FD excluded) | explicit MH ids (used as labels) | none on the TW org (land-cover imperviousness, Ottawa-style) | Open Government Licence – Toronto |
| **Kingston, ON** | utility.arcgis.com proxy services from the city DCAT ([opendatakingston](https://opendatakingston.cityofkingston.ca)) | `Eng/Storm_Pipe`, `CONSTRUCTION_STATUS='Constructed'` (UPSTREAM/DOWNSTREAM_INVERT inside a (60,200) m band — city publishes literal 0/1 placeholders; MANHOLE/INLET/OUTLET id families as labels; DOWNSTREAM_OUTLET_ID ends = outfall candidates) · `Eng/Storm_Inlet` seeds · Storm Manhole has NO elevations (default depths) | geometry topology, labelled ends | `Buildings/1` footprints (Parcel MapServer refuses anonymous spatial queries) · **no sanitary published** | Open Data – Kingston |
| **Peterborough, ON** | `citymaps.peterborough.ca/.../SanStormExternal/MapServer` | `18` Storm Gravity Main, `WATERTYPE='SW'` (UPELEV/DOWNELEV inverts; MH-prefixed FROMMH/TOMH labels) · `13` Storm Manhole (RIMELEV) · `11` Storm Discharge Point outfalls · `12` Storm Inlet seeds · `5`/`2` San Gravity Main/Manhole (separated **sanitary** skeleton, second tagged system) | geometry topology, labelled ends | none on host (land-cover imperviousness) | Open Data – Peterborough |
| **Burnaby, BC** | `gis.burnaby.ca/arcgis/rest/services/OpenData` | `OpenData2/18` Storm Main, `SERVSTAT='I' OR NULL` (UPSELEV/DWNELEV inverts; UNITID/UNITID2 DM-labels; PIPESHP/PIPEHT sections) · `19` Catchbasin seeds · `10` Sanitary Main (separated **sanitary** skeleton, second tagged system) · fittings publish depth only, no rims (default max depths) | geometry topology, labelled ends | `OpenData4/7` Legal Parcels · `18` Building Outlines | OGL – Burnaby |
| **Nanaimo, BC** | `services1.arcgis.com/D2GiQOd2jzaj2Pzh` ([data.nanaimo.ca](https://data.nanaimo.ca)) | `Storm_Sewer_Main/7`, `FTYPE IN ('Main','Culvert')` (ST/END_INVERT + **ST/END_COVELV rims on the pipe rows**; MH-prefixed ST/END_NODE labels) · `Storm_Sewer_Catchbasin/2` seeds · `Sanitary_Sewer_Main/9`, `FTYPE='Gravity'` (separated **sanitary** skeleton, second tagged system) · Inlet/Outlet layer unused (no inlet/outlet distinction) | geometry topology, labelled ends | `Parcel_Map_BC_Parcel_Polygon/4` · `Building_Footprints/1` | Open Government Licence – Nanaimo |
| **New Westminster, BC** | `services3.arcgis.com/A7O8YnTNtzRPIn7T` ([opendata.newwestcity.ca](https://opendata.newwestcity.ca)) | `Sewer_Stormwater_Gravity_Main` + `Sewer_Combined_Gravity_Main` — **combined joins the storm system**, ADR 0021 (UPELEV/DOWNELEV where published; missing pipe ends take their FROMMH/TOMH manhole's `INVERT` chamber flow line — Reykjavík-precedent lift via the id join) · Stormwater+Combined Manholes (RIMELEV) · `Sewer_Sanitary_Gravity_Main` (separated **sanitary** skeleton, second tagged system) · `Sewer_Stormwater_Inlets` seeds | explicit FROMMH/TOMH ids (used as labels) | `Legal_Parcel` · `Building_Footprints2` | Open Government Licence – New Westminster |
| **Penticton, BC** | `services1.arcgis.com/ZMQyarkhNAnn8lip` ([opendata.penticton.ca](https://opendata.penticton.ca)) | `Storm_PRD/415` Pipe, In Service Gravity (upelev/downelev inverts; us/ds_feat SWMH-/SWDP- labels; diameter '300 mm' text; material '… - CP' codes) · `410` Outlet outfalls · `408` Catchbasin seeds · `Sanitary_PRD/316` Main/Trunk (separated **sanitary** skeleton, second tagged system) · manholes carry no rim (default depths) | geometry topology, labelled ends | none fetched (land-cover imperviousness) | Open Government Licence – Penticton |
| **White Rock, BC** | `maps.whiterockcity.ca/server/rest/services/opendata` | `Storm_Lines`, `Line_Type IN ('Pipe','Pi_Dc')` (Us/Ds_Inv_Ele inverts + **Us/Ds_Rm_E rims on the pipe rows**; MH-prefixed Us/Ds_End_Id labels) · `Storm_Manholes` seeds (no catch-basin layer) · `Sanitary_Lines`, `Line_Type='Gravity'` (separated **sanitary** skeleton, second tagged system) | geometry topology, labelled ends | `Parcel` · `Building_Outlines` | Open Data – White Rock |
| **Sarnia, ON** | `services1.arcgis.com/ICybsLmBXrZCZV3x` | `Storm_Sewers_Open_Data/1`, `Lifecycle_Status='Active'` (UpStreamIn/DownStream inverts in a (150,250) m band; MH_Up/Downstream labels; Diam_m text mm) · `Catch_Basins_Open_Data/1` seeds · `Sanitary_Sewers_Open_Data/0` (separated **sanitary** skeleton, second tagged system) · no manhole elevations (default depths) | geometry topology, labelled ends | `Buildings_Open_Data/2` (no parcels) | Open Data – Sarnia |
| **Whitby, ON** | `services5.arcgis.com/ATdLnvuMRJk8AGkQ` | `WhitbyStormLines` (UP/DOWN_INV inverts, 31%, 0-sentinel; FR/TO_NODE prefix-typed labels ST/CB/JX; DRAIN_AREA+CO_EFF per pipe, unconsumed) · catch-basin seeds extracted from CB-prefixed endpoints · **no sanitary** (Durham Region asset), no rims (default depths) | geometry topology, labelled ends | none published | Open Data – Whitby |
| **Esquimalt, BC** | `gis.esquimalt.ca/arcgis/rest/services/Services` | `Drain/4` Mains (no pipe elevations — manholes publish **compass-wall inverts** N/S/E/W + CENTER, lifted onto pipe ends by bearing via the DMH id join) · `Drain/2` Manholes (RIM_ELEVATION) · `Drain/0` Catch Basin seeds · `Outfalls/0` shoreline points · `Sewer/5` Sanitary Mains (on-row UPSTREAM/DOWNSTREAM_ELEVATION; separated **sanitary** skeleton) | id-joined directional inverts | `Cadastre/0` Parcel · `Buildings_EOC/0` | Open Data – Esquimalt |
| **Moncton, NB** | `services1.arcgis.com/E26PuSoie2Y7bbyI` — `Sewer_Agol3` (public token-free service, **not in the open.moncton.ca catalogue; licence unstamped** — recorded honestly) | `4` Sewer Main, `UNITTYPE IN ('STM','COMB')` — **combined joins the storm system**, ADR 0021 (UPSELEV/DWNELEV inverts; MAINKEY1/2 -> Manholes COMPKEY, MH-prefixed labels; PIPESHP/HEIGHT sections) · `3` Manholes (ZTOPCOV rim) · `1` Storm Inlet seeds · `UNITTYPE='SANI'` (separated **sanitary** skeleton, second tagged system) | explicit MAINKEY ids (used as labels) | `Parcels/0` · `Buildings/0` | network: unstamped; land: Open Data – Moncton |
| **Kamloops, BC** | `maps.kamloops.ca/arcgis/rest/services/OpenData` | `OpenDataDrainEmerGeo/12` DGravityMain (UPSTREAM/DOWNSTREAMINVERT, 94%; **missing sentinel = literal 9999**) · `14` DManhole (RIMELEVATION) · `16` DOutlet outfalls · `2` DCatchBasin seeds · `OpenDataSanitaryTel/12` (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `OpenDataPlanimetric/39` Buildings (no parcels) | Open Data – Kamloops |
| **Township of Langley, BC** | `services5.arcgis.com/frpHL0Fv8koQRVWY` ([data.tol.ca](https://data.tol.ca)) | `Drainage_Pipes`, Asbuilt/Preliminary-Constructed (Upstream/Downstream_Elevation inverts, 90% — nulls cluster at the table start; STRING Diameter; no material field) · `Drainage_Manholes` (Manhole_RimElev) · `Drainage_Sources` seeds · `Sanitary_Pipes` (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `Parcels` (no buildings) | [tol.ca/opengovlicense](https://www.tol.ca/opengovlicense) |
| **Delta, BC** | `services9.arcgis.com/w2mu7sRltY6PiQ7J` ([opendata-deltabc.hub.arcgis.com](https://opendata-deltabc.hub.arcgis.com)) | `Drainage_Mains` (START/END_IL inverts, 76%, **sentinel -99, genuine negatives near sea level** + **START/END_GL grounds on the rows** -> max depths; vertical datum CVD28GVRD2018 per the city's ELEV_NOTE, no shim applied) · `Sanitary_Gravity_Mains` (START/END_INVELEV; separated **sanitary** skeleton, second tagged system) | geometry-inferred (START/END_NODE fields exist but are empty) | `Property_Parcels` (no catch basins/buildings) | Open Data – Delta |
| **Greater Sudbury, ON** | `services.arcgis.com/q3mIlR87lZlZsds3` | `Drainage_view/9` Gravity Main, STYPE Storm/Trunk/Outfall/Tunnel (INVERTUS/DS in a (200,420) m band — live junk like a 958 m invert screened) · `6` Maintenance Hole (ELEVATION rim) · `4` Discharge outfalls · `0` Catch Basin seeds · `wastewater_open_data/10` (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `Land_Use_and_Boundaries_view/6` parcels · `Address_and_Building_Roofline/3` | Open Data – Greater Sudbury |
| **Chilliwack, BC** | `maps.chilliwack.com/arcgis/rest/services/External` (root directory empty — walk the folder; WAF-fronted) | `Dynamic_Utility/8` StormPipe (INVERT/INVERT_DOWN, 67%) · `Dynamic_Utility_Feature/5` StormSymbol split by SYM_TYPE (MANHOLE family = RIM depths; CATCHBASIN family = seeds) · `Dynamic_Utility/4` SanitaryPipe (separated **sanitary** skeleton, second tagged system) | geometry-inferred | none queryable (catalogue downloads sit behind an agree-gate) | Open Data – Chilliwack |
| **Port Coquitlam, BC** | `services9.arcgis.com/nz97KciUs5nOw64q` ([data-poco.hub.arcgis.com](https://data-poco.hub.arcgis.com)) | `Drainage_Network/0` StmMains (From/To_Elev_m inverts, **sentinel -99**; STRING Diameter_mm) · `5` StmManholes (Rim_Elev_m) · `6` StmBasins seeds · `Sanitary_Network2/0` SanMains (separated **sanitary** skeleton, second tagged system) | geometry-inferred | `Cadastral/0` · `Buildings/0` | Open Data – Port Coquitlam |
| **Vancouver, BC** | `maps.vancouver.ca/server/rest/services` (VanMap public) + [opendata.vancouver.ca](https://opendata.vancouver.ca) | `Hosted/swGravityMain/11`, `eflnttype IN ('Storm','Combined')` + `In Service` (diameter mm, slope %, material; **Combined joins the storm system**, ADR 0020) · **as-built UPSTREAM/DWNSTREAM inverts** from `VanMapViewer/Infrastructure_Sewer` layers 36/37 (join `COV_SOURCE_KEY`=facilityid; city's `..._ESTIMATED` flags kept; 0 = missing sentinel) · `Hosted/swManhole/12` rimelev → fallback inverts + max depths · layer 35 `Sanitary` (separated **sanitary** skeleton, second tagged system) | explicit manhole IDs (frommh/tomh) | Open data: `sewer-catch-basins` · `property-parcel-polygons` · `building-footprints-2015` | OGL – Vancouver (open-data layers); VanMap services published `access=public` |

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
