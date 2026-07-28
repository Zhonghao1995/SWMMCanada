# Strathcona County fixtures

Recorded **2026-07-28** from the County AGOL org (`services.arcgis.com/B7ZrK1Hv4P1dsm9R`),
`f=geojson`, WGS84, pagination OK. Field names are all-lowercase.

## Sub-bbox (EPSG:4326): `-113.330, 53.510, -113.305, 53.528` — Sherwood Park (~2 km)

| file | service | where | n |
|---|---|---|---|
| `mains.geojson` | `Storm_Gravity_Main` | `pipetype IN ('Collector','Transmission','Conduit','Culvert')` | 325 |
| `manholes.geojson` | `Storm_Manhole` | — | 279 |
| `outfalls.geojson` | `Storm_Discharge_Point` | — | 38 |
| `catchbasins.geojson` | `Storm_Catch_Basin` | — | 229 |
| `sanitary_mains.geojson` | `Waste_Water_Gravity_Main` | — | 535 |
| `sanitary_manholes.geojson` | `Waste_Water_Manhole` | — | 456 |
| `buildings.geojson` | `Building_Footprints` | — | 2735 |

Field notes: `upinvert`/`downinvert` inverts (164/325 here — the patchiest tier-2 source
of the wave, as the scan's adversarial verify warned; 0 = missing), `rimelev` rims
(118/279). The Discharge_Point layer mixes true outlets with INLET-side structures — 11
fixture points sat at the HIGH end of their pipe and are dropped by the adapter's
downhill-end filter. `pipetype` vocabulary: Collector/Transmission/Conduit/Culvert in;
Catchbasin Lead/SPDC/Pressurized out.
