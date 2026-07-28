# Esquimalt fixtures

Recorded **2026-07-28** from `gis.esquimalt.ca/arcgis/rest/services/Services` (on-prem
ArcGIS Server; `f=geojson`, WGS84, `maxRecordCount` 1000, pagination OK).

## Sub-bbox (EPSG:4326): `-123.416, 48.428, -123.401, 48.439` — central Esquimalt (~1 km)

| file | service/layer | n |
|---|---|---|
| `mains.geojson` | `Drain/4` Drain Mains | 347 |
| `manholes.geojson` | `Drain/2` Drain Manholes | 280 |
| `catchbasins.geojson` | `Drain/0` Drain Catch Basin | 481 |
| `outfalls.geojson` | `Outfalls/0` | 0 in this AOI (23 township-wide, shoreline) |
| `sanitary_mains.geojson` | `Sewer/5` Sewer Mains | 263 |
| `sanitary_manholes.geojson` | `Sewer/4` Sewer Manholes | 236 |
| `parcels.geojson` | `Cadastre/0` Parcel (attributes slimmed) | 1164 |
| `buildings.geojson` | `Buildings_EOC/0` (attributes slimmed) | 1713 |

Field notes: drain mains carry NO elevations — the manholes publish them BY COMPASS WALL
(`NORTH/SOUTH/EAST/WEST_INVERT` + `CENTER_INVERT`) plus `RIM_ELEVATION`; the join key is
the manhole layer's `ID` (`DMH…`) matched from the pipes' `UPSTREAM/DOWNSTREAM_MANHOLE`
(`END`/`OUT` = dead end/outfall). Pipe ends are drawn to the chamber walls, so ends at
one manhole sit ~1-1.5 m apart — beyond the 1 m snap — and most id labels legitimately
drop to generated names. Sanitary mains are richer: `UPSTREAM/DOWNSTREAM_ELEVATION` on
the rows (242/263 here) plus a `MANNINGS_NUMBER` column (unconsumed).
