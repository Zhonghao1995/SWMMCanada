# Township of Langley fixtures

Recorded **2026-07-28** from the Township AGOL org (`services5.arcgis.com/frpHL0Fv8koQRVWY`,
data.tol.ca; NOT the separate City of Langley). `f=geojson`, WGS84, pagination OK.

## Sub-bbox (EPSG:4326): `-122.660, 49.128, -122.640, 49.143` — Willoughby (~1.6 km)

| file | service | where | n |
|---|---|---|---|
| `mains.geojson` | `Drainage_Pipes` | `Lifecycle_Status IN ('Asbuilt','Preliminary - Constructed')` | 479 |
| `manholes.geojson` | `Drainage_Manholes` | same | 207 |
| `catchbasins.geojson` | `Drainage_Sources` | — | 646 |
| `sanitary_mains.geojson` | `Sanitary_Pipes` | same | 117 |
| `sanitary_manholes.geojson` | `Sanitary_Manholes` | same | 106 |
| `parcels.geojson` | `Parcels` (attributes slimmed) | — | 2687 |

Field notes: `Upstream/Downstream_Elevation` inverts (446/479 here; nulls cluster at the
START of the table city-wide — never judge from page one), STRING `Diameter` ("250"), no
material field (default roughness), no node ids (endpoint snapping). Manholes:
`Manhole_RimElev` (169/207). No building footprints on the org.
