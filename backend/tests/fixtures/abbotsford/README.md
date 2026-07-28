# Abbotsford fixtures

Recorded **2026-07-28** from the City of Abbotsford AGOL org
(`services8.arcgis.com/ZYlQy38aWlfDG1Qh`), `f=geojson` (WGS84, `resultOffset` pagination,
`maxRecordCount` 2000). Everything lives in ONE monolithic
`Engineering_Layers_External_Feature` FeatureServer — layer IDs matter.

## Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`)

**`-122.315, 49.030, -122.303, 49.040`** — central-west Abbotsford residential (~1 km).

| file | layer | where | n |
|---|---|---|---|
| `mains.geojson` | `207` Drainage Mains | `LIFECYCLE_STATUS=0` (Active) | 214 |
| `manholes.geojson` | `204` Drainage Manholes | `LIFECYCLE_STATUS=0` | 132 |
| `outlets.geojson` | `198` Drainage Outlets | — | 4 |
| `catchbasins.geojson` | `205` Drainage Catchbasins | — | 228 |
| `sanitary_mains.geojson` | `214` Sanitary Mains | `LIFECYCLE_STATUS=0` | 123 |
| `sanitary_manholes.geojson` | `212` Sanitary Manholes | `LIFECYCLE_STATUS=0` | 94 |
| `parcels.geojson` | `Parcel_Layers_External_Feature/0` Parcels | — | 330 |

Field notes: attributes are CODED DOMAINS — `LIFECYCLE_STATUS` 0=Active, `MATERIAL` integer
codes (0=PVC, 1=Concrete, 2=Vit. Clay, 3=AC, 5=Corrugated Steel, 6=HDPE, 7=DI, 8=Steel,
11=CIPP, 13=PVCO; domain recorded 2026-07-28). `UPSTREAM/DOWNSTREAM_INVERT` use BOTH `0`
and `-1` as missing sentinels (185/214 storm rows here have real up-inverts).
`UPLINK`/`DOWNLINK` are link-id node labels with the literal string `'N/A'` for absent.
`RIM_ELEVATION` on manholes (118/132 here). No public building-footprint layer exists.
