# Vancouver fixtures

Real downtown-Vancouver extracts recorded 2026-07-10 **through the adapter's own fetch
functions** (bbox `-123.125, 49.275, -123.117, 49.281`, ~600 m):

- `mains.geojson` — 137 gravity mains, `eflnttype IN ('Storm','Combined') AND
  servstatus='In Service'`, from VanMap `Hosted/swGravityMain/FeatureServer/11`
- `manholes.geojson` — 140 manholes the mains reference, fetched by `facilityid`
  from `Hosted/swManhole/FeatureServer/12` (carry `rimelev` for the fallback vertical)
- `invert_rows.geojson` — 136 as-built invert rows (Esri-JSON attributes) from
  `VanMapViewer/Infrastructure_Sewer/MapServer` layers 36 (Storm) + 37 (Combined);
  `COV_SOURCE_KEY` joins to the mains' `facilityid`. Contains real 0/0 sentinel rows
  (the source's missing-data marker) so the fallback tier is exercised.
- `sanitary_mains.geojson` / `sanitary_manholes.geojson` / `sanitary_invert_rows.geojson`
  — the Sanitary-only tracer (104 / 113 / 103 features; inverts from layer 35)

Attributes of note: `frommh`/`tomh` (explicit manhole topology), `diameter` (mm),
`slope` (%), `length` (m), `material` (full words, e.g. "Vitrified Clay"),
`UPSTREAM_INVERT`/`DWNSTREAM_INVERT` + the city's own `..._ESTIMATED` flags.
Vertical tiers: as-built inverts first, rim − default depth fallback (ADR 0020 amended).
