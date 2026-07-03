# Kelowna storm-network fixtures (real data, captured from geoportal.kelowna.ca)

Captured **2026-06-22**. NOTE the service root is `geoportal.kelowna.ca` (NOT `geo.kelowna.ca`).

## Service + layers
Storm utilities: `https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Utilities_Storm/MapServer`
- `storm_pipes.geojson` — layer **22** Storm Main (LineString). Source of truth for topology + hydraulics.
- `outfalls.geojson`    — layer **4** Storm Outfall (Point).
- `catchbasins.geojson` — layer **19** Storm Catchbasin (Point); carry `SUMP_ELEVATION`, `CB_TYPE`.

Parcels (layer **3** Legal Parcel) and Buildings (layer **17** Building Outlines) live on a
separate service and are fetched live (not captured here, to keep fixtures small):
`https://geoportal.kelowna.ca/arcgis/rest/services/ArcGISOnline/OpenData_Planning_and_other/MapServer`

## Sub-bbox (EPSG:4326 lon/lat, min_lon,min_lat,max_lon,max_lat)
`-119.475, 49.890, -119.469, 49.895` (a small residential sub-area of Kelowna, BC).
Yields 46 storm mains, 4 outfalls, 61 catch basins.

## f=geojson vs f=json
`f=geojson` returns **real geometry** on this MapServer (verified 2026-06-22) — LineString
coordinates for layer 22, Point for layers 4/19. So the adapter reads GeoJSON directly and
does **not** need `base.esri_to_geojson`. (Files here are those `f=geojson` responses, as
GeoJSON FeatureCollections; volatile audit fields CreatedBy/CreatedDate/LastEditor/LastEditDate
were stripped to keep the fixtures small.)

## Field notes (storm_pipes properties)
- `INVERT_IN_Z`, `INVERT_OUT_Z` — doubles (m). **Populated** on the real data (~96% of pipes;
  41/44 of 46 in this fixture). Mapped INVERT_IN_Z->inv_a, INVERT_OUT_Z->inv_b. (The IN/OUT
  naming does not reliably indicate flow direction — `base.assemble_network` re-orients each
  conduit downhill by node invert, so this is only a node-invert candidate.)
- `DIAMETER` — **STRING** (mm), e.g. `"300"`. Cast to float, /1000 -> metres; ""/0/None -> missing.
- `LENGTH` — **STRING** (m), e.g. `"47.46"`, sometimes null. Cast to float; ""/0/None -> missing
  (geodesic from geometry then used).
- `MATERIAL` — AC / CMP / PVC / PERFPVC / RIBPVC / RCP / CONC / VIT / HDPE / CI / DI ... The
  adapter strips PERF/RIB prefixes and aliases RCP->concrete, VIT->clay before the shared
  material->roughness lookup.
- NO node ids -> topology inferred from polyline endpoints (snap_decimals=5).

## Sanitary skeleton (ADR 0011) — `sanitary_mains.geojson`
Captured **2026-07-03** from the sanitary utilities service
(`.../ArcGISOnline/OpenData_Utilities_Sanitary/MapServer`, layer **11** Sanitary Main) over
the same sub-bbox with the adapter's where-clause `STATUS = 'A'` (active only; STATUS also
holds B / I codes city-wide, and force mains live on their own layer 12, not fetched).
47 mains; same schema as the storm layer (INVERT_IN_Z/OUT_Z doubles, string DIAMETER/LENGTH),
volatile audit fields stripped, so `build_kelowna_network` assembles it unchanged as the
second tagged system.
