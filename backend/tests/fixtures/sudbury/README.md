# Greater Sudbury fixtures

Recorded **2026-07-28** from the city AGOL org (`services.arcgis.com/q3mIlR87lZlZsds3`),
`f=geojson`, WGS84, pagination OK (the PR #154 AGOL exceededTransferLimit family).

## Sub-bbox (EPSG:4326): `-81.005, 46.485, -80.985, 46.500` — downtown Sudbury (~1.6 km)

| file | service/layer | where | n |
|---|---|---|---|
| `mains.geojson` | `Drainage_view/9` Gravity Main | `STYPE IN ('Storm sewer','Collector (trunk)','Outfall','Tunnel')` | 1584 |
| `manholes.geojson` | `6` Maintenance Hole | — | 664 |
| `outfalls.geojson` | `4` Discharge | — | 37 |
| `catchbasins.geojson` | `0` Catch Basin | — | 724 |
| `sanitary_mains.geojson` | `wastewater_open_data/10` Gravity Main | — | 692 |
| `sanitary_manholes.geojson` | `wastewater_open_data/6` | — | 545 |
| `buildings.geojson` | `Address_and_Building_Roofline/3` | — | 1478 |
| `parcels.geojson` | `Land_Use_and_Boundaries_view/6` | — | 1413 |

Field notes: `INVERTUS/DS` inverts (1103/1584 here; 0 = missing; the live feed also ships
junk like a 958 m invert against ~280 m terrain -> (200,420) band), `WIDTH` mm,
`MATERIAL` codes (CL = clay). Maintenance holes: `ELEVATION` rim (521/664). One recorded
conduit runs 1 cm "uphill" into a discharge point — survey noise at the forced-outfall
orientation, tolerated at 0.02 m in the tests.
