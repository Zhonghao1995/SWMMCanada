# Coquitlam fixtures

Recorded **2026-07-28** from the City of Coquitlam AGOL org
(`services2.arcgis.com/Q6Lq3evZUGfPrN7o`) with `f=geojson` (WGS84, real geometry,
`resultOffset` pagination; layer `maxRecordCount` 2000). Licence: Open Government
Licence – Coquitlam (data.coquitlam.ca).

## Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`)

**`-122.800, 49.272, -122.788, 49.281`** — Coquitlam Town Centre (~900 m, dense urban).

| file | source layer | where | n |
|---|---|---|---|
| `mains.geojson` | `Drainage Utility/16` Drainage Mains | `STATUS='OPERATING'` | 137 |
| `manholes.geojson` | `Drainage Utility/6` Drainage Manholes | — | 131 |
| `outfalls.geojson` | `Drainage Utility/10` Drainage Outfalls | — | 1 |
| `catchbasins.geojson` | `Drainage Utility/11` Drainage Catchbasins | — | 292 |
| `sanitary_mains.geojson` | `Sanitary Utility/10` Sanitary Mains | `STATUS='OPERATING'` | 118 |
| `sanitary_manholes.geojson` | `Sanitary Utility/0` Sanitary Manholes | — | 75 |
| `parcels.geojson` | `Cadastral/13` Parcels | — | 56 |
| `buildings.geojson` | `Cadastral/15` Buildings | — | 131 |

Field notes: storm mains carry `UP/DN_ELEVATION` (pipe-end inverts, 126/137 > 0 here) and
`UP/DN_TERM_TYPE`+`UP/DN_TERM_ID` (termination ids — used as node labels, not topology);
sanitary mains use `UP/DN_ELEV` + `DIAMETER`. Manhole rims: `RIM_ELEVATION` (storm,
131/131 here) / `RIM_ELEV` (sanitary). The service name contains a space
(`Drainage%20Utility` in URLs). `STATUS` vocabulary: OPERATING / MOT / METRO / PRIVATE /
ABANDONED / DECOMMISSIONED / NOT READY.
