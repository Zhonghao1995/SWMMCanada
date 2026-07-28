# Delta fixtures

Recorded **2026-07-28** from the City of Delta AGOL org (`services9.arcgis.com/w2mu7sRltY6PiQ7J`,
opendata-deltabc.hub.arcgis.com), `f=geojson`, WGS84, pagination OK.

## Sub-bbox (EPSG:4326): `-122.920, 49.140, -122.900, 49.155` — North Delta (~1.6 km)

| file | service | n |
|---|---|---|
| `mains.geojson` | `Drainage_Mains` | 674 |
| `sanitary_mains.geojson` | `Sanitary_Gravity_Mains` | 488 |
| `parcels.geojson` | `Property_Parcels` (attributes slimmed) | 3155 |

Field notes: rows carry `START/END_IL` inverts (549/674 here) AND `START/END_GL` ground
levels (grounds-on-row — no manhole layer exists). **Missing sentinel = -99**; genuine
negative inverts exist near sea level, so the screen is `> -90`, never `> 0`. Vertical
datum per the city's ELEV_NOTE: CVD28GVRD2018 (no shim applied — recorded in DATA.md).
`START/END_NODE` fields exist but are empty (endpoint snapping). Sanitary uses
`START/END_INVELEV`. No catch-basin or building layers.
