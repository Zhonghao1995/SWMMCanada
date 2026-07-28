# New Westminster fixtures

Recorded **2026-07-28** from the city AGOL org (`services3.arcgis.com/A7O8YnTNtzRPIn7T`,
opendata.newwestcity.ca), `f=geojson`, WGS84, `resultOffset` pagination.

## Sub-bbox (EPSG:4326): `-122.935, 49.203, -122.923, 49.212` — Uptown/Massey (~900 m)

| file | service | n |
|---|---|---|
| `storm_mains.geojson` | `Sewer_Stormwater_Gravity_Main` | 163 |
| `combined_mains.geojson` | `Sewer_Combined_Gravity_Main` | 276 |
| `storm_manholes.geojson` | `Sewer_Stormwater_Manhole` | 127 |
| `combined_manholes.geojson` | `Sewer_Combined_Manhole` | 225 |
| `inlets.geojson` | `Sewer_Stormwater_Inlets` | 364 |
| `sanitary_mains.geojson` | `Sewer_Sanitary_Gravity_Main` | 10 |
| `sanitary_manholes.geojson` | `Sewer_Sanitary_Manhole` | 18 |
| `parcels.geojson` | `Legal_Parcel` | 727 |
| `buildings.geojson` | `Building_Footprints2` | 643 |

Field notes: a COMBINED city — combined mains (276 here) outnumber separated storm (163)
and carry pipe inverts almost never (12/276) while storm mains carry them on 96/163; the
manholes carry `INVERT` (chamber flow line, 287/352) + `RIMELEV` (292/352), so missing pipe
ends take their FROMMH/TOMH manhole's INVERT via the id join (Reykjavík flow-line
precedent). `_SYSTEM` is a fixture-recording tag, not a source field. The separated
sanitary system is tiny inside this AOI (10 mains) — normal for a combined core.
