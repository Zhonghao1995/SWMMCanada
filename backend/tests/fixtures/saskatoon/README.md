# Saskatoon fixtures

Recorded **2026-07-28** from `gisext.saskatoon.ca` with `f=geojson` (WGS84, `resultOffset`
pagination, `maxRecordCount` 2000). Network layers come from the public token-free
`Core/WSSTreatment_AGOL` MapServer (NOT the official `OD` open-data folder — provenance
recorded in DATA.md); parcels from the official `OD/LandSurface` open-data MapServer.

## Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`)

**`-106.670, 52.123, -106.660, 52.131`** — downtown Saskatoon (~800 m).

| file | layer | where | n |
|---|---|---|---|
| `mains.geojson` | `Core/WSSTreatment_AGOL/5` Storm Main | `STATUS='A1' AND PIPETYPE IN ('Main','Trunk','Bypass Main')` | 162 |
| `manholes.geojson` | `6` Storm Manhole | `STATUS='A1'` | 129 |
| `catchbasins.geojson` | `7` Storm CatchBasin | `STATUS='A1'` | 195 |
| `sanitary_mains.geojson` | `1` Sanitary Main | same PIPETYPE filter | 129 |
| `sanitary_manholes.geojson` | `2` Sanitary Manhole | `STATUS='A1'` | 111 |
| `parcels.geojson` | `OD/LandSurface/1` City of Saskatoon - Parcel | — | 245 |

Field notes: mains carry `UPELEV`/`DOWNELEV` (pipe-end inverts, 149/162 storm rows here;
join verified: a pipe's UPELEV equals its FROMMH manhole's INVERTELEV) + `FROMMH`/`TOMH`
node ids (numeric; prefixed `MH` as node labels) + `DIAMETER` mm + `MATERIAL` codes
(CP/CT/PVC...). Manholes: `RIMELEV` (121/129 here). `STATUS` A1=active, D3=decommissioned;
Catch Basin Leads and Subdrainage Mains are separate PIPETYPEs, excluded. No explicit
outfall layer exists — per-component sinks stand in. No public building footprints.
