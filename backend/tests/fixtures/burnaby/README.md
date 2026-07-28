# Burnaby fixtures

Recorded **2026-07-28** from `gis.burnaby.ca/arcgis/rest/services/OpenData` (`f=geojson`,
WGS84, `resultOffset` pagination, `maxRecordCount` 1000). OGL – Burnaby.

## Sub-bbox (EPSG:4326): `-123.005, 49.222, -122.993, 49.231` — Metrotown-north (~900 m)

| file | layer | where | n |
|---|---|---|---|
| `mains.geojson` | `OpenData2/18` Storm Main | `SERVSTAT='I' OR SERVSTAT IS NULL` | 215 |
| `catchbasins.geojson` | `OpenData2/19` Catchbasin | — | 399 |
| `sanitary_mains.geojson` | `OpenData2/10` Sanitary Main | same | 166 |
| `parcels.geojson` | `OpenData4/7` Legal Parcels | — | 268 |
| `buildings.geojson` | `OpenData4/18` Building Outlines | — | 288 |

Field notes: mains carry `UPSELEV`/`DWNELEV` (inverts, 179/215 here, 0 = missing),
`UNITID`/`UNITID2` (DM… node labels), `PIPEDIAM` mm, `PIPETYPE` material, `PIPESHP`/`PIPEHT`
sections, `PIPELEN`. `SERVSTAT` vocabulary: I / NULL (live) vs RMVD / ABND / DRMV. Storm
Fitting publishes MHDPTH depth but NO rim elevation (not fetched — default max depths).
