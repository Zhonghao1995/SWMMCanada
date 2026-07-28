# District of North Vancouver fixtures

Clipped **2026-07-28** from the live SHP dumps on `geoweb.dnv.org/Products/Data/SHP/`
(EPSG:26910, reprojected; download-and-cache source, actively refreshed by the District).

## Sub-bbox (EPSG:4326): `-123.045, 49.330, -123.020, 49.348` — Lynn Valley (~2 km)

| file | dump | n |
|---|---|---|
| `mains.geojson` | `StmMain_shp.zip` | 2289 |
| `sanitary_mains.geojson` | `SanMain_shp.zip` | 900 |

Field notes: `UP_ELEV`/`DN_ELEV` inverts are STRINGS with the **-99** sentinel (1008/2289
real here — 44%), `AM_SIZE` string mm, spelled-out `AM_MATERIA` ("NON REINF CONC"). No
node ids; no rims (the fitting dump publishes structure inverts but no rim, and its type
domain arrives as bare codes — no seeds consumed, junction delineation). Mountain relief:
fixture inverts span 20-283 m and the creeks fragment the graph into many per-component
sinks — expected for the North Shore.
