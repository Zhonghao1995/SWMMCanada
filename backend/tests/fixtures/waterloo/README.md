# Waterloo fixtures

Recorded **2026-09-18** from the City of Waterloo AGOL org
(`services.arcgis.com/ZpeBVw5o1kjit7LT`, [data.waterloo.ca](https://data.waterloo.ca)) through
the adapter's own fetch functions, `f=geojson` (WGS84, `resultOffset` pagination, layer
`maxRecordCount` 1000). Licence: Waterloo Open Data User Licence (OGL family). Properties are
trimmed to the fields below; coordinates rounded to 7 dp (~1 cm).

## Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`)

**`-80.5270, 43.4625, -80.5190, 43.4680`** — Uptown Waterloo (~650 m x 610 m, dense urban,
Laurel Creek outlets).

| file | source layer | where | n |
|---|---|---|---|
| `mains.geojson` | `Stormwater_Gravity_Mains/0` | `ASSET_TYPE = 'Collector'` | 194 |
| `manholes.geojson` | `Stormwater_Manholes/0` | — | 138 |
| `outfalls.geojson` | `Stormwater_Outlets/0` | — | 6 |
| `catchbasins.geojson` | `Stormwater_Catch_Basins/0` | — | 180 |
| `sanitary_mains.geojson` | `Sanitary_Sewer_Gravity_Mains/0` | — | 117 |
| `parcels.geojson` | `Property_Fabric/1358` | ROAD / RAILWAY parcels left out | 260 |
| `buildings.geojson` | `Buildings/0` | — | 251 |

Field notes:

* **Two missing-data sentinels** on `UPSTREAMINVERT`/`DOWNSTREAMINVERT`/`TOPOFMH`: a literal
  `0` and `111.111` (city-wide 2,926 and 2,734 storm lines; `111.111` is the only value
  anywhere between 0 and 250). Real inverts run 304.7–~405 m, rims 309.5–407.4 m. Here: 34
  pipe ends carry a sentinel, 128/138 manholes have an in-band rim.
* The mains layer mixes `ASSET_TYPE`s: Collector (9,890 city-wide, 87% with both inverts) /
  Catchbasin Lead (6,370, 15%) / Culvert / Ditch / Track + Trench Drain. Only collectors are
  fetched.
* Geometry is the topology authority: `line[0]` is the FROM (upstream) end in 698/698
  id-joined collectors checked live, and endpoints coincide with their manholes (median
  0.00 m). `FROM_ASSET_ID`/`TO_ASSET_ID` are null on ~20% of collectors. Manhole `ENTNAME`
  (e.g. `L55L-6`) labels the nodes.
* Storm mains carry `HEIGHT`/`WIDTH` (mm; equal for circular) + `CROSSSECTIONSHAPE`
  (5 Elliptical here); sanitary mains carry `DIAMETER` (mm). `MATERIAL` is spelt out
  ("Polyvinyl Chloride", and "Vitified Clay" misspelt at source).
* Measured on this AOI: 25 of 231 nodes gap-filled (10.8% → tier B); holdout MAE of
  estimated inverts 0.55 m (10% mask, 5 seeds).
