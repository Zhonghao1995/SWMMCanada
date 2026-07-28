# Peterborough fixtures

Recorded **2026-07-28** from `citymaps.peterborough.ca/arcgis/rest/services/SanStormExternal/MapServer`
(`f=geojson`, WGS84, `resultOffset` pagination, `maxRecordCount` 1000).

## Sub-bbox (EPSG:4326): `-78.330, 44.297, -78.318, 44.306` — central Peterborough (~1 km)

| file | layer | where | n |
|---|---|---|---|
| `mains.geojson` | `18` Storm Gravity Main | `WATERTYPE='SW'` | 815 |
| `manholes.geojson` | `13` Storm Manhole | — | 418 |
| `outfalls.geojson` | `11` Storm Discharge Point | — | 34 |
| `inlets.geojson` | `12` Storm Inlet | — | 400 |
| `sanitary_mains.geojson` | `5` San Gravity Main | — | 224 |
| `sanitary_manholes.geojson` | `2` San Manhole | — | 187 |

Saskatoon/Toronto-family schema: `UPELEV`/`DOWNELEV` inverts (729/815 here), numeric
`FROMMH`/`TOMH` ids (MH-prefixed as labels), `DIAMETER` mm, `MATERIAL` codes, `RIMELEV`
on manholes (388/418). No parcel/building layers on this host.
