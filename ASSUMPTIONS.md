# Model assumptions — what's real, what's derived, what's approximated

SWMMCanada builds a complete, runnable model fast. **Most of that model is grounded in real
data** — either measured and used as-is, or computed from measurements by standard, accepted
methods (the way professional hydrological models are built). A few parts are approximations
where direct data is thin. This page is the honest, layer-by-layer breakdown so you know exactly
which is which.

Sources (providers, endpoints, licences) are in **[DATA.md](DATA.md)**; the calibration caveat is
in the [README](README.md).

## The buckets

| | Bucket | What it means | Layers |
|---|---|---|---|
| 🟢 | **Real data** | measured & published, used as-is | storm pipe network (the 35 real-network cities); ground elevation; rainfall & temperature; parcel & building footprints; node / outfall / catch-basin locations |
| 🟢 | **Derived from real data** | computed from the above by a standard, accepted method — trustworthy model inputs, the way professional models are built | imperviousness %, terrain slope, curve number (CN), evaporation, and the outlines of parcel-followed subcatchments |
| 🟠 | **Approximated / assumed** | where direct data is thin: a sensible approximation or a standard default — apply judgment | the network **outside** the 35 cities (synthesized from streets); how subcatchments are **partitioned** (nearest-inlet service areas, not surveyed watersheds); gap-fills for missing inverts/diameters; non-circular pipes treated as circular; default roughness / depths |

> In a real-network-city model, the great majority of what matters — pipes, terrain, climate, roofs, and the
> parameters derived from them — is 🟢. The 🟠 items are normal modelling approximations to be
> aware of, not red flags.

## By model layer

| Layer | Grounding | Notes |
|---|---|---|
| **Storm network** (pipes, nodes, outfalls) | 🟢 Real (35 cities) · 🟠 synthesized elsewhere | Real = published inverts, diameters, materials, locations. Honest gap-fills for missing inverts (share varies by city — see the per-city table): neighbour values, the node's own rim, then the DEM surface, each counted in the diagnostics; dangling node refs snap to pipe geometry; non-circular profiles → equivalent circular (original shape kept in diagnostics). |
| **Imperviousness (%)** | 🟢 Derived | From real building roofs + road right-of-way where parcels/buildings are published; otherwise from the NALCMS land-cover raster (30 m). |
| **Terrain slope** | 🟢 Derived | Computed from the real NRCan MRDEM (30 m). |
| **Infiltration / curve number** | 🟢 Derived · 🟠 fallback | From real soil (SoilGrids/HYSOGs) → hydrologic soil group → SCS curve number. Falls back to a documented HSG-B default only if soil can't be fetched. |
| **Rainfall / temperature** | 🟢 Real | Nearest active ECCC climate station over your dates. |
| **Evaporation** | 🟢 Derived | Hargreaves (FAO-56) from the station's daily min/max/mean temperature. |
| **Snowmelt** | 🟠 Assumed parameters | On by default whenever a temperature series exists (above the 0 °C dividing temperature nothing accumulates, so summer runs are unchanged). One URBAN snow pack for all subcatchments: melt coefficients 0.1–0.3 mm·h⁻¹·°C⁻¹ (typical degree-day factors 2.4–7.2 mm·d⁻¹·°C⁻¹, converted), base 0 °C, free-water fraction 0.10, plowable fraction 0.10, 100 %-cover depth 25 mm; ATI weight 0.5 and negative-melt ratio 0.6 are SWMM defaults. **Uncalibrated first-pass values — calibrate downstream before using cold-season results.** |
| **Subcatchment outlines** | 🟢 Derived (parcels) · 🟠 otherwise | Shapes follow **real lot lines** where a city publishes parcels (Victoria/Calgary/Surrey/London/Kelowna); a geometric catch-basin tessellation where it doesn't (Ottawa/Kitchener). |
| **Subcatchment partitioning** | 🟠 Approximated | Which area drains to which inlet is a **nearest-inlet service area, not a surveyed (DEM-derived) watershed.** This is the model's main approximation. |
| **Pipe diameters (synthesized networks)** | 🟠 First-pass design | Rational method per pipe — Q = C·i·A over accumulated upstream subcatchments (C from imperviousness: 0.9 impervious / 0.2 pervious), design intensity from the **nearest ECCC IDF station** at the pipe's time of concentration (10 min inlet floor + travel at 1 m/s), **T = 5 yr**; Manning full-flow diameter rounded UP a commercial ladder (300 mm–3.0 m), no downstream shrinkage. IDF unreachable → documented 30 mm/h constant (noted in provenance). **A plausibility estimate, not a certified sizing.** Real-network cities keep their published diameters. |
| **Other parameters** (Manning's n, depression storage, default node depth) | 🟠 Assumed | Standard engineering defaults / material lookup tables; a 2 m default manhole depth where a real elevation is missing. |

## Per-city differences

All 35 real-network cities use **real pipes** (🟢): published locations, connectivity,
diameters and materials. They differ in two things — what each city publishes around the
pipes (subcatchment/imperviousness inputs), and how complete the **vertical** data is
(pipe inverts). The vertical tier is the honest one-glance signal:

- **A** — published inverts, ≤10 % of nodes gap-filled on the recorded test AOI.
- **B** — published inverts with real gaps (~10–35 % of nodes gap-filled from
  neighbours / rims / the DEM).
- **C** — vertical data thin (>35 % gap-filled): pipe locations are real, but a large
  share of node inverts is estimated, mostly anchored to the DEM surface. Fine for a
  first-pass screening model; not a basis for detailed design.

Percentages are measured on each city's recorded test AOI. "typ. err" is the measured
typical error of ESTIMATED node inverts (mask the published inverts on the test AOI at
the city's own sparsity, re-run the gap-fill, mean absolute error against the masked
truth). Every build reports the exact per-tier counts for *your* AOI in its diagnostics
(`n_inv_from_neighbour` / `n_inv_from_rim` / `n_inv_from_dem` / `n_inv_from_global_min`).

| City | Vertical (inverts) | Network topology | Subcatchment outline | Imperviousness |
|---|---|---|---|---|
| Abbotsford, BC | 🟠 B (~16 %, typ. err 0.7 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 land cover (no buildings published) |
| Barrie, ON | 🟠 B (~20 %, typ. err 1.0 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Burnaby, BC | 🟠 B (~20 %, typ. err 1.8 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Calgary, AB | 🟢 A (typ. err 0.4 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Chilliwack, BC | 🟠 B (~26 %, typ. err 0.3 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 land cover |
| Coquitlam, BC | 🟢 A (~7 %, typ. err 0.8 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Delta, BC | 🟠 B (~17 %, typ. err 2.4 m)  | geometry-inferred | 🟠 junction cells (no catch-basin layer) | 🟢 land cover |
| Esquimalt, BC | 🟠 B (~25 %, typ. err 2.0 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Greater Sudbury, ON | 🟠 B (~34 %, typ. err 0.9 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Kamloops, BC | 🟢 A (~4 %, typ. err 2.1 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 real buildings |
| Kelowna, BC | 🟢 A (typ. err 0.7 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Kingston, ON | 🟠 C (~60 %, typ. err 1.4 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 real buildings |
| Kitchener–Waterloo, ON | 🟢 A (typ. err 0.9 m)  | explicit node IDs | 🟠 catch-basin tessellation | 🟢 land cover (no parcels published) |
| Langley (Township), BC | 🟢 A (~7 %, typ. err 1.3 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 land cover |
| London, ON | 🟢 A (typ. err 0.8 m)  | explicit node IDs | 🟢 real parcel lines | 🟢 real buildings |
| Moncton, NB | 🟠 B (~25 %, typ. err 0.7 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Nanaimo, BC | 🟠 C (~41 %, typ. err 4.1 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| New Westminster, BC | 🟠 C (~30 %, typ. err 4.1 m) | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| North Vancouver (District), BC | 🟠 C (~66 %, typ. err 1.7 m)  | geometry-inferred | 🟠 junction cells (packaged download, no land layers) | 🟢 land cover |
| Ottawa, ON | 🟢 A (typ. err 1.0 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 land cover (no parcels published) |
| Penticton, BC | 🟠 B (~13 %, typ. err 0.8 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 land cover |
| Peterborough, ON | 🟠 B (~17 %, typ. err 0.5 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 land cover |
| Port Coquitlam, BC | 🟠 B (~28 %, typ. err 0.4 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Regina, SK | 🟢 A (typ. err 0.6 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Reykjavík, IS | 🟠 C (error not measured: synthetic fixture)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Sarnia, ON | 🟢 A (~10 %, typ. err 0.5 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 real buildings |
| Saskatoon, SK | 🟢 A (~9 %, typ. err 1.2 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 land cover |
| Strathcona County, AB | 🟠 C (~48 %, typ. err 1.6 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 real buildings |
| Surrey, BC | 🟢 A (typ. err 0.7 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Toronto, ON | 🟠 B (~15 %, typ. err 0.8 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 land cover |
| Vancouver, BC | 🟠 B (typ. err 1.3 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Victoria, BC | 🟢 A (typ. err 1.3 m)  | explicit node IDs | 🟢 real parcel lines | 🟢 real buildings |
| Whitby, ON | 🟠 C (~70 %, typ. err 0.7 m)  | geometry-inferred | 🟠 catch-basin tessellation | 🟢 land cover |
| White Rock, BC | 🟠 C (~36 %, typ. err 3.3 m)  | geometry-inferred | 🟢 real parcel lines | 🟢 real buildings |
| Windsor, ON | 🟠 B (~21 %, typ. err 0.9 m)  | geometry-inferred | 🟠 junction cells (packaged download, no land layers) | 🟢 land cover |

City-specific notes:

- **Vancouver** publishes an invert *rows* table (some entries city-flagged as estimated);
  ends it doesn't cover are anchored to the real manhole rim minus the default 2.5 m depth.
- **New Westminster** sits in tier C despite a ~30 % gap-fill share: 68 % of its
  "published" pipe-end inverts are manhole chamber stamps (only ~24 % of ends are true
  pipe measurements), and estimates on its steep riverfront terrain err ~4 m typical.
  Its manhole rims are excellent; its pipe-end verticals are not.
- **Reykjavík** sits outside the Canadian DEM, so the DEM tier cannot help its gaps — thin
  spots fall back to neighbour values and the counted AOI minimum.
- "Geometry-inferred" topology means nodes come from snapped pipe endpoints; most of these
  cities publish manhole/structure IDs, which are kept as the node names.
- The original 8 cities (Victoria → Regina) were re-audited field-by-field in 2026-07
  (elevation semantics: what each city's "invert"/"elevation" column actually means).

Outside these cities, the network itself is 🟠 synthesized from OpenStreetMap streets.

## The bottom line

> [!NOTE]
> A generated model is **grounded in real data and ready to run**: the pipes (in the 35 cities),
> terrain, climate, roofs/parcels, and the parameters derived from them are real or standard
> derivations from real data. The approximations to keep in mind are the **subcatchment
> partitioning** and, outside the 35 cities, the **network** itself.

> [!WARNING]
> **Models are uncalibrated.** No parameters are fitted to observations — this is true of any
> auto-built model, however real its inputs. Calibrate against gauged flow (e.g. ECCC HYDAT)
> before using results for design or decisions.

## Physical imperviousness (ADR 0023 cut 1, #138)

Where OSM maps buildings inside a synthesis cell, `pct_imperv` is the physical estimate
instead of the 30 m land-cover mean:

- **Road half-width 4.0 m** — the paved band each side of a street centreline (~8 m local
  carriageway, curb to curb). One documented number for all street classes.
- **Driveway/sidewalk allowance +10 %** — paved surfaces that ride along mapped roofs but
  are not mapped themselves (driveways, walks, patios).
- **Evidence threshold: roof fraction ≥ 2 %** — cells without mapped buildings keep the
  land-cover value; OSM's suburban sparsity must degrade to the raster, never to zero.
- **Cap 90 %** — even a fully built cell keeps some pervious cracks/verges.

## Curve numbers: land cover × HSG (F-021/ADR 0024)

`CURVE_NUMBER` infiltration composes SCS CN as the area-weighted TR-55 value over the
cell's NALCMS classes for its dominant hydrologic soil group (Table 2-2 rows: woods
fair/good, brush fair, open space fair, row crop SR good, fallow bare, commercial
districts, saturated wetland 85, water 98). Unknown classes read as open space. Without
a usable land-cover window the old single HSG→CN lookup applies, then the caller's
fallback — the tiers are recorded implicitly by which inputs existed.

### Round-2 amendment: urban CN is the pervious remainder

Built-up classes map to the TR-55 urban *pervious* row (open-space fair, 49/69/79/84),
NOT composite commercial CN: SWMM applies CURVE_NUMBER infiltration to the pervious
sub-area only and `pct_imperv` already carries the impervious share — composite CN would
double-count imperviousness. NALCMS class 13 corrected to barren (lichen-moss).

## Node vertical geometry: what an "invert" is here (audit #157)

At one real manhole **three different elevations coexist** — the in-chamber channel/flow
line, the lowest connected pipe invert, and the sump/chamber floor below the flow line —
and municipal "bottom elevation" fields may mean any of them. This is the convention the
pipeline uses, now stated explicitly rather than left implicit in the code:

- **Node invert = the lowest connected pipe-end invert** (`min` over the pipe ends meeting
  at that node). Where a city publishes no invert for a node, the gap fills in TIERS
  (#158): first from neighbouring nodes; then from **the node's own rim minus a default
  2.5 m node depth** (a local estimate anchored to real ground — the Vancouver ADR 0020
  convention generalised); then, in pipeline builds, from **the DEM surface at the node
  minus the same 2.5 m** (rim ≈ DEM ground), which is what carries cities that publish no
  rim layer at all — the sampled surface also serves as the node's ground estimate so max
  depth is real; only a node with no invert, no informed neighbour, no rim AND no DEM
  sample falls back to the AOI-wide minimum, and every tier is counted in the diagnostics
  (`n_inv_from_neighbour` / `n_inv_from_rim` / `n_inv_from_dem` / `n_inv_from_global_min`).
  The old two-step fill let the global minimum put sea-level inverts on 80 m hillsides;
  with the DEM tier a North Vancouver District test AOI went from 1,377 identical
  valley-floor inverts to 0.
- **Sump depth is not modelled.** Regina (`SUMPELEVATION`, a median 1.77 m below the rim),
  Kelowna (`SUMP_ELEVATION`) and Kitchener (`SUMP`) all publish a genuine chamber-floor
  field. None is read. A sump is dead storage that traps grit; it does not carry flow, and
  substituting it for the flow line would lower every node bottom and flatten pipe slopes.
  Node bottoms therefore sit at the flow line, which is what a hydraulic model wants.
- **Max depth = rim − lowest pipe invert**, and only inside a plausibility band: a
  non-positive value, or one beyond `MAX_NODE_DEPTH_M` (15 m), falls back to the 2 m default
  and is counted as `n_depths_rejected`. SWMM reads MaxDepth as the depth at which a node
  floods, so an inflated depth creates a node that can *never* flood and silently swallows
  surcharge that should have been reported as flooding. Before this band existed, 884
  junctions across the ten fixture AOIs carried impossible depths (to 336 m).
- **Per-city field semantics are audited, not assumed.** Every elevation field each adapter
  reads has a confirmed meaning recorded in that adapter's module docstring, with the
  publisher's own definition quoted where one exists and a numeric cross-check where the
  dictionary is silent. Two cities settle it with their own arithmetic: Victoria and London
  publish a `Depth` column that reproduces `rim − invert`.
- **Known loss — Reykjavík.** Its pipes publish no inverts at all, so one structure-level
  `BOTNKODI` (confirmed a flow line, alias `Rennslishæð`) is lifted onto every pipe end that
  snaps to it. That erases in-chamber falls at 85% of its structures; peer cities that
  publish per-end inverts put the erased fall at a median 0.02–0.11 m (p75 0.13–0.53 m).
  Reykjavík profiles are smooth by construction.
