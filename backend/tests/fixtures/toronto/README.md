# Toronto fixtures

Recorded **2026-07-28** from Toronto Water's official EXTERNAL VIEW feature services on the
city AGOL org (`services5.arcgis.com/MFwjjnaTnj9B3bil`, owner GCC_TWAGO — same data as the
open-data portal's daily-updated Sewer packages, with real spatial queries). `f=geojson`,
WGS84, `resultOffset` pagination, `maxRecordCount` 2000.

## Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`)

**`-79.394, 43.647, -79.386, 43.653`** — King West / Entertainment District (~700 m).

| file | service (`.../FeatureServer/0`) | where | n |
|---|---|---|---|
| `mains.geojson` | `COT_Geospatial_TW_Sewer_Gravity_Main_Ext_View` | `WATERTYPE IN ('Storm','Combined')` | 307 |
| `manholes.geojson` | `COT_Geospatial_TW_Sewer_Manhole_Ext_View` | — | 291 |
| `outfalls.geojson` | `COT_Geospatial_TW_Sewer_Discharge_Point_Ext_View` | — | 0 (downtown combined core drains to interceptors; 1,914 discharge points city-wide) |
| `inlets.geojson` | `COT_Geospatial_TW_Sewer_Inlet_Ext_View` | — | 350 |
| `sanitary_mains.geojson` | gravity mains | `WATERTYPE='SAN'` | 27 |

Field notes: mains carry `UPELEV`/`DOWNELEV` (pipe-end inverts, 270/307 here, 0 = missing)
+ `FROMMH`/`TOMH` (maintenance-hole id labels, `MH…`) + `DIAMETER` mm + `MATERIAL`
(CP/…). Manholes: `RIMELEV` (252/291 here). `WATERTYPE` vocabulary: Storm / Combined /
SAN / CSO / SCSO / EO / FD — the storm graph takes Storm+Combined (ADR 0021), sanitary
takes SAN, relief structures (CSO/SCSO/EO) and FD stay out. Downtown is 194 Combined /
113 Storm — a heavily combined core, like Ottawa/Vancouver.
