"""City registry — the ONE place a real-network city is wired into the pipeline.

Each entry is a ``CitySpec``: coverage bbox (how the AOI dispatcher picks the city), the
city's metric CRS, and three callables that hide the adapter's fetch/build composition
(some builders take the fetch dict whole, others unpack it — that variance stays here).
Adding city #9 = write its adapter module + append ONE spec below; ``pipeline.py`` is
untouched (ADR 0006's "a new city is mostly a thin field mapping", now enforced).
"""
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.abbotsford import (
    build_abbotsford_network, fetch_abbotsford_land, fetch_abbotsford_sanitary,
    fetch_abbotsford_storm,
)
from swmmcanada.sources.cities.barrie import (
    build_barrie_network, fetch_barrie_land, fetch_barrie_sanitary, fetch_barrie_storm,
)
from swmmcanada.sources.cities.burnaby import (
    build_burnaby_network, fetch_burnaby_land, fetch_burnaby_sanitary, fetch_burnaby_storm,
)
from swmmcanada.sources.cities.calgary import (
    build_calgary_network, fetch_calgary_land, fetch_calgary_sanitary, fetch_calgary_storm,
)
from swmmcanada.sources.cities.chilliwack import (
    build_chilliwack_network, fetch_chilliwack_land, fetch_chilliwack_sanitary,
    fetch_chilliwack_storm,
)
from swmmcanada.sources.cities.coquitlam import (
    build_coquitlam_network, fetch_coquitlam_land, fetch_coquitlam_sanitary,
    fetch_coquitlam_storm,
)
from swmmcanada.sources.cities.delta import (
    build_delta_network, fetch_delta_land, fetch_delta_sanitary, fetch_delta_storm,
)
from swmmcanada.sources.cities.esquimalt import (
    build_esquimalt_network, fetch_esquimalt_land, fetch_esquimalt_sanitary,
    fetch_esquimalt_storm,
)
from swmmcanada.sources.cities.kamloops import (
    build_kamloops_network, fetch_kamloops_land, fetch_kamloops_sanitary,
    fetch_kamloops_storm,
)
from swmmcanada.sources.cities.kelowna import (
    build_kelowna_network, fetch_kelowna_land, fetch_kelowna_sanitary, fetch_kelowna_storm,
)
from swmmcanada.sources.cities.kingston import (
    build_kingston_network, fetch_kingston_land, fetch_kingston_storm,
)
from swmmcanada.sources.cities.kitchener import (
    build_kitchener_network, fetch_kitchener_land, fetch_kitchener_sanitary, fetch_kitchener_storm,
)
from swmmcanada.sources.cities.langley import (
    build_langley_network, fetch_langley_land, fetch_langley_sanitary, fetch_langley_storm,
)
from swmmcanada.sources.cities.london import (
    build_london_network, fetch_london_land, fetch_london_sanitary, fetch_london_storm,
)
from swmmcanada.sources.cities.moncton import (
    build_moncton_network, fetch_moncton_land, fetch_moncton_sanitary, fetch_moncton_storm,
)
from swmmcanada.sources.cities.nanaimo import (
    build_nanaimo_network, fetch_nanaimo_land, fetch_nanaimo_sanitary, fetch_nanaimo_storm,
)
from swmmcanada.sources.cities.newwestminster import (
    build_newwestminster_network, fetch_newwestminster_land, fetch_newwestminster_sanitary,
    fetch_newwestminster_storm,
)
from swmmcanada.sources.cities.northvandistrict import (
    build_northvandistrict_network, fetch_northvandistrict_land,
    fetch_northvandistrict_sanitary, fetch_northvandistrict_storm,
)
from swmmcanada.sources.cities.ottawa import (
    build_ottawa_network, fetch_ottawa_land, fetch_ottawa_sanitary, fetch_ottawa_storm,
)
from swmmcanada.sources.cities.penticton import (
    build_penticton_network, fetch_penticton_land, fetch_penticton_sanitary,
    fetch_penticton_storm,
)
from swmmcanada.sources.cities.peterborough import (
    build_peterborough_network, fetch_peterborough_land, fetch_peterborough_sanitary,
    fetch_peterborough_storm,
)
from swmmcanada.sources.cities.portcoquitlam import (
    build_portcoquitlam_network, fetch_portcoquitlam_land, fetch_portcoquitlam_sanitary,
    fetch_portcoquitlam_storm,
)
from swmmcanada.sources.cities.regina import (
    build_regina_network, fetch_regina_land, fetch_regina_sanitary, fetch_regina_storm,
)
from swmmcanada.sources.cities.sarnia import (
    build_sarnia_network, fetch_sarnia_land, fetch_sarnia_sanitary, fetch_sarnia_storm,
)
from swmmcanada.sources.cities.saskatoon import (
    build_saskatoon_network, fetch_saskatoon_land, fetch_saskatoon_sanitary,
    fetch_saskatoon_storm,
)
from swmmcanada.sources.cities.reykjavik import (
    build_reykjavik_network, fetch_reykjavik_land, fetch_reykjavik_sanitary, fetch_reykjavik_storm,
)
from swmmcanada.sources.cities.strathcona import (
    build_strathcona_network, fetch_strathcona_land, fetch_strathcona_sanitary,
    fetch_strathcona_storm,
)
from swmmcanada.sources.cities.sudbury import (
    build_sudbury_network, fetch_sudbury_land, fetch_sudbury_sanitary, fetch_sudbury_storm,
)
from swmmcanada.sources.cities.surrey import (
    build_surrey_network, fetch_surrey_land, fetch_surrey_sanitary, fetch_surrey_storm,
)
from swmmcanada.sources.cities.vancouver import (
    build_vancouver_network, fetch_vancouver_land, fetch_vancouver_sanitary,
    fetch_vancouver_storm,
)
from swmmcanada.sources.cities.toronto import (
    build_toronto_network, fetch_toronto_land, fetch_toronto_sanitary, fetch_toronto_storm,
)
from swmmcanada.sources.cities.whitby import (
    build_whitby_network, fetch_whitby_land, fetch_whitby_storm,
)
from swmmcanada.sources.cities.windsor import (
    build_windsor_network, fetch_windsor_land, fetch_windsor_sanitary, fetch_windsor_storm,
)
from swmmcanada.sources.cities.whiterock import (
    build_whiterock_network, fetch_whiterock_land, fetch_whiterock_sanitary,
    fetch_whiterock_storm,
)
from swmmcanada.sources.cities.victoria import (
    build_victoria_network, fetch_victoria_land, fetch_victoria_sanitary, fetch_victoria_storm,
)

Bbox = Tuple[float, float, float, float]
# (bbox, client) -> base.NetworkResult — the adapter's fetch+build composed.
NetworkFn = Callable[[Bbox, Optional[object]], "base.NetworkResult"]
# (bbox, client) -> {"catchbasins": [...], "parcels": [...], "buildings": [...]}
LandFn = Callable[[Bbox, Optional[object]], dict]


@dataclass(frozen=True)
class CitySpec:
    """Everything the pipeline needs to build from one real-network city (ADR 0006)."""

    key: str                    # stable id ("victoria") — provenance, tests, build_city()
    label: str                  # human label for the mode string ("Victoria, BC")
    coverage: Bbox              # coarse dispatch bbox (min_lon, min_lat, max_lon, max_lat)
    sub_crs: str                # the city's metric CRS (subcatchments, coordinates)
    network_source: str         # provenance string shipped in the result package
    storm: NetworkFn
    land: LandFn
    sanitary: Optional[NetworkFn] = None   # None = city publishes no sanitary layer


CITIES: Tuple[CitySpec, ...] = (
    # Victoria (ADR 0004/0005): explicit node-id topology; parcels + buildings published.
    CitySpec(
        key="victoria", label="Victoria, BC",
        coverage=(-123.43, 48.40, -123.33, 48.47), sub_crs="EPSG:32610",
        network_source="City of Victoria storm drain (real municipal network)",
        storm=lambda bbox, client: build_victoria_network(**fetch_victoria_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_victoria_land(bbox, client=client),
        sanitary=lambda bbox, client: build_victoria_network(**fetch_victoria_sanitary(bbox, client=client)),
    ),
    # Ottawa: geometry-inferred topology; no public parcels/buildings, so subcatchments seed
    # on real catch basins with land-cover imperviousness (no parcel/building override).
    CitySpec(
        key="ottawa", label="Ottawa, ON",
        coverage=(-76.05, 45.15, -75.40, 45.55), sub_crs="EPSG:32618",
        network_source="City of Ottawa storm sewer (real municipal network)",
        storm=lambda bbox, client: build_ottawa_network(fetch_ottawa_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_ottawa_land(bbox, client=client),
        sanitary=lambda bbox, client: build_ottawa_network(fetch_ottawa_sanitary(bbox, client=client)),
    ),
    # London: explicit node-id topology (UpstreamID/DownstreamID -> GIS_FeatureKey);
    # parcels + buildings published.
    CitySpec(
        key="london", label="London, ON",
        coverage=(-81.38, 42.86, -81.12, 43.06), sub_crs="EPSG:32617",
        network_source="City of London storm sewer (real municipal network)",
        storm=lambda bbox, client: build_london_network(**fetch_london_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_london_land(bbox, client=client),
        sanitary=lambda bbox, client: build_london_network(**fetch_london_sanitary(bbox, client=client)),
    ),
    # Kitchener–Waterloo (Region of Waterloo): explicit integer manhole-id topology; no parcel
    # polygons published, so subcatchments fall back to catch-basin Voronoi (buildings available).
    CitySpec(
        key="kitchener", label="Kitchener–Waterloo, ON",
        coverage=(-80.70, 43.30, -80.20, 43.60), sub_crs="EPSG:32617",
        network_source="Region of Waterloo storm sewer (real municipal network)",
        storm=lambda bbox, client: build_kitchener_network(**fetch_kitchener_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_kitchener_land(bbox, client=client),
        sanitary=lambda bbox, client: build_kitchener_network(**fetch_kitchener_sanitary(bbox, client=client)),
    ),
    # Calgary: geometry-inferred topology; parcels + buildings published.
    CitySpec(
        key="calgary", label="Calgary, AB",
        coverage=(-114.32, 50.84, -113.86, 51.21), sub_crs="EPSG:32611",
        network_source="City of Calgary storm sewer (real municipal network)",
        storm=lambda bbox, client: build_calgary_network(fetch_calgary_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_calgary_land(bbox, client=client),
        sanitary=lambda bbox, client: build_calgary_network(fetch_calgary_sanitary(bbox, client=client)),
    ),
    # Surrey: geometry-inferred topology (gravity mains); parcels (Lot) + buildings published.
    CitySpec(
        key="surrey", label="Surrey, BC",
        # Tightened 2026-07-28 (wave 2): the old box (-123.00..-122.69 x 49.00..49.22)
        # annexed North Delta, Queensborough/New West and south Burnaby — areas whose
        # assets Surrey's feed does not carry. True envelope: Scott Rd west, Fraser north.
        coverage=(-122.89, 49.00, -122.68, 49.205), sub_crs="EPSG:32610",
        network_source="City of Surrey storm drainage (real municipal network)",
        storm=lambda bbox, client: build_surrey_network(fetch_surrey_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_surrey_land(bbox, client=client),
        sanitary=lambda bbox, client: build_surrey_network(fetch_surrey_sanitary(bbox, client=client)),
    ),
    # Kelowna: geometry-inferred topology (node inverts back-filled from pipe ends);
    # parcels + buildings published.
    CitySpec(
        key="kelowna", label="Kelowna, BC",
        coverage=(-119.60, 49.77, -119.28, 50.05), sub_crs="EPSG:32611",
        network_source="City of Kelowna storm sewer (real municipal network)",
        storm=lambda bbox, client: build_kelowna_network(fetch_kelowna_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_kelowna_land(bbox, client=client),
        sanitary=lambda bbox, client: build_kelowna_network(fetch_kelowna_sanitary(bbox, client=client)),
    ),
    # Regina: geometry-inferred topology (active gravity lines; node inverts back-filled from
    # pipe ends); parcels + building footprints published.
    CitySpec(
        key="regina", label="Regina, SK",
        coverage=(-104.80, 50.35, -104.45, 50.55), sub_crs="EPSG:32613",
        network_source="City of Regina storm sewer (real municipal network)",
        storm=lambda bbox, client: build_regina_network(fetch_regina_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_regina_land(bbox, client=client),
        sanitary=lambda bbox, client: build_regina_network(fetch_regina_sanitary(bbox, client=client)),
    ),
    # Vancouver (ADR 0020): explicit frommh/tomh manhole topology from the VanMap public
    # FeatureServer (real diameter/slope/material; combined mains join the storm system);
    # rim-anchored inverts (no inverts published); land kit from the open-data portal.
    CitySpec(
        key="vancouver", label="Vancouver, BC",
        # East edge tightened to Boundary Road (wave 2) so Burnaby's box can sit flush.
        # North edge tightened to the Burrard shore/Prospect Point (wave 2) so the
        # District of North Vancouver's box can sit above the Inlet.
        coverage=(-123.26, 49.19, -123.024, 49.315), sub_crs="EPSG:32610",
        network_source="City of Vancouver sewer network via VanMap (real municipal network; "
                       "storm + combined mains)",
        storm=lambda bbox, client: build_vancouver_network(fetch_vancouver_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_vancouver_land(bbox, client=client),
        sanitary=lambda bbox, client: build_vancouver_network(fetch_vancouver_sanitary(bbox, client=client)),
    ),
    # Abbotsford (wave 2): monolithic FeatureServer, coded domains, 0/-1 sentinels.
    CitySpec(
        key="abbotsford", label="Abbotsford, BC",
        coverage=(-122.44, 49.00, -122.10, 49.14), sub_crs="EPSG:32610",
        network_source="City of Abbotsford drainage open data (real municipal network)",
        storm=lambda bbox, client: build_abbotsford_network(fetch_abbotsford_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_abbotsford_land(bbox, client=client),
        sanitary=lambda bbox, client: build_abbotsford_network(fetch_abbotsford_sanitary(bbox, client=client)),
    ),
    # Barrie (wave 2): geometry topology + FROM/TO_ID labels; device layer carries rims,
    # outfall candidates and catch-basin seeds; real non-circular sections (#130).
    CitySpec(
        key="barrie", label="Barrie, ON",
        coverage=(-79.74, 44.30, -79.61, 44.42), sub_crs="EPSG:32617",
        network_source="City of Barrie storm open data (real municipal network)",
        storm=lambda bbox, client: build_barrie_network(fetch_barrie_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_barrie_land(bbox, client=client),
        sanitary=lambda bbox, client: build_barrie_network(fetch_barrie_sanitary(bbox, client=client)),
    ),
    # Saskatoon (wave 2): FROMMH/TOMH labels (MH-prefixed), UPELEV/DOWNELEV inverts;
    # network from the public Core service (licence unstamped — provenance in DATA.md),
    # parcels from the official OD folder.
    CitySpec(
        key="saskatoon", label="Saskatoon, SK",
        coverage=(-106.83, 52.05, -106.50, 52.24), sub_crs="EPSG:32613",
        network_source="City of Saskatoon WSS public service (real municipal network; "
                       "public token-free service, licence unstamped)",
        storm=lambda bbox, client: build_saskatoon_network(fetch_saskatoon_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_saskatoon_land(bbox, client=client),
        sanitary=lambda bbox, client: build_saskatoon_network(fetch_saskatoon_sanitary(bbox, client=client)),
    ),
    # Burnaby (wave 2): UNITID labels + 65% inverts; no rim source (default depths).
    # Boundary slivers ceded: Boundary Rd -> Vancouver's box, North Rd -> Coquitlam's;
    # New Westminster's box NESTS inside this one (smallest-box dispatch).
    CitySpec(
        key="burnaby", label="Burnaby, BC",
        coverage=(-123.02, 49.176, -122.895, 49.30), sub_crs="EPSG:32610",
        network_source="City of Burnaby storm open data (real municipal network)",
        storm=lambda bbox, client: build_burnaby_network(fetch_burnaby_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_burnaby_land(bbox, client=client),
        sanitary=lambda bbox, client: build_burnaby_network(fetch_burnaby_sanitary(bbox, client=client)),
    ),
    # New Westminster (wave 2): combined city; manhole-INVERT lift carries the vertical.
    # Coverage box NESTS inside Burnaby's (smallest-box dispatch); the Sapperton sliver
    # east of -122.896 is ceded to keep Coquitlam's box untouched.
    CitySpec(
        key="newwestminster", label="New Westminster, BC",
        coverage=(-122.955, 49.179, -122.896, 49.235), sub_crs="EPSG:32610",
        network_source="City of New Westminster sewer open data (real municipal network; "
                       "storm + combined mains)",
        storm=lambda bbox, client: build_newwestminster_network(fetch_newwestminster_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_newwestminster_land(bbox, client=client),
        sanitary=lambda bbox, client: build_newwestminster_network(fetch_newwestminster_sanitary(bbox, client=client)),
    ),
    # Moncton (wave 2): first Atlantic city; STM+COMB join storm; MAINKEY labels;
    # ZTOPCOV rims. Public service outside the hub catalogue — provenance in DATA.md.
    CitySpec(
        key="moncton", label="Moncton, NB",
        coverage=(-64.95, 46.03, -64.69, 46.16), sub_crs="EPSG:32620",
        network_source="City of Moncton sewer public service (real municipal network; "
                       "public token-free service, licence unstamped)",
        storm=lambda bbox, client: build_moncton_network(fetch_moncton_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_moncton_land(bbox, client=client),
        sanitary=lambda bbox, client: build_moncton_network(fetch_moncton_sanitary(bbox, client=client)),
    ),
    # Nanaimo (wave 2): rims-on-row (ST/END_COVELV) + inverts on the pipe ends;
    # Inlet/Outlet layer unused (does not distinguish inlets — Barrie headwall lesson).
    CitySpec(
        key="nanaimo", label="Nanaimo, BC",
        coverage=(-124.05, 49.06, -123.81, 49.26), sub_crs="EPSG:32610",
        network_source="City of Nanaimo storm open data (real municipal network)",
        storm=lambda bbox, client: build_nanaimo_network(fetch_nanaimo_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_nanaimo_land(bbox, client=client),
        sanitary=lambda bbox, client: build_nanaimo_network(fetch_nanaimo_sanitary(bbox, client=client)),
    ),
    # Township of Langley (wave 2, tier 2): endpoint snapping; string diameters; the
    # nulls cluster at the table start. Box sits east of Surrey's, west of Abbotsford's.
    CitySpec(
        key="langley", label="Township of Langley, BC",
        coverage=(-122.679, 49.00, -122.45, 49.17), sub_crs="EPSG:32610",
        network_source="Township of Langley drainage open data (real municipal network)",
        storm=lambda bbox, client: build_langley_network(fetch_langley_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_langley_land(bbox, client=client),
        sanitary=lambda bbox, client: build_langley_network(fetch_langley_sanitary(bbox, client=client)),
    ),
    # Port Coquitlam (wave 2, tier 2): -99 sentinel; box NESTS inside Coquitlam's
    # (fourth production nesting).
    CitySpec(
        key="portcoquitlam", label="Port Coquitlam, BC",
        # West edge = the Coquitlam River, north edge 49.278 — Coquitlam Town Centre
        # (west of the river) must stay in Coquitlam's box.
        coverage=(-122.79, 49.225, -122.71, 49.278), sub_crs="EPSG:32610",
        network_source="City of Port Coquitlam drainage open data (real municipal network)",
        storm=lambda bbox, client: build_portcoquitlam_network(fetch_portcoquitlam_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_portcoquitlam_land(bbox, client=client),
        sanitary=lambda bbox, client: build_portcoquitlam_network(fetch_portcoquitlam_sanitary(bbox, client=client)),
    ),
    # Chilliwack (wave 2, tier 2): one SYM_TYPE-split symbol layer carries rims AND seeds;
    # WAF-fronted host (small pages).
    CitySpec(
        key="chilliwack", label="Chilliwack, BC",
        coverage=(-122.05, 49.05, -121.80, 49.22), sub_crs="EPSG:32610",
        network_source="City of Chilliwack storm open data (real municipal network)",
        storm=lambda bbox, client: build_chilliwack_network(fetch_chilliwack_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_chilliwack_land(bbox, client=client),
        sanitary=lambda bbox, client: build_chilliwack_network(fetch_chilliwack_sanitary(bbox, client=client)),
    ),
    # Delta (wave 2, tier 2): grounds-on-row; -99 sentinel with LEGAL negative inverts;
    # vertical datum CVD28GVRD2018 (no shim — DATA.md note). East edge cedes Scott Road
    # to stay disjoint from Surrey's box; north edge stops shy of Vancouver's.
    CitySpec(
        key="delta", label="Delta, BC",
        coverage=(-123.19, 48.98, -122.891, 49.175), sub_crs="EPSG:32610",
        network_source="City of Delta drainage open data (real municipal network)",
        storm=lambda bbox, client: build_delta_network(fetch_delta_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_delta_land(bbox, client=client),
        sanitary=lambda bbox, client: build_delta_network(fetch_delta_sanitary(bbox, client=client)),
    ),
    # Strathcona County (wave 2, tier 2): patchiest inverts of the wave; the discharge
    # layer mixes inlet-side structures (downhill-end filter).
    CitySpec(
        key="strathcona", label="Strathcona County, AB",
        coverage=(-113.42, 53.44, -112.80, 53.72), sub_crs="EPSG:32612",
        network_source="Strathcona County storm open data (real municipal network)",
        storm=lambda bbox, client: build_strathcona_network(fetch_strathcona_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_strathcona_land(bbox, client=client),
        sanitary=lambda bbox, client: build_strathcona_network(fetch_strathcona_sanitary(bbox, client=client)),
    ),
    # Greater Sudbury (wave 2, tier 2): STYPE-filtered gravity graph; (200,420) band.
    CitySpec(
        key="sudbury", label="Greater Sudbury, ON",
        coverage=(-81.20, 46.35, -80.80, 46.65), sub_crs="EPSG:32617",
        network_source="City of Greater Sudbury drainage open data (real municipal network)",
        storm=lambda bbox, client: build_sudbury_network(fetch_sudbury_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_sudbury_land(bbox, client=client),
        sanitary=lambda bbox, client: build_sudbury_network(fetch_sudbury_sanitary(bbox, client=client)),
    ),
    # Kamloops (wave 2, tier 2): endpoint snapping; the missing sentinel is literal 9999.
    CitySpec(
        key="kamloops", label="Kamloops, BC",
        coverage=(-120.53, 50.60, -120.17, 50.78), sub_crs="EPSG:32610",
        network_source="City of Kamloops drainage open data (real municipal network)",
        storm=lambda bbox, client: build_kamloops_network(fetch_kamloops_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_kamloops_land(bbox, client=client),
        sanitary=lambda bbox, client: build_kamloops_network(fetch_kamloops_sanitary(bbox, client=client)),
    ),
    # Esquimalt (wave 2): compass-wall manhole inverts lifted by pipe bearing; box
    # NESTS inside Victoria's (smallest-box dispatch).
    CitySpec(
        key="esquimalt", label="Esquimalt, BC",
        coverage=(-123.43, 48.42, -123.395, 48.46), sub_crs="EPSG:32610",
        network_source="Township of Esquimalt drain open data (real municipal network)",
        storm=lambda bbox, client: build_esquimalt_network(fetch_esquimalt_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_esquimalt_land(bbox, client=client),
        sanitary=lambda bbox, client: build_esquimalt_network(fetch_esquimalt_sanitary(bbox, client=client)),
    ),
    # Coquitlam (wave 2): geometry topology with real per-end inverts (95% populated) and
    # labelled ends; coverage = the city-boundary envelope (Cadastral layer 11 extent), just
    # above Surrey's box across the Fraser. Port Coquitlam will later NEST inside this box
    # (smallest-coverage-box dispatch).
    CitySpec(
        key="coquitlam", label="Coquitlam, BC",
        coverage=(-122.894, 49.221, -122.621, 49.352), sub_crs="EPSG:32610",
        network_source="City of Coquitlam drainage open data (real municipal network)",
        storm=lambda bbox, client: build_coquitlam_network(fetch_coquitlam_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_coquitlam_land(bbox, client=client),
        sanitary=lambda bbox, client: build_coquitlam_network(fetch_coquitlam_sanitary(bbox, client=client)),
    ),
    # Peterborough (wave 2): Saskatoon-family schema + a real discharge-point layer.
    CitySpec(
        key="peterborough", label="Peterborough, ON",
        coverage=(-78.42, 44.24, -78.22, 44.38), sub_crs="EPSG:32617",
        network_source="City of Peterborough storm open data (real municipal network)",
        storm=lambda bbox, client: build_peterborough_network(fetch_peterborough_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_peterborough_land(bbox, client=client),
        sanitary=lambda bbox, client: build_peterborough_network(fetch_peterborough_sanitary(bbox, client=client)),
    ),
    # Whitby (wave 2): one-layer city; CB-prefixed pipe endpoints are the seeds;
    # 31% inverts (0-sentinel) with gap-fill; no sanitary (Durham Region asset).
    CitySpec(
        key="whitby", label="Whitby, ON",
        coverage=(-78.99, 43.83, -78.89, 43.95), sub_crs="EPSG:32617",
        network_source="Town of Whitby storm open data (real municipal network)",
        storm=lambda bbox, client: build_whitby_network(fetch_whitby_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_whitby_land(bbox, client=client),
        sanitary=None,
    ),
    # Sarnia (wave 2): text-mm diameters, (150,250) invert band, MH-id labels.
    CitySpec(
        key="sarnia", label="Sarnia, ON",
        coverage=(-82.48, 42.92, -82.25, 43.03), sub_crs="EPSG:32617",
        network_source="City of Sarnia storm open data (real municipal network)",
        storm=lambda bbox, client: build_sarnia_network(fetch_sarnia_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_sarnia_land(bbox, client=client),
        sanitary=lambda bbox, client: build_sarnia_network(fetch_sarnia_sanitary(bbox, client=client)),
    ),
    # Penticton (wave 2): text-with-units diameters, trailing-code materials, Outlet layer.
    CitySpec(
        key="penticton", label="Penticton, BC",
        coverage=(-119.68, 49.44, -119.53, 49.53), sub_crs="EPSG:32611",
        network_source="City of Penticton storm open data (real municipal network)",
        storm=lambda bbox, client: build_penticton_network(fetch_penticton_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_penticton_land(bbox, client=client),
        sanitary=lambda bbox, client: build_penticton_network(fetch_penticton_sanitary(bbox, client=client)),
    ),
    # District of North Vancouver (wave 2, final city): download-and-cache SHP dumps;
    # box starts above the Burrard shore (Maplewood flats ceded to stay clear of
    # Vancouver's box); the western Capilano arm is out of the box for now.
    CitySpec(
        key="northvandistrict", label="District of North Vancouver, BC",
        coverage=(-123.046, 49.316, -122.93, 49.42), sub_crs="EPSG:32610",
        network_source="District of North Vancouver storm open data (real municipal "
                       "network; static SHP dump, download-and-cache)",
        storm=lambda bbox, client: build_northvandistrict_network(fetch_northvandistrict_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_northvandistrict_land(bbox, client=client),
        sanitary=lambda bbox, client: build_northvandistrict_network(fetch_northvandistrict_sanitary(bbox, client=client)),
    ),
    # Windsor (wave 2): first download-and-cache city (static ZIP dumps, no query API);
    # STORM+COMBINED join the storm graph.
    CitySpec(
        key="windsor", label="Windsor, ON",
        coverage=(-83.12, 42.23, -82.89, 42.36), sub_crs="EPSG:32617",
        network_source="City of Windsor sewer open data (real municipal network; "
                       "static ZIP dump, download-and-cache)",
        storm=lambda bbox, client: build_windsor_network(fetch_windsor_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_windsor_land(bbox, client=client),
        sanitary=lambda bbox, client: build_windsor_network(fetch_windsor_sanitary(bbox, client=client)),
    ),
    # Kingston (wave 2): richest link schema (3 node-id families); bimodal invert
    # sentinel band; no rims (default depths); no sanitary network published.
    CitySpec(
        key="kingston", label="Kingston, ON",
        coverage=(-76.69, 44.19, -76.37, 44.32), sub_crs="EPSG:32618",
        network_source="City of Kingston storm open data (real municipal network)",
        storm=lambda bbox, client: build_kingston_network(fetch_kingston_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_kingston_land(bbox, client=client),
        sanitary=None,
    ),
    # Toronto (wave 2): Toronto Water Ext View services; Combined joins storm (ADR 0021),
    # SAN is the tracer; MH-id labels; inlets seed subcatchments (no parcels/buildings).
    CitySpec(
        key="toronto", label="Toronto, ON",
        coverage=(-79.64, 43.58, -79.11, 43.86), sub_crs="EPSG:32617",
        network_source="Toronto Water sewer external-view services (real municipal network)",
        storm=lambda bbox, client: build_toronto_network(fetch_toronto_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_toronto_land(bbox, client=client),
        sanitary=lambda bbox, client: build_toronto_network(fetch_toronto_sanitary(bbox, client=client)),
    ),
    # White Rock (wave 2): rims-on-row; box NESTS inside Surrey's (smallest-box dispatch).
    CitySpec(
        key="whiterock", label="White Rock, BC",
        coverage=(-122.845, 49.005, -122.79, 49.045), sub_crs="EPSG:32610",
        network_source="City of White Rock storm open data (real municipal network)",
        storm=lambda bbox, client: build_whiterock_network(fetch_whiterock_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_whiterock_land(bbox, client=client),
        sanitary=lambda bbox, client: build_whiterock_network(fetch_whiterock_sanitary(bbox, client=client)),
    ),
    # Reykjavík (IS) — first non-Canadian city: geometry-inferred topology like Ottawa, but with
    # REAL surveyed inverts carried on the structure points (BOTNKODI) and snapped onto pipe ends.
    # National *fitjuskrá* schema shared across Icelandic municipalities. NB: the Canada geofence
    # below (preview/UX only) still rejects this AOI — widening it is deliberately out of scope for
    # this real-network-only adapter; city dispatch here is exact via the coverage bbox.
    CitySpec(
        key="reykjavik", label="Reykjavík, IS",
        coverage=(-22.05, 64.05, -21.60, 64.20), sub_crs="EPSG:3057",
        network_source="Veitur / Orkuveita Reykjavíkur fráveita via LÚKOR (real municipal network); "
                       "scaffolded against the shared-schema Kópavogur LÚKK open service pending "
                       "Reykjavík host confirmation",
        storm=lambda bbox, client: build_reykjavik_network(fetch_reykjavik_storm(bbox, client=client)),
        land=lambda bbox, client: fetch_reykjavik_land(bbox, client=client),
        sanitary=lambda bbox, client: build_reykjavik_network(fetch_reykjavik_sanitary(bbox, client=client)),
    ),
)


# Canada's coarse WGS84 envelope. The honest "is this even Canada" gate for
# preview/UX responses: deliberately generous (a northern-US border town can
# pass), because city dispatch stays exact via each spec's coverage bbox and
# the build itself fails on non-Canadian data. Downstream consumers (aiswmm's
# geofence pre-check) mirror this envelope; this is the authoritative copy.
CANADA_COARSE_BBOX: Bbox = (-141.1, 41.6, -52.5, 83.2)


def in_canada_coarse(lon: float, lat: float) -> bool:
    """Whether a point falls inside the coarse Canada envelope."""
    min_lon, min_lat, max_lon, max_lat = CANADA_COARSE_BBOX
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
# Vertical-data tier per city (the ASSUMPTIONS.md per-city table is the public,
# evidence-carrying copy — percentages measured on each city's recorded test AOI):
#   A — published inverts, <=10 % of nodes gap-filled
#   B — published inverts with real gaps (~10-35 % gap-filled)
#   C — vertical data thin (>35 % gap-filled, mostly DEM-anchored estimates)
# Every registry key MUST have an entry (enforced by test); adding a city without
# consciously choosing its tier is the failure mode this guards against.
DATA_TIERS: Dict[str, str] = {
    "victoria": "A", "ottawa": "A", "calgary": "A", "surrey": "A", "london": "A",
    "kitchener": "A", "kelowna": "A", "regina": "A", "coquitlam": "A", "saskatoon": "A",
    "kamloops": "A", "langley": "A", "sarnia": "A",
    "vancouver": "B", "barrie": "B", "abbotsford": "B", "toronto": "B",
    "peterborough": "B", "burnaby": "B", "newwestminster": "B", "penticton": "B",
    "esquimalt": "B", "moncton": "B", "delta": "B", "sudbury": "B", "chilliwack": "B",
    "portcoquitlam": "B", "windsor": "B",
    "kingston": "C", "nanaimo": "C", "whiterock": "C", "whitby": "C", "strathcona": "C",
    "northvandistrict": "C", "reykjavik": "C",
}


# Typical error of ESTIMATED node inverts per city, in metres: holdout MAE measured on
# the recorded test AOI at the city's own observed sparsity (mask published inverts,
# re-run the gap-fill, compare). The ASSUMPTIONS.md per-city table is the public copy.
# None = not measured on real data (Reykjavik's fixture is synthetic-schema).
# Every registry key MUST have an entry (enforced by test, like DATA_TIERS).
TYPICAL_INVERT_ERROR_M: Dict[str, Optional[float]] = {
    "abbotsford": 0.7, "barrie": 1.0, "burnaby": 1.8, "calgary": 0.4, "chilliwack": 0.3,
    "coquitlam": 0.8, "delta": 2.4, "esquimalt": 2.0, "kamloops": 2.1, "kelowna": 0.7,
    "kingston": 1.4, "kitchener": 0.9, "langley": 1.3, "london": 0.8, "moncton": 0.7,
    "nanaimo": 4.1, "newwestminster": 4.1, "northvandistrict": 1.7, "ottawa": 1.0,
    "penticton": 0.8, "peterborough": 0.5, "portcoquitlam": 0.4, "regina": 0.6,
    "reykjavik": None, "sarnia": 0.5, "saskatoon": 1.2, "strathcona": 1.6,
    "sudbury": 0.9, "surrey": 0.7, "toronto": 0.8, "vancouver": 1.3, "victoria": 1.3,
    "whitby": 0.7, "whiterock": 3.3, "windsor": 0.9,
}


def coverage_summary() -> list:
    """The public shape of the registry for the /coverage endpoint.

    Everything here is already public in result packages (labels,
    provenance strings); this is discovery metadata, not new exposure.
    """
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "coverage_bbox": list(spec.coverage),
            "has_sanitary": spec.sanitary is not None,
            "data_tier": DATA_TIERS[spec.key],
            "typical_invert_error_m": TYPICAL_INVERT_ERROR_M[spec.key],
        }
        for spec in CITIES
    ]


def city_for_point(lon: float, lat: float) -> Optional[CitySpec]:
    """The city whose coverage bbox contains the point, else None.

    Smallest containing bbox wins (ties: registry order). Adjacent-municipality regions
    (e.g. Metro Vancouver) make strict non-overlap impossible with axis-aligned boxes —
    a suburb's tight box can sit inside a neighbour's natural envelope (White Rock inside
    Surrey's, Port Coquitlam inside Coquitlam's). Nesting is therefore legal; the tighter
    box is always the more specific claim. With disjoint boxes this reduces to the old
    first-match behaviour."""
    best: Optional[CitySpec] = None
    best_area = float("inf")
    for spec in CITIES:
        lo1, la1, lo2, la2 = spec.coverage
        if lo1 <= lon <= lo2 and la1 <= lat <= la2:
            area = (lo2 - lo1) * (la2 - la1)
            if area < best_area:
                best, best_area = spec, area
    return best


def city_spec(key: str) -> CitySpec:
    """Look up a spec by its stable key; raises KeyError with the known keys listed."""
    for spec in CITIES:
        if spec.key == key:
            return spec
    raise KeyError(f"Unknown city {key!r} — known: {', '.join(s.key for s in CITIES)}")
