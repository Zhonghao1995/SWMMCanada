# Penticton fixtures

Recorded **2026-07-28** from the City of Penticton AGOL org (`services1.arcgis.com/ZMQyarkhNAnn8lip`),
`f=geojson`, WGS84, `resultOffset` pagination.

## Sub-bbox (EPSG:4326): `-119.605, 49.480, -119.585, 49.495` — central Penticton (~1.6 km)

| file | service/layer | where | n |
|---|---|---|---|
| `mains.geojson` | `Storm_PRD/415` Pipe | `lifecyclestatus='In Service' AND ASSETTYPE='Gravity Pipe'` | 187 |
| `manholes.geojson` | `Storm_PRD/412` Manhole | In Service | 209 |
| `outlets.geojson` | `Storm_PRD/410` Outlet | — | 11 |
| `catchbasins.geojson` | `Storm_PRD/408` Catchbasin | — | 344 |
| `sanitary_mains.geojson` | `Sanitary_PRD/316` Sewer Gravity Main | In Service (Main/Trunk) | 415 |
| `sanitary_manholes.geojson` | `Sanitary_PRD/313` Manhole | In Service | 372 |

Field notes: `upelev`/`downelev` inverts (163/187 here), `us_feat`/`ds_feat` termination-id
labels (SWMH-/SWDP-/SWF-), `diameter` is TEXT with units ("300 mm"), `material` spelled out
with a trailing code ("… - CP"). Manholes publish `invertelev`/`highelev` but NO rim —
default max depths. The Outlet layer (SWDP-*) is a real outfall source.
