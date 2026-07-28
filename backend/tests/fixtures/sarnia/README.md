# Sarnia fixtures

Recorded **2026-07-28** from `services1.arcgis.com/ICybsLmBXrZCZV3x` (one FeatureServer per
dataset; `f=geojson`, WGS84, pagination OK).

## Sub-bbox (EPSG:4326): `-82.415, 42.960, -82.390, 42.978` — downtown Sarnia (~2 km)

| file | service/layer | where | n |
|---|---|---|---|
| `mains.geojson` | `Storm_Sewers_Open_Data/1` | `Lifecycle_Status='Active'` | 429 |
| `catchbasins.geojson` | `Catch_Basins_Open_Data/1` | — | 1397 |
| `sanitary_mains.geojson` | `Sanitary_Sewers_Open_Data/0` | Active | 698 |
| `buildings.geojson` | `Buildings_Open_Data/2` | — | 1788 (clipped to the inner half to keep the fixture lean) |

Field notes: `UpStreamIn`/`DownStream` inverts (380/429 here; 0 = missing, one junk ~108 m
row exists city-wide -> (150,250) band), `MH_Upstream/Downstream` id labels, `Diam_m` TEXT
mm, spelled-out `Material`. No manhole layer with elevations (default depths), no parcels.
