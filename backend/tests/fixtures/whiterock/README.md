# White Rock fixtures

Recorded **2026-07-28** from `maps.whiterockcity.ca/server/rest/services/opendata`
(one MapServer per dataset, layer 0; `f=geojson`, WGS84, pagination OK).

## Sub-bbox (EPSG:4326): `-122.815, 49.018, -122.800, 49.028` — central White Rock (~1 km)

| file | service | where | n |
|---|---|---|---|
| `mains.geojson` | `Storm_Lines` | `Line_Type IN ('Pipe','Pi_Dc')` | 616 |
| `manholes.geojson` | `Storm_Manholes` | — | 854 |
| `sanitary_mains.geojson` | `Sanitary_Lines` | `Line_Type='Gravity'` | 358 |
| `parcels.geojson` | `Parcel` | — | 819 |
| `buildings.geojson` | `Building_Outlines` | — | 889 |

Field notes: shapefile-truncated names — `Us/Ds_Inv_Ele` inverts (405/616 here, 0 =
missing) AND `Us/Ds_Rm_E` rims on the same pipe row (rims-on-row); SmallInteger
`Us/Ds_End_Id` node ids (0 = absent; MH-prefixed as labels); `Us_Pipe_Si` mm;
`Us_Pipe_Ty` codes (CO = concrete). Storm `Line_Type` vocabulary: Pipe / Pi_Dc / Dtch /
Cree / Abnd; sanitary: Gravity / Force / Aband. No catch-basin layer exists — Storm
Manholes seed subcatchments instead.
