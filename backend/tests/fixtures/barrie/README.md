# Barrie fixtures

Recorded **2026-07-28** from `gispublic.barrie.ca/arcgis/rest/services/Open_Data` with
`f=geojson` (WGS84, real geometry, `resultOffset` pagination; `maxRecordCount` 1000).

## Sub-bbox (EPSG:4326 `min_lon,min_lat,max_lon,max_lat`)

**`-79.700, 44.385, -79.688, 44.394`** — downtown Barrie / Kempenfelt Bay shore (~1 km).

| file | source layer | where | n |
|---|---|---|---|
| `mains.geojson` | `StormInfrastructure/1` Storm Linear | `TYPE IN ('LOCAL','TRUNK','CULVERT','ENTRANCE CULVERT') AND STATUS='ACTIVE'` | 295 |
| `devices.geojson` | `StormInfrastructure/0` Storm Device | — | 841 |
| `sanitary_mains.geojson` | `SanitaryInfrastructure/2` Sanitary Pipe | `TYPE IN ('LOCAL','TRUNK') AND STATUS='ACTIVE'` | 251 |
| `sanitary_devices.geojson` | `SanitaryInfrastructure/1` Sanitary Device | — | 257 |
| `parcels.geojson` | `ParcelPublishing/2` Parcel | — | 736 |
| `buildings.geojson` | `FacilitiesStreets/36` Buildings | — | 946 |

Field notes: linear layers (storm and sanitary share one schema) carry `INV_UP_ELV`/
`INV_DN_ELV` (pipe-end inverts, 249/295 storm rows here), `FROM_ID`/`TO_ID` (asset-id node
labels), `PIPESHP` (CIRCULAR/ARCH/CLOSED_RECT) with `WIDTH`/`HEIGHT` in mm. Devices carry
`TOPELEV` (rim; 328/841 populated here — catch basins often lack it) and a rich `TYPE`
vocabulary: OUTFALL/HEADWALL/OUTLET STRUCTURE… are outfall candidates, the CATCH BASIN
family seeds subcatchments. Open-channel linear TYPEs (WATERCOURSE/DITCH/SWALE) carry null
inverts by design and are excluded by the where-clause. Sanitary `TYPE='FORCE'` excluded.
