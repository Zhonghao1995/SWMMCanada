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
