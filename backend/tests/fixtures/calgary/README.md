# Calgary storm-network fixtures (real data, captured from the City of Calgary ArcGIS org)

Org root: `https://services1.arcgis.com/AVP60cs0Q9PEA8rH/arcgis/rest/services`
All layers are hosted **FeatureServer** services; fetched with `f=geojson` (geometry populated —
no Esri-JSON conversion needed in practice). All geometry EPSG:4326 (lon, lat). Captured **2026-06-22**.

## Sub-bbox
A ~450 m downtown box on the south bank of the Bow River (Eau Claire / Prince's Island area),
chosen because it has both a manageable pipe count and real river outfalls:

```
bbox (min_lon, min_lat, max_lon, max_lat) = (-114.083, 51.051, -114.077, 51.055)
```

## Layers / endpoints
- **Pipes**: `Storm_pipe_DMAP/FeatureServer/0` (layer `STORM_PIPE`, polyline).
  Fields used: `UP_INVERT`, `DN_INVERT` (m AMSL, doubles); `MATERIAL` (PVC/CON/...);
  `LENGTH` (m); `HEIGHT`/`WIDTH` (mm, equal for circular → `diameter_m = WIDTH/1000`); `SLOPE`.
  **No node ids** → topology inferred from polyline endpoints (coordinate snap ~1 m).
  Note: `0` is the missing-data sentinel for `UP_INVERT`/`DN_INVERT` (real inverts ≈ 1040–1047 m).
- **Outfalls**: `Storm_Inlet_Outfall_DMAP/FeatureServer/0` (layer `STORM_INLET_OUTFALL`, point).
  A feature is an **outfall** when `OUT_INLET` names a receiving water body (non-null, e.g.
  "BOW RIVER") — captured with `where=OUT_INLET IS NOT NULL`. `S_FUNCTION` is
  "OUTFALL STRUCTURE" / "FLOOD GATE OUTFALL". Inlets have a null `OUT_INLET`.
- **Catch basins**: `Storm_catch_basin_DMAP/FeatureServer/0` (id `ASSET_ID`, point).

## Files (GeoJSON FeatureCollections; `properties` = raw ArcGIS attributes)
- `storm_pipes.geojson` — 38 STORM_PIPE polylines (source of truth for topology + hydraulics).
- `outfalls.geojson`    — 4 outfall points (all to BOW RIVER); each snaps onto a pipe endpoint.
- `catchbasins.geojson` — 16 catch basins.

## Parcels / buildings (fetched live by `fetch_calgary_land`, not checked in)
Real **polygon** layers in the same org (verified 2026-06-22):
- parcels   = `Parcel_with_Roll_2026/FeatureServer/0` (`ROLL_CPID_2026`, full-coverage parcel
  polygons; ~4 957 in this bbox). **Not** `Parcel_Assessment` — that one is a *polyline* display
  layer. `Parcels_with_Building_Permits` is also a polygon layer but only covers permitted parcels
  (0 downtown), so the full-coverage roll layer is used instead.
- buildings = `Buildings_from_Digital_Aerial_Survey/FeatureServer/0` (`DAS_BUILDING` polygons).
