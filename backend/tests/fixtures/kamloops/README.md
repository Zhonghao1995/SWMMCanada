# Kamloops fixtures

Recorded **2026-07-28** from `maps.kamloops.ca/arcgis/rest/services/OpenData`
(`f=geojson`, WGS84, `maxRecordCount` 1000, pagination OK).

## Sub-bbox (EPSG:4326): `-120.345, 50.665, -120.325, 50.680` — Sahali/downtown (~1.6 km)

| file | service/layer | n |
|---|---|---|
| `mains.geojson` | `OpenDataDrainEmerGeo/12` DGravityMain | 1064 |
| `manholes.geojson` | `14` DManhole | 303 |
| `outlets.geojson` | `16` DOutlet | 21 |
| `catchbasins.geojson` | `2` DCatchBasin | 583 |
| `sanitary_mains.geojson` | `OpenDataSanitaryTel/12` SGravityMain | 623 |
| `sanitary_manholes.geojson` | `13` SManhole | 435 |
| `buildings.geojson` | `OpenDataPlanimetric/39` Building | 1463 |

Field notes: **the missing sentinel is the literal 9999** on UPSTREAM/DOWNSTREAMINVERT
and RIMELEVATION (1028/1064 mains here carry real inverts, 296/303 manholes real rims);
no node ids (endpoint snapping); MATERIAL uses 'CNC' for concrete. No parcel polygons in
the open catalogue.
