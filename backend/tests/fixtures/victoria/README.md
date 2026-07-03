# Victoria storm-drain fixtures (real data, captured from maps.victoria.ca)

Service: `https://maps.victoria.ca/server/rest/services/OpenData/OpenData_StormDrain/MapServer`
Layers: Gravity Mains=10, Manholes=4, Fittings=3, Outfall(Discharge)=5, Catch Basins=1, Catchment Areas=12.
Captured for a ~600 m downtown AOI around outfall CRD 614 (-123.36878, 48.42248). All geometry EPSG:4326 (lon,lat).

## Files (GeoJSON FeatureCollections; `properties` = raw ArcGIS attributes)
- `mains.geojson`     — 50 STM gravity mains (LineString). Source of truth for topology + hydraulics.
- `manholes.geojson`  — referenced DMH nodes (Point). Carry ground `Elevation` + `Depth`.
- `fittings.geojson`  — referenced DFG nodes (Point). NO elevation/invert (geometry only).
- `outfalls.geojson`  — referenced DOF nodes (Point). Carry `Elevation`, `OutfallNo`.
- `raw_arcgis_mains_query.json` — one raw ArcGIS `f=json` response (for fetch-client parse tests; note `exceededTransferLimit`).

## Join / topology contract
- **Node join key = `AssetID`** (NOT `InfrastructureID`). Main `UpstreamNodeID`/`DownstreamNodeID` match a node's `AssetID`.
- Node-id prefixes: `DMH`=manhole, `DFG`=fitting, `DOF`=outfall.
- ~10% of main endpoints are **dangling** (referenced AssetID absent from every point layer). Fallback: take that node's coordinate from the main's polyline endpoint (the OTHER endpoint matches a resolved node's point, so the unresolved end is unambiguous; if both dangling, polyline start=upstream, end=downstream).

## Key main fields (mains.geojson properties)
UpstreamNodeID, DownstreamNodeID, UpstreamInvert (m), DownstreamInvert (m), Diameter (mm),
CrossSectionShape (CIR/BOX/ARCH/IEGG/HSH/UNK/None), Material, Slope, Length_2D (m), WaterType (filter 'STM').

## Node fields
Manholes/Outfalls: AssetID, Elevation (m, ground), Depth (m), geometry Point.  Fittings: AssetID, geometry Point.

## Sanitary skeleton (ADR 0011) — `sanitary_mains.geojson`
Captured **2026-07-03** from the separate Sewer service
(`.../OpenData/OpenData_Sewer/MapServer`, layer **4** Sewer Gravity Mains) over the fixture
bbox `-123.380, 48.415, -123.360, 48.430` with the adapter's where-clause
`WaterType='SEW' AND LifecycleStatus='ACT'` (drops the two CWW combined relics and ABD
abandoned lines; pressurized mains live on layer 2 and are not fetched). 528 mains; same
schema as the storm mains (UpstreamNodeID/DownstreamNodeID, Upstream/DownstreamInvert,
Diameter mm, Material, Length_2D), volatile audit fields stripped. The sewer node layers use
a different id scheme than the storm DMH/DFG/DOF join, so `fetch_victoria_sanitary` fetches
NO node layers — every endpoint takes the documented polyline-vertex fallback, and
per-component sinks stand in for the treatment-bound exits.
