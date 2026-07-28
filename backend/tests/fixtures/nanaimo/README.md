# Nanaimo fixtures

Recorded **2026-07-28** from the City of Nanaimo AGOL org (`services1.arcgis.com/D2GiQOd2jzaj2Pzh`,
data.nanaimo.ca), `f=geojson`, WGS84, `resultOffset` pagination, `maxRecordCount` 2000.

## Sub-bbox (EPSG:4326): `-124.005, 49.230, -123.993, 49.239` — north Nanaimo (~900 m)

| file | service/layer | where | n |
|---|---|---|---|
| `mains.geojson` | `Storm_Sewer_Main/7` | `FTYPE IN ('Main','Culvert')` | 331 |
| `catchbasins.geojson` | `Storm_Sewer_Catchbasin/2` | — | 294 |
| `sanitary_mains.geojson` | `Sanitary_Sewer_Main/9` | `FTYPE='Gravity'` | 225 |
| `parcels.geojson` | `Parcel_Map_BC_Parcel_Polygon/4` | — | 871 |
| `buildings.geojson` | `Building_Footprints/1` | — | 656 |

Field notes: pipe rows carry BOTH `ST/END_INVERT` (222/331 here) AND `ST/END_COVELV`
(cover/ground, 211 here — rims-on-row, no manhole join needed), plus numeric
`ST_NODE`/`END_NODE` labels (MH-prefixed), `PIPESIZE` mm, spelled-out `MATERIAL`
("Polyvinyl Chloride"). Storm `FTYPE` vocabulary: Main / Culvert / Catch Basin Lead /
Perforated Drain; sanitary: Gravity / Pressure. The Inlet/Outlet point layer does not
distinguish inlets from outlets and is not used (Barrie headwall lesson).
