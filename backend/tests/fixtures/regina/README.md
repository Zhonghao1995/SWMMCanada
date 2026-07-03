# Regina storm-network fixtures (real data, captured from opengis.regina.ca)

Captured **2026-07-02** from the City of Regina's own ArcGIS Server (10.91), the machine
behind the open-data catalogue at <https://open.regina.ca>.

## Service + layers
Storm network: `https://opengis.regina.ca/arcgis/rest/services/OpenData/StormSewerNetwork/MapServer`
- `storm_pipes.geojson` — layer **5** Storm Sewer Line (LineString). Source of truth for
  topology + hydraulics. Captured with the adapter's where-clause
  `STATUS = 'ACTIVE' AND SUBTYPENAME <> 'Force'` (gravity graph only: Main/Trunk/Drain/
  Culvert/RetentionTank; drops ABANDONED / CUT OFF / NOT IN USE lines and force mains).
- `outfalls.geojson`    — layer **4** Outfall (Point); Wascana Creek bank outfalls.
- `catchbasins.geojson` — layer **3** Catch Basin (Point); carry `RIMELEVATION`, `SUMPELEVATION`.

Parcels (`OpenData/Parcels/MapServer/0`, the ASSESSMENT_REGIONS lot-polygon layer, ~73k
parcels with ACCOUNT_NUMBER/APN) and buildings (`OpenData/BuildingFootprint/MapServer/0`)
are fetched live by `fetch_regina_land` (not captured here, to keep fixtures small).

## Sub-bbox (EPSG:4326 lon/lat, min_lon,min_lat,max_lon,max_lat)
`-104.622, 50.4355, -104.612, 50.447` (downtown Regina reaching the Wascana Creek bank, SK).
Yields 178 storm sewer lines, 15 outfalls, 307 catch basins.

## f=geojson vs f=json
`f=geojson` returns **real geometry** on this server (verified 2026-07-02) — LineString
coordinates for layer 5, Point for layers 4/3 — and honours `resultOffset` pagination
(maxRecordCount 1000). The adapter reads GeoJSON directly; `base.esri_to_geojson` is kept as
a defensive fallback. (Files here are those `f=geojson` responses as FeatureCollections;
volatile audit / free-text fields UPDATE_DATE/UPDATE_USER/SURVEY_DATE/COMMENTS/DETAILDRAWING
were stripped to keep the fixtures small.)

## Field notes (storm_pipes properties)
- `STARTELEVATION`, `ENDELEVATION` — doubles (m AMSL, ~570+ in Regina). Populated on 162/178
  fixture pipes (at least one end). Mapped STARTELEVATION->inv_a, ENDELEVATION->inv_b; either
  may be null (`base.assemble_network` re-orients each conduit downhill by node invert and
  gap-fills missing node inverts from neighbours). RARE DIRTY VALUES: GISID 1244 (in this
  fixture) carries the placeholder `1.0` at both ends; city-wide 5 of ~15,800 active lines are
  outside [500, 700] m (also a `57.23` dropped-digit typo). The adapter treats out-of-band
  inverts as missing (`_invert`, band 500–700 m).
- `DIAMETER` — **integer** (mm), e.g. `250`; 0/None -> missing. /1000 -> metres.
- `SURVEYLENGTH` — double (m), sometimes null/0 -> missing (geodesic from geometry then used).
- `MATERIAL` — CONC / RCP / CSP / PVC (+ "PVC RIBBED"/"PVC SDR35"/"PVC FLEXLOC"/"PVC PERMALOC")
  / VCT / TILE / AC / CI / STEEL / "CORREGATED GALVANIZED STEEL" (spelling as published) /
  POLY variants / PRELOAD / UNKNOWN. The adapter collapses PVC*/CORREGATED*/POLY* prefixes and
  aliases RCP->concrete, VCT/TILE->clay, PRELOAD->concrete before the shared roughness lookup.
- `GISID` — integer asset id, unique per layer (0 nulls city-wide) -> conduit names.
- NO node ids -> topology inferred from polyline endpoints (snap_decimals=5).
- Manholes (layer **2**) publish RIMELEVATION (~88% populated) and ARE fetched as node ground elevations (max depth = rim − invert), like
  the other geometry-inferred adapters.
