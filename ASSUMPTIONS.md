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

## What a storm subcatchment is here

**Land is divided among the model's nodes.** A subcatchment discharges to a node that
exists, and in a published pipe network those nodes are the maintenance holes. Catch basins
are surface structures joined by leads; almost none are model nodes, and the reach between
two nodes has one tributary area however many inlets sit on it. Splitting it per inlet gives
several subcatchments discharging to the same node — more model objects, no more information
about the pipe. That level of detail belongs to a dual-drainage model, where inlet capture
and street flow are the question being asked.

Catch basins keep their real job: their leads say which main a lot taps, which is how a
cell's outlet is resolved.

**Each node takes the land draining to its own reach** — the street segment plus the lots
fronting it, back to the rear-lot line, with the divide at the segment midpoint. This is
what a municipality draws. Assigning land to the nearest node *point* instead carves every
block into a triangle fan meeting at its centre, which is not a shape anyone surveys.

**Which way each gutter runs is decided by the ground, and by the inlets.** The divide
between two nodes sits at the crest between them, not at the geometric midpoint — water runs
downhill to whichever node is lower, and maintenance holes are placed for pipe runs rather
than for symmetry. Grade alone would send a whole falling street to its lowest node and
leave every node above it dry, so published inlets intercept: each stretch of gutter is
caught by the first inlet below it, and that inlet resolves to a node. Several inlets on one
reach merge into one cell. Without a surface the divide stays at the midpoint; without
published inlets the grade rule stands alone.

Terrain is used here rather than to cut cells. Cutting by terrain was tried and produced a
majority of cells at noise scale — the area was all present, gathered into a few basins with
slivers around the rest of the nodes.

Measured on downtown Victoria (88.9 ha, 391 nodes, 541 inlets): 174 cells, 91% coverage,
median 0.36 ha, ninetieth percentile 0.86 ha, and no cells at noise scale. For comparison
the previous inlet tessellation gave a 0.076 ha median with 36% of cells at noise scale, and
routing terrain to each inlet gave 0.027 ha with 59%.

Where the streets are not published the shaping falls back — to terrain where the surface is
fine enough, to lot lines where it is not, and to nearest-node tessellation as the floor. The
unit does not change; only the edges do.

## Bringing your own subcatchment boundaries

Everything in the sections that follow is a judgement call: which method the data supports,
what an overland flow length is, how much of a road reserve is paved, whether kerb lines are
fine enough to use. They are defensible and they are still judgements.

So an uploaded layer overrides all of it. Upload a GeoJSON of polygons and those boundaries
are used **verbatim** — no reshaping, no merging, no sliver discipline — ahead of the city's
own catchment layer and ahead of anything derived here. A municipal layer is authoritative
about the municipality; yours is authoritative about what you want modelled.

What is still derived: outlets, unless a feature names one this network contains (a polygon
file rarely carries our node ids, and naming one we do not have is not a reason to reject
the upload); and imperviousness, slope and curve number, from terrain, land cover and soil
exactly as for any other cell. Only the boundary is yours.

Its confidence is recorded as **unrated** rather than high or low. We did not draw these and
cannot vouch for them — calling them high would be endorsing someone else's work, low would
be dismissing it.

## Telling the terrain what it cannot see

At 1 m LiDAR posting a 150 mm kerb is one pixel of a smooth cross-slope, so D8 routes street
runoff across it into the front garden when in reality it runs along the gutter to the
nearest inlet. Where a city publishes the assets, three facts are written into the surface
before flow directions are computed:

- **kerbs** are raised 0.30 m — larger than the real face on purpose, so the barrier is
  decisive rather than a model of its exact height;
- **kerb drops and inlets** open a 2 m gate, punched out of the barrier *before* it is
  raised, so the router finds the crossing on its own;
- **buildings** are raised 10 m and are not crossed at all.

Conditioning happens before depressions are filled, because ponding behind a kerb is real
and filling is what resolves it.

Two limits, both physical rather than incidental: a kerb across a valley is simply
overtopped, because the water behind it has nowhere else to go; and a 150 mm kerb does
nothing on a steep grade. Neither is a defect and both are pinned by tests.

It is skipped entirely on a DEM coarser than 2 m — a 150 mm edit is far below the vertical
noise of a 30 m surface, and claiming to have conditioned with it would be theatre. Five of
the fleet publish kerbs; the rest are untouched.

**Inlets are snapped to the local low** before being used as drainage targets, within about
one carriageway width. A published inlet coordinate marks the structure, not the pixel water
arrives at, and an inlet left on the kerb gets a basin of one pixel while its real catchment
drains past it. The search is deliberately short: a distant low point is somebody else's
gutter. Snapping is a heuristic, so the delineation is still checked afterwards and falls
back if it made things worse.

## How much of a road reserve is pavement

Imperviousness counts roofs in full plus the paved share of the road reserve — the land in a
cell that falls outside every parcel.

That share was an implicit, undocumented **1.0**: every square metre outside a parcel was
counted as pavement. Downtown that is close enough, because carriageway plus sidewalk fills
the reserve. In a suburb it is not — a 20 m reserve carries an 8 m carriageway between grass
boulevards, and counting all of it inflates a residential cell.

It is now a stated **0.85**, and configurable. A stated number can be argued with; a silent
one cannot. Measured on live downtown Victoria the median cell moves from 100% to 95.6% and
the lower quartile from 56.3% to 52.9% — small downtown, which is exactly where the old
assumption was nearly right, and larger where it was not.

Roofs are never discounted by it: the allowance applies to the reserve, and a roof is a
roof. Where kerb lines are published the paved area can be measured instead of assumed, and
that replaces this number rather than tuning it.

## What a method label is telling you

The delineation method travels with every model, and it is the shortest honest answer to
"how much should I trust these boundaries". Two of the labels were misleading and have been
renamed:

| Label | What produced the boundaries |
|---|---|
| `catchbasin_parcel` | real inlets, real lot lines and roofs |
| `junction_dem` | terrain, D8 flow paths to manholes |
| `fallback_voronoi_catchbasin` | real inlets, **geometric** division between them |
| `fallback_voronoi_junction` | **nothing** to delineate with: land goes to the nearest node |

"Voronoi" reads to a hydrologist as a technique. It is not one here. Assigning land to
whichever node happens to be nearest is what the code does when it has no inlets, no lot
lines and no usable terrain, and the name now says so rather than dressing an absence of
data as a method. The two renamed entries keep their `low` confidence — this is a
relabelling, not a demotion, because they were always the floor.

## Subcatchment width: a flow length, not a square root

SWMM's width is area divided by the distance water travels overland. `sqrt(area)` is that
distance for a square cell and only for a square cell.

Municipal cells are street-frontage strips, and water does **not** run along the gutter to
the inlet as overland flow — it crosses the lot perpendicular to the street, reaches the
gutter, and travels the rest as channel flow. So the length that matters is the depth of the
strip, and the width is its frontage.

Width is now `area / (area / frontage)`, with frontage taken as the long side of the cell's
minimum rotated rectangle. Measured on live downtown Victoria (3,387 cells): the median
width is **1.58x** what the square assumption gave, the 95th percentile **2.67x**, and 58%
of cells widened by more than half. Only 2% are within 10% of where they were — square-ish
cells stay put, elongated ones move, which is the intent.

This changes hydrographs, not just a number: a wider subcatchment has a shorter flow length
and responds faster and more sharply. Models built before this ran their street strips as if
water crossed four times the distance it does.

The frontage estimate is geometric and does not know where the street is. It is the honest
approximation available without a flow-direction raster; the DEM path computes the same
quantity properly from raster flow paths.

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

## Wastewater loading: dry-weather flow (ADR 0031, #12)

The sanitary system carried pipes and manholes and no water until now. It carries flow
because a service area was drawn and a coefficient was applied to it — and **the coefficient
is the accuracy ceiling, not the drawing**. Splitting a city into thousands of service areas
and multiplying each by a handbook number does not make the answer more accurate; it makes a
very fine polygon times a guess. Every number below is 🟠 until a city's measured plant
influent replaces it.

- **280 L per person per day** — average-day domestic sanitary flow, Canadian municipal
  design practice. Carries roughly ±50 % until calibrated.
- **2.4 persons per dwelling** — StatCan 2021 average household size, used where a city
  publishes address points or dwelling counts.
- **45 persons per hectare** — medium-density urban residential, used only where neither a
  population nor a dwelling count is available. This is the floor of the ladder and every
  build reports what share of its areas rest on it.
- **Diurnal pattern** — a standard municipal 24-hour shape (overnight minimum ~0.28,
  morning peak ~1.61), mean exactly 1.0 so it redistributes the average day without
  changing its volume. A handbook shape until a city's own flow record replaces it.

**Population is estimated on a ladder, and which rung answered is recorded**: published
population → dwelling count × household size → area × assumed density. An area resting on
assumed density stays distinguishable from one resting on a census count, the same
discipline the invert gap-fill uses.

**Geometry provenance and loading provenance are separate.** A service area can be taken
verbatim from a municipal polygon (`geometry_source = official`) while the coefficient
applied to it is a handbook number (`loading_source = synthetic`). One must not lend its
authority to the other.

**Wet-weather sewer response (RDII) is deliberately absent.** The interface exists and is
empty. Without measured wet-weather sewer flow the RTK unit-hydrograph parameters can only
be invented, and an invented wet-weather response is a worse failure than an honest gap —
it would look like the answer to the question combined and I&I studies actually ask.

## How well our outlet resolution matches the city's own (Victoria, 2026-08-12)

Municipal catchment polygons declare which outfall each area drains to. We do not read that
field — it lives in the city's id space and most of the fleet infers topology geometrically
— so we resolve outlets ourselves and use the declaration as a yardstick.

**Victoria: 80.3% agreement over 1,185 comparable units, 62.9% of the model.** Every
disagreement lands on an *adjacent* published outfall rather than somewhere unrelated, which
is the residual this method is expected to have: two neighbouring drainage areas differ near
their shared boundary, not wholesale.

The coverage figure matters as much as the rate. Units are excluded, and counted separately,
when they drain to an invented boundary (357 — their real destination is outside the AOI),
reach no outfall at all (237 — a connectivity fault reported in its own right), fall outside
every official polygon (63), or are sent to an outfall outside the extract (8). Without that
exclusion the same method reported 3.8% on a clipped extract, with every "disagreement"
pointing at a boundary we had invented ourselves — a number that looks like a quality measure
and is an artefact of the clip.

Only Victoria publishes both the polygons and a joinable outlet key, so this is one city's
number, not the fleet's.

## Invented outfalls: how many of a model's destinations are real

Where a city publishes no outfall for a drainage component, the assembler promotes that
component's lowest node into one so the water has somewhere to go. This has always
happened, for every city and every system; what is new is that those outfalls now **say so**
(`synthesised = true`) instead of being indistinguishable from published structures.

The scale is easy to underestimate. Victoria publishes **zero** sanitary outfalls, so all
**19** destinations in its sanitary system are modelling boundaries. A missing destination
fails validation loudly; an invented one that looks published passes quietly and is then
used as if it were real. That is the failure this marker exists to prevent.

Reading a result: an outfall marked synthesised is a place where water leaves the model, not
a structure you can go and look at. Its invert is the component's lowest node minus a
nominal drop, and it carries no boundary behaviour beyond free discharge.

## Wastewater terminal outlets (ADR 0029 Q4)

A combined sewer has two real destinations: dry weather leaves through an interceptor to
the treatment plant, storm weather overflows to a watercourse through a CSO.

The fleet scan (2026-08-12) measured what is published: **no supported city publishes a CSO
structure**, and two publish interceptors. So in nearly every build the wastewater system is
terminated by a **synthetic interceptor / treatment-plant boundary outfall** — a node we
invented, 0.5 m below the lowest node of its component, carrying no overflow behaviour and
labelled `synthetic` in provenance.

Two consequences to be aware of:

- **CSO discharge is identically zero** in these models. There is no overflow structure, so
  there is nothing to overflow through. A combined model here answers questions about
  conveyance, not about overflow volumes.
- **A wastewater system is never given a storm outfall.** Ottawa publishes 13 outfalls and
  not one of them takes combined flow; reusing one would fabricate a destination the city
  does not have and let the model answer questions about it.

## Sewer service areas: what they are and are not (ADR 0029 Q1)

A service area is **not a watershed**. Sewage reaches a pipe through a lateral connection,
not by flowing over the ground, so a service-area boundary follows parcels and connections
and may cross a topographic divide without anything being wrong. It never becomes a SWMM
subcatchment; it becomes node loading.

Seeds are chosen by evidence, best first, and the choice is recorded:

- **Lateral endpoints** where a city publishes laterals (16 of the fleet do) — a lateral
  states which property feeds which main. An endpoint more than 60 m from any node is
  not treated as a connection to it: beyond a block width the pairing is a guess, and a
  guessed connection routes a household to the wrong sewer.
- **Manholes** otherwise — these say only where the network is, which is a weaker claim,
  and the diagnostics say so.
- Laterals that are published but never snap are reported **differently** from laterals that
  were never published. The first usually means a city's lateral and main layers disagree
  about where its network is.
