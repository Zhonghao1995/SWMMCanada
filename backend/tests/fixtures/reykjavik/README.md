# Reykjavík / capital-area fráveita fixtures

First **non-Canadian** city. The Icelandic municipal drainage register follows the national
*fitjuskrá* feature standard, so Reykjavík (Veitur / Orkuveita Reykjavíkur's **LÚKOR**) and the
neighbouring municipalities share ONE schema. The fixtures here are **synthetic** (hand-authored
with the real field names) so the network-assembly + structure-invert-snap logic is testable
offline; a live capture drops in without touching the adapter.

## Data sources
- **Target — Reykjavík (Veitur / OR):** `https://lukor.or.is/arcgis/rest/services/lukor/lukor_overlay/MapServer`
  (ArcGIS MapServer, ISN93/EPSG:3057). Geo-restricted to `.is` today — reachable from Iceland,
  refused elsewhere. Swap it in via `PIPES_URL`/`STRUCT_URL`/`INLETS_URL` only (no other change).
- **Verified twin — Kópavogur (LÚKK), token-free 2026-07-17** (the adapter default): hosted
  FeatureServers on `services7.arcgis.com/ZIyvqwbcRPyMF4iE` — `Fráveitulagnir/9` (mains),
  `Fráveitubúnaður/4` (structures), `Niðurföll/0` (catch basins), `Landeignir/2` (parcels),
  `Hús/0` (building footprints). Same fitjuskrá fields. For Reykjavík the land layers instead
  come from the City LÚKR portal (lukrgatt.reykjavik.is: OpinGognLodirLodamork / OpinGognHus).

The adapter fetches **Esri JSON** and converts (`_fetch` → `base.esri_to_geojson`), for both hosts.
NB: hosted FeatureServers nest `exceededTransferLimit` under the GeoJSON collection's
`.properties`, which once made a GeoJSON fetch silently stop after one page (a 6428-pipe AOI
returned exactly 1000). `base.fetch_paged` now reads the flag in both places (fixed after this
adapter's review surfaced it — live Calgary had lost 2716 of a 4716-pipe AOI), so GeoJSON pages
correctly too; Esri JSON stays this adapter's format because both Icelandic hosts serve it.

## Topology contract (Ottawa-style, but with real inverts)
Pipes carry **no inverts and no node ids** → connectivity is inferred from polyline endpoints
(`cities.base` coordinate snapping). The inverts the pipes lack live on the **structure points**,
so `build_reykjavik_network` snaps each pipe endpoint to its nearest structure (≤ `_SNAP_TOL_M` = 5 m)
and lifts that structure's fields onto the network.

## Files (GeoJSON FeatureCollections; `properties` = raw fitjuskrá attributes)
- `storm_pipes.geojson`   — 2 mains (LineString). Fields below. Source of truth for topology.
- `sanitary_pipes.geojson`— 1 separated sanitary main (INNIHALD=skólp) for the ADR 0011 tracer.
- `structures.geojson`    — 3 structures (Point): the invert/rim/id/outfall carrier.
- `raw_pipes.geojson`     — 6 mains with mixed INNIHALD (regnvatn/blandað/skólp) for the fetch split.
- `catchbasins.geojson`   — 3 Niðurföll (Point): subcatchment seeds.
- `parcels.geojson`       — 2 Landeignir (Polygon): lot-line subcatchment cells.
- `buildings.geojson`     — 3 Hús (Polygon), incl. one self-intersecting bowtie for the repair test.

## Key pipe fields (`Fráveitulagnir`)
`EFNISGERD` material (steypa/plast/leir…) → Manning's n · `TVERMAL` diameter (mm) · `INNIHALD`
contents (**ofanvatn**=storm / **blandað**=combined → joins storm / **skólp**=sanitary) ·
`RENNSLI` flow type · `Shape__Length` length (m). No invert, no node id.

## Key structure fields (`Fráveitubúnaður`)
`HLUTUR` type (**Brunnur**=manhole, **Endi**=outfall, Niðurfall=inlet…) · `HAED` rim/ground
elevation (m) · **`BOTNKODI` (Rennslishæð) = invert / flow elevation (m)** · `AUDKENNI` asset id
(→ SWMM node id). `AUSTUR`/`NORDUR` hold the ISN93 easting/northing (geometry is requested back in
EPSG:4326 by the shared fetch loop, so the adapter reads geometry, not those columns).
