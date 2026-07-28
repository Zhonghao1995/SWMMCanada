# Whitby fixtures

Recorded **2026-07-28** from `services5.arcgis.com/ATdLnvuMRJk8AGkQ/.../WhitbyStormLines/FeatureServer/0`
(`f=geojson`, WGS84, pagination OK). Whitby publishes ONE storm layer and nothing else.

## Sub-bbox (EPSG:4326): `-78.960, 43.870, -78.935, 43.888` — central Whitby (~2 km)

| file | n | notes |
|---|---|---|
| `mains.geojson` | 731 | `UP_INV`/`DOWN_INV` inverts on 186/731 (31% — matches city-wide; 0 = missing); `FR/TO_NODE` ids 729/731, typed by prefix (ST/CB/JX); `DIAM` mm; `DRAIN_AREA`+`CO_EFF` per pipe (unconsumed calibration bonus) |

No node layer, no rims (default depths), no sanitary (Durham Region asset), no
parcels/buildings. Catch-basin seeds are extracted from pipe endpoints whose node id
starts with `CB`.
