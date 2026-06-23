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

Counts: 35 gravity mains, 4 outfalls, 31 catch basins.

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
