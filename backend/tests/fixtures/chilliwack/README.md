# Chilliwack fixtures

Recorded **2026-07-28** from `maps.chilliwack.com/arcgis/rest/services/External` (the ROOT
services directory is empty — walk the External folder). WAF-fronted: bursty clients get
403s; recorded with small pages + backoff. `f=geojson`, WGS84.

## Sub-bbox (EPSG:4326): `-121.970, 49.155, -121.945, 49.172` — Chilliwack Proper (~2 km)

| file | service/layer | n |
|---|---|---|
| `mains.geojson` | `Dynamic_Utility/8` StormPipe | 973 |
| `symbols.geojson` | `Dynamic_Utility_Feature/5` StormSymbol | 2499 |
| `sanitary_mains.geojson` | `Dynamic_Utility/4` SanitaryPipe | 588 |

Field notes: `INVERT`/`INVERT_DOWN` inverts (723/973 here; 0 = missing; valley floor sits
~8-12 m), `PIPE_DIAMETER` mm, `MATERIAL` (CONC…), no node ids. The ONE StormSymbol layer
is typed by `SYM_TYPE`: MANHOLE/CB-family rows carry `RIM` (1098/2499 here) -> depths;
CATCHBASIN/CB/MH/LAWN BASIN seed subcatchments. No queryable parcels/buildings (the
catalogue's downloads sit behind an agree-gate).
