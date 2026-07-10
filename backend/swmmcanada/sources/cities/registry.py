"""City registry — the ONE place a real-network city is wired into the pipeline.

Each entry is a ``CitySpec``: coverage bbox (how the AOI dispatcher picks the city), the
city's metric CRS, and three callables that hide the adapter's fetch/build composition
(some builders take the fetch dict whole, others unpack it — that variance stays here).
Adding city #9 = write its adapter module + append ONE spec below; ``pipeline.py`` is
untouched (ADR 0006's "a new city is mostly a thin field mapping", now enforced).
"""
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from swmmcanada.sources.cities import base
from swmmcanada.sources.cities.calgary import (
    build_calgary_network, fetch_calgary_land, fetch_calgary_sanitary, fetch_calgary_storm,
)
from swmmcanada.sources.cities.kelowna import (
    build_kelowna_network, fetch_kelowna_land, fetch_kelowna_sanitary, fetch_kelowna_storm,
)
from swmmcanada.sources.cities.kitchener import (
    build_kitchener_network, fetch_kitchener_land, fetch_kitchener_sanitary, fetch_kitchener_storm,
)
from swmmcanada.sources.cities.london import (
    build_london_network, fetch_london_land, fetch_london_sanitary, fetch_london_storm,
)
from swmmcanada.sources.cities.ottawa import (
    build_ottawa_network, fetch_ottawa_land, fetch_ottawa_sanitary, fetch_ottawa_storm,
)
from swmmcanada.sources.cities.regina import (
    build_regina_network, fetch_regina_land, fetch_regina_sanitary, fetch_regina_storm,
)
from swmmcanada.sources.cities.surrey import (
    build_surrey_network, fetch_surrey_land, fetch_surrey_sanitary, fetch_surrey_storm,
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
        coverage=(-123.00, 49.00, -122.69, 49.22), sub_crs="EPSG:32610",
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


def city_for_point(lon: float, lat: float) -> Optional[CitySpec]:
    """The city whose coverage bbox contains the point, else None. First match wins;
    coverage boxes must not overlap (same rule the old pipeline table had)."""
    for spec in CITIES:
        lo1, la1, lo2, la2 = spec.coverage
        if lo1 <= lon <= lo2 and la1 <= lat <= la2:
            return spec
    return None


def city_spec(key: str) -> CitySpec:
    """Look up a spec by its stable key; raises KeyError with the known keys listed."""
    for spec in CITIES:
        if spec.key == key:
            return spec
    raise KeyError(f"Unknown city {key!r} — known: {', '.join(s.key for s in CITIES)}")
