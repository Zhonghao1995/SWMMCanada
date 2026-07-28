# Windsor fixtures

Clipped **2026-07-28** from the live `Sewer Pipeline.zip` dump on opendata.citywindsor.ca
(EPSG:26917 shapefile, reprojected to WGS84; the wave's first download-and-cache source —
no query API exists).

## Sub-bbox (EPSG:4326): `-83.045, 42.300, -83.015, 42.325` — downtown Windsor (~2.5 km)

| file | filter | n |
|---|---|---|
| `mains.geojson` | `Sewer_Type IN ('STORM','COMBINED')` | 1514 |
| `sanitary_mains.geojson` | `Sewer_Type='SANITARY'` | 207 |

Field notes (DBF-truncated names): `Upstream_E`/`Downstre_1` inverts ((150,220) m band;
0 sentinel ~14% of storm rows; deep combined interceptors dip to ~164 m under the Detroit
River), `Upstream_M`/`Downstream` node-id labels, `Pipe_Size` mm, `Pipe_Type` (RCP →
concrete), `Pipe_Shape`. Downtown is heavily combined (1060 COMBINED vs 454 STORM).
ABANDONED/PRIVATE rows excluded. No point layers consumed (junction delineation).
