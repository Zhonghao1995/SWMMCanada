# London (Ontario) storm-sewer fixtures (real data, captured from maps.london.ca)

Captured **2026-06-22**. All geometry EPSG:4326 (lon,lat). Metric CRS for the city is UTM 17N (`EPSG:32617`).

Service (network): `https://maps.london.ca/server/rest/services/OpenData/OpenData_Environment/MapServer`
Layers: Catch Basins=1, Manholes=2, Sewer Other Nodes=3, Sewer Outfalls=4, **Sewer Pipes=5**.
Service (land):    `https://maps.london.ca/server/rest/services/OpenData/OpenData_BaseMaps/MapServer`
Layers: Buildings=3, Parcels=53.

`f=geojson` works on every layer (same Esri "OpenData" vendor as Victoria), so the adapter fetches GeoJSON
directly; no `f=json` + `esri_to_geojson` conversion is needed in production. A raw `f=json` mains page is kept
only to exercise the fetch-client parse/pagination path in tests.

Sub-bbox (EPSG:4326): **(-81.260, 42.980, -81.254, 42.985)** — a ~600 m downtown core around Richmond St,
chosen so the network drains to real STM outfalls (the 6G/7M/8K outfall cluster by the Thames).

## Files (GeoJSON FeatureCollections; `properties` = raw ArcGIS attributes)
- `mains.geojson`       — 44 STM sewer pipes (LineString). `where=FlowType='STM'`. Source of truth for topology + hydraulics.
- `manholes.geojson`    — 43 referenced manholes (layer 2, Point). Carry ground `LidElevation` (18/43 populated).
- `other_nodes.geojson` — 3 referenced "Sewer Other Nodes" (layer 3, Point). Geometry only (supplement node coords).
- `outfalls.geojson`    — 6 referenced STM outfalls (layer 4, Point). `PipeInvert`/`HeadwallElevation` are 0 here (unpopulated) → outfall inverts are gap-filled from connected pipe ends.
- `catchbasins.geojson` — 50 catch basins (layer 1, Point). NB: layer 1 has NO `GIS_FeatureKey` — only `OBJECTID`/`GIS_ID`.
- `parcels.geojson`     — 87 parcels (BaseMaps layer 53, Polygon).
- `buildings.geojson`   — 89 buildings (BaseMaps layer 3, Polygon).
- `raw_arcgis_mains_query.json` — one raw ArcGIS `f=json` mains response (Esri JSON; for the fetch-client parse test).

### Sanitary skeleton (ADR 0011), captured 2026-07-03 over the same bbox
The SAME Sewer Pipes layer carries the sanitary system; unlike the storm where-clause,
`ConstructedStatus` matters here — the layer also holds Abandoned/Proposed/Removed SAN lines
(~40% over this bbox). Captured with the adapter's where-clause
`FlowType='SAN' AND ConstructedStatus='Built'`, plus the joined node layers (same
`GIS_FeatureKey` join as storm); volatile audit fields stripped.
- `sanitary_mains.geojson`       — 54 built SAN sewer pipes (LineString).
- `sanitary_manholes.geojson`    — 54 referenced manholes (Point, `LidElevation`).
- `sanitary_other_nodes.geojson` — 3 referenced other nodes (Point).
- `sanitary_outfalls.geojson`    — 1 referenced outfall (Point).

## Join / topology contract
- **EXPLICIT topology.** A pipe's `UpstreamID` / `DownstreamID` (string, e.g. `8G24`) match a node's **`GIS_FeatureKey`**.
- A node may live in any of three layers: Manholes(2), Sewer Other Nodes(3), Sewer Outfalls(4). The adapter
  collects referenced ids and fetches each layer by `GIS_FeatureKey IN (...)`.
- `Upstream/DownstreamInventoryType` tags each endpoint (`MH`, `OF`, `CBM`, `CB`, `TEE`, `CAP`, `RED`, ...) — recorded in diagnostics, not used for the join.
- Outfalls are detected by membership in the outfall layer (layer 4), NOT by an id prefix.
- Dangling refs (id absent from every node layer) do occur city-wide (~1.5% in the broader downtown); this clean
  window happens to resolve 100%, so dangling/blank-id handling is covered by synthetic unit tests instead.

## Key pipe fields (mains.geojson properties)
`FlowType` (filter `'STM'`; `'SAN'`=sanitary excluded), `UpstreamID`, `DownstreamID`,
`UpstreamInvert` (m), `DownstreamInvert` (m) — **both well populated** (42/44, 43/44 here; the 1-2 gaps are gap-filled),
`Diameter` (mm; the city's circular-equivalent, used directly even for EGG shapes),
`PipeShape` (`R`/`Round`=circular, `EGG`=egg, ...), `Height`/`Width` (mm), `Material`, `Length` (m).

## Node fields
Manholes: `GIS_FeatureKey`, `FlowType`, `LidElevation` (m, ground rim).
Sewer Other Nodes: `GIS_FeatureKey`, `FlowType` (geometry only).
Outfalls: `GIS_FeatureKey`, `FlowType`, `PipeInvert`, `HeadwallElevation` (0/unpopulated here).
Catch basins: `OBJECTID`, `GIS_ID`, `SWMFacilityID` (no `GIS_FeatureKey`).

## Material codes seen here (-> Manning's n via cities.base.material_roughness)
`CONC` (0.013, concrete), `PVC` (0.010), `VIT` (vitrified clay), `BRCK` (brick), `None`/`?` (-> default 0.013).
NB London uses `VIT`/`BRCK`, not Victoria's `VITC`/`BRK`; the adapter normalises these to the shared table.
