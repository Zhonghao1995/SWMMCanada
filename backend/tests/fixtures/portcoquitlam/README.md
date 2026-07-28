# Port Coquitlam fixtures

Recorded **2026-07-28** from the city AGOL org (`services9.arcgis.com/nz97KciUs5nOw64q`,
data-poco.hub.arcgis.com), `f=geojson`, WGS84, pagination OK.

## Sub-bbox (EPSG:4326): `-122.785, 49.255, -122.765, 49.270` — downtown PoCo (~1.6 km)

| file | service/layer | n |
|---|---|---|
| `mains.geojson` | `Drainage_Network/0` StmMains | 615 |
| `manholes.geojson` | `5` StmManholes | 556 |
| `basins.geojson` | `6` StmBasins | 903 |
| `sanitary_mains.geojson` | `Sanitary_Network2/0` SanMains | 333 |
| `buildings.geojson` | `Buildings/0` | 843 |
| `parcels.geojson` | `Cadastral/0` | 964 |

Field notes: `From/To_Elev_m` inverts (409/615 here; **sentinel -99**), STRING
`Diameter_mm`, no node ids. Manholes: `Rim_Elev_m` (485/556) + `Bottom_Elev_m`
(deliberately unread). Coverage box NESTS inside Coquitlam's.
