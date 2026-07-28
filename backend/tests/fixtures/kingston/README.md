# Kingston fixtures

Recorded **2026-07-28** from the city's DCAT-published utility.arcgis.com proxy services
(one hash per service; token-free despite appearances). `f=geojson`, WGS84, `resultOffset`
pagination.

## Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`)

**`-76.570, 44.255, -76.560, 44.263`** — Cataraqui West suburbs (~800 m; 55% invert
coverage vs 18-42% downtown — Kingston's inverts thin toward the old town).

| file | service | where | n |
|---|---|---|---|
| `mains.geojson` | `Eng/Storm_Pipe/FeatureServer/0` | `CONSTRUCTION_STATUS='Constructed'` | 187 |
| `inlets.geojson` | `Eng/Storm_Inlet/FeatureServer/0` | — | 80 |
| `buildings.geojson` | `Buildings/FeatureServer/1` | — | 299 |

Field notes: pipes carry `UPSTREAM/DOWNSTREAM_INVERT` (m AMSL) with a **bimodal sentinel**:
city-wide, 6,018 rows sit at real 60-200 m vs 467 placeholder rows at <= 2 m (Lake Ontario
is ~74.5 m) — the adapter's (60, 200) band screens them. Three node-id families label the
ends: `…_MANHOLE_ID` (MHS-…), `…_INLET_ID` (CB-…), `DOWNSTREAM_OUTLET_ID` (outfall
candidates). Storm Manhole layer publishes NO elevations (not fetched); the Parcel
MapServer refuses anonymous spatial queries (parcels stay empty); no sanitary network is
published. `CONSTRUCTION_STATUS` vocabulary: Constructed / Removed / Retired / Approved
for Construction.
