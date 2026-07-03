# Kitchener / Waterloo / Cambridge / Region of Waterloo storm fixtures

Small REAL extract from the Region of Waterloo open-data ArcGIS org, used by the offline
`kitchener` adapter tests. Captured **2026-06-22**.

## Source (hosted FeatureServer org)

Org root: `https://services1.arcgis.com/qAo1OsXi67t7XgmS/arcgis/rest/services`
All layers serve `f=geojson` directly (supportedQueryFormats: `JSON, geoJSON, PBF`), so the
fixtures are plain GeoJSON FeatureCollections — no Esri-JSON conversion needed.

| File | Layer | Notes |
|------|-------|-------|
| `pipes.geojson` | `Storm_Pipes/FeatureServer/0` | `UP_INVERT`/`DN_INVERT` (m), `UP_STMMANHOLEID`/`DN_STMMANHOLEID` (int, `-1` = no manhole), `WIDTH`/`HEIGHT` (mm), `PIPE_SHAPE`, `MATERIAL`, `LENGTH`, `OWNERSHIP` |
| `manholes.geojson` | `Storm_Manholes/FeatureServer/0` | keyed by `STMMANHOLEID`; `COVER_ELEVATION` (rim) -> ground points. Fetched BY id (the IN-list join), not by bbox. |
| `outlets.geojson` | `Storm_Outlets/FeatureServer/0` | keyed by `STMOUTLETID`; `PIPE_INVERT`. Used as outfall points. |
| `catchbasins.geojson` | `Storm_Catchbasins/FeatureServer/0` | keyed by `STMCATCHBASINID` (subcatchment seeds). |
| `buildings.geojson` | `Building_Outlines/FeatureServer/0` | polygons (impervious roofs for ADR 0005). |
| `sanitary_pipes.geojson` | `Sanitary_Pipes/FeatureServer/0` | captured **2026-07-03**, `where=STATUS='ACTIVE' AND CATEGORY='GRAVITY'` (drops FORCEMAIN / SLUDGE FORCEMAIN / STUB / SYPHON). 128 lines, audit fields stripped. Same schema as `Storm_Pipes` but keyed `UP_SANMANHOLEID`/`DN_SANMANHOLEID`, so the sanitary fetch passes NO manholes/outlets — endpoints take the polyline-vertex fallback (line geometry coincides with node points in this org). Second tagged system (ADR 0011). |

## Sub-bbox (EPSG:4326)

`-80.4925, 43.4385, -80.4810, 43.4445` — a ~1.2 km x 0.7 km slice of central Kitchener around
storm outlets 700210/700211, within the allowed downtown window
(lon -80.56..-80.38, lat 43.36..43.50).

## Counts

55 pipes / 60 manholes (all referenced ids resolved) / 29 outlets / 60 catch basins / 40 buildings.

## Topology & data notes

* **Explicit integer-id topology**: `UP_STMMANHOLEID`/`DN_STMMANHOLEID` join to `STMMANHOLEID`.
  A value of `-1` (the universal sentinel: 1508 DN / 1031 UP citywide) means the end is NOT a
  manhole — it drains to an outlet/catch basin. 13 of the 55 fixture pipes have such a dangling
  end. Geometry is consistent — `line[0]` coincides (0.00 m) with the upstream manhole and
  `line[-1]` with the downstream manhole — so dangling ends fall back to the polyline vertex.
* **Inverts are REAL**: 55/55 fixture pipes have both `UP_INVERT` and `DN_INVERT` populated
  (e.g. 320-340 m). No inverts are synthesized.
* **4 outlets coincide (<2 m) with a pipe endpoint** -> those become direct SWMM outfalls.
* `OWNERSHIP` spans KITCHENER / WATERLOO / CAMBRIDGE / REGION (one feed = whole region).
* **No parcel polygons** exist in this org (`Property_Ownership_Public` is POINT geometry), so the
  adapter returns `parcels: []` and subcatchment delineation falls back to catch-basin Voronoi.
