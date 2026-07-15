# Vancouver fixtures

Real downtown-Vancouver extracts recorded 2026-07-10 **through the adapter's own fetch
functions** (bbox `-123.125, 49.275, -123.117, 49.281`, ~600 m):

- `mains.geojson` — 137 gravity mains, `eflnttype IN ('Storm','Combined') AND
  servstatus='In Service'`, from VanMap `Hosted/swGravityMain/FeatureServer/11`
- `manholes.geojson` — 140 manholes the mains reference, fetched by `facilityid`
  from `Hosted/swManhole/FeatureServer/12` (carry `rimelev` for the rim-anchored vertical)
- `sanitary_mains.geojson` / `sanitary_manholes.geojson` — the Sanitary-only tracer
  (104 / 113 features)

Attributes of note: `frommh`/`tomh` (explicit manhole topology), `diameter` (mm),
`slope` (%), `length` (m), `material` (full words, e.g. "Vitrified Clay").
Vancouver publishes **no inverts** — the adapter anchors them to rims (ADR 0020).
