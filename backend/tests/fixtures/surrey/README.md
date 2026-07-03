# Surrey storm-network fixtures

Small real capture from the **City of Surrey** open-data ArcGIS MapServer, used by
`tests/sources/cities/test_surrey_network.py` (offline). Surrey publishes inverts but **no node
ids on mains**, so topology is inferred from polyline endpoints (mirrors Ottawa).

## Source

- Service root: `https://gisservices.surrey.ca/arcgis/rest/services/OpenData/MapServer`
- Captured: **2026-06-22**
- Format: **`f=geojson`** — Surrey's MapServer returns real GeoJSON geometry directly, so no
  Esri-JSON→GeoJSON conversion was needed for capture. (The adapter keeps a
  `base.esri_to_geojson` fallback for any layer that ever serves Esri JSON only.)
- Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`): **`-122.825,49.118,-122.821,49.122`**
  (within the assigned Surrey window lon -122.85..-122.79, lat 49.10..49.14).

## Layers and filters

| file | layer | name | filter (`where`) | geom |
|------|-------|------|------------------|------|
| `storm_pipes.geojson` | 18 | Drn Mains | `MAIN_TYPE2='Gravity'` | polyline |
| `outfalls.geojson`    | 25 | Drainage Devices | `DEVICE_CLASSIFICATION='Outlet'` | point |
| `catchbasins.geojson` | 24 | Drainage Catch Basins | `1=1` | point |
| `manholes.geojson`    | 23 | Drainage Manholes | `1=1` | point |
| `sanitary_mains.geojson` | 41 | San Mains | `MAIN_TYPE2='Gravity' AND STATUS='In Service'` | polyline |

Counts: 35 gravity mains, 4 outfalls, 31 catch basins, 23 manholes, 16 sanitary mains
(manholes + sanitary captured **2026-07-03**, volatile audit fields stripped).

## Manholes (layer 23) → node max depths

`RIM_ELEVATION` (m AMSL, double) is 100% populated over this bbox (17.7–23.7 m) → node
ground elevation, so junction max depth becomes rim − invert instead of the 2 m assembler
default. Plausibility band 0.5–200 m (sea-level lowlands to ~134 m): unlike pipe inverts —
where 0 = sea level is a legitimate value — a 0.0 RIM is a missing-data placeholder, so the
band's lower edge screens it.

## San Mains (layer 41) → sanitary skeleton (ADR 0011)

Same publication schema as Drn Mains (`UP/DOWN_ELEVATION`, `MAIN_SIZE`, `MATERIAL`,
`SHAPE.LEN`, no node ids), so `build_surrey_network` assembles it unchanged as the second
tagged system. Unlike the storm layer, San Mains also carries `STATUS` Abandoned/Proposed
lines — the where-clause keeps only in-service gravity mains.

## Field mapping (Drn Mains, layer 18)

- `UP_ELEVATION` (m, double) → upstream invert; `DOWN_ELEVATION` (m) → downstream invert.
  **Both are populated** in Surrey's data (~97.5% non-null city-wide; 34/35 in this fixture —
  the one gap exercises `cities.base` invert gap-fill).
- `MAIN_SIZE` (mm, smallint) ÷ 1000 → diameter (m).
- `MATERIAL` (PVC, CP=concrete pipe, AC, CMP, PE, ...) → `base.material_roughness`
  (`CP` is not in the table → falls to the concrete default 0.013, which is correct).
- `SHAPE.LEN` → conduit length (m); `MAIN_SHAPE` kept in diagnostics only (builder is circular).
- `MAIN_TYPE2` → routed to gravity mains only (Culvert/Stub/Forcemain/Foundation Drain excluded).
- No node ids on mains → endpoints snapped to shared nodes by coordinate (`snap_decimals=5`).
