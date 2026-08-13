"""Live street source: OSM via osmnx → an undirected networkx graph (x, y per node),
then DEM elevation sampling so `network.synthesise_network` can run. The synthesis core
stays osmnx-free and offline-testable; this adapter is the only osmnx user."""
import threading

import networkx as nx

# osmnx settings (cache folder, use_cache) are process-GLOBAL, and the poisoned-cache
# recovery below deletes a directory other tasks may be reading (F-013): one lock
# serialises every OSM street/building fetch in this process. Coarse but correct — the
# real fix (per-task cache dirs / process isolation) rides the hosted-mode track (#8).
_OSM_LOCK = threading.RLock()

from swmmcanada.network.errors import NetworkError


# A cached Overpass answer below this many street nodes gets ONE cache-bypassed recheck:
# under load, Overpass returns HTTP 200 with PARTIAL data (server-side timeout), osmnx
# builds a tiny graph without raising, and the cache then poisons every rebuild of that
# bbox forever (observed live: a dense Duncan block cached as a 6-node graph). Genuinely
# tiny rural boxes just pay one extra Overpass call.
MIN_PLAUSIBLE_NODES = 16

# Overpass is a volunteer service with several interchangeable mirrors serving the same OSM
# data. Naming one of them made it a single point of failure for the primary delineation
# method: when overpass-api.de refused connections, every city in the fleet lost its streets
# and dropped to a coarser fallback. Tried in order, first answer wins. Whatever the process
# already has configured goes first, so an operator override still takes precedence.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
    "https://overpass.osm.ch/api",
)


#: How long to spend asking Overpass whether it has a slot. The answer is a courtesy check,
#: not the query — if the status page is slow we are better off attempting the query, which
#: the server will refuse with 429 if we are genuinely over quota.
STATUS_TIMEOUT_S = 10


def _overpass_status(base_endpoint: str) -> str:
    """The server's status page, or '' if it cannot be read."""
    import requests

    try:
        return requests.get(f"{base_endpoint}/status", timeout=STATUS_TIMEOUT_S).text
    except Exception:
        return ""


def _slots_free(status_text: str):
    """True/False if the status says whether a slot is free, None if it cannot be read.

    osmnx asks this question itself before every query, but cannot parse the answer Overpass
    currently gives, and recurses on it: measured, the same downtown query returns in 1.7 s
    with that handshake disabled and never returns with it enabled. Reading it here keeps the
    courtesy — the server is still asked, and a refusal is still honoured — without a
    client-side loop that has no exit.
    """
    import re

    m = re.search(r"(\d+)\s+slots?\s+available\s+now", status_text or "")
    if m:
        return int(m.group(1)) > 0
    if "Slot available after" in (status_text or ""):
        return False
    return None


def _endpoints(configured):
    """The configured endpoint first, then the mirrors it is not."""
    rest = [u for u in OVERPASS_MIRRORS if u != configured]
    return [configured, *rest] if configured else list(OVERPASS_MIRRORS)


def fetch_street_graph(bbox_wgs84) -> nx.Graph:
    """bbox = (minlon, minlat, maxlon, maxlat). Returns an undirected graph with node x/y
    (lon/lat) and edge length (m)."""
    import shutil
    import tempfile
    from pathlib import Path

    import osmnx as ox

    # osmnx caches Overpass responses to ./cache RELATIVE TO THE CWD by default — a served
    # worker's cwd may be read-only, killing every synthesis build at the STREETS stage
    # ([Errno 13] Permission denied: 'cache'; found by the first out-of-8-cities build from
    # the web UI). Cache explicitly in the system temp dir: always writable, and shared
    # across builds, which is kinder to Overpass than disabling the cache.
    with _OSM_LOCK:
        cache = Path(tempfile.gettempdir()) / "swmmcanada-osmnx-cache"
        cache.mkdir(parents=True, exist_ok=True)
        ox.settings.cache_folder = str(cache)

        prior_url = getattr(ox.settings, "overpass_url", None)
        prior_limit = getattr(ox.settings, "overpass_rate_limit", True)
        g, unreachable = None, None
        try:
            for url in _endpoints(prior_url):
                ox.settings.overpass_url = url
                # Believe the server about its own capacity. Only when it says a slot is
                # occupied do we hand the wait back to osmnx, which handles that case fine.
                ox.settings.overpass_rate_limit = (_slots_free(_overpass_status(url)) is False)
                try:
                    # A cached answer is keyed on the query URL, so the configured endpoint
                    # going first is also what keeps earlier builds' cache reachable.
                    g = _graph_from_bbox(ox, bbox_wgs84, use_cache=True)
                    break
                except Exception as exc:
                    unreachable = exc
        finally:
            # osmnx settings are process-global: a mirror reached here must not redirect
            # every later build in this worker, nor leave the rate limit as we set it.
            ox.settings.overpass_url = prior_url
            ox.settings.overpass_rate_limit = prior_limit
        if g is None:
            raise unreachable

        if g.number_of_nodes() < MIN_PLAUSIBLE_NODES:
            try:
                fresh = _graph_from_bbox(ox, bbox_wgs84, use_cache=False)
            except Exception:
                # Overpass is unreachable. The recheck exists to improve on what we have, so
                # it must never be a second way to lose it: a small graph beats no graph,
                # because no streets means the plan falls back to a coarser unit than the
                # one a municipality would draw. A genuinely poisoned cache stays poisoned
                # until Overpass answers again, which the node-count guard will catch then.
                fresh = g
            if fresh.number_of_nodes() > g.number_of_nodes():
                # The cached answer was poisoned (partial Overpass response): trust the
                # live one and drop the cache so future builds re-cache good data.
                shutil.rmtree(cache, ignore_errors=True)
                cache.mkdir(parents=True, exist_ok=True)
                g = fresh

    if g.number_of_nodes() < 2:
        raise NetworkError("OSM returned too few street nodes for this AOI.")
    return g


def _graph_from_bbox(ox, bbox_wgs84, *, use_cache: bool) -> nx.Graph:
    left, bottom, right, top = bbox_wgs84
    prior = ox.settings.use_cache
    ox.settings.use_cache = use_cache
    try:
        g_osm = ox.graph_from_bbox(bbox=(left, bottom, right, top), network_type="drive")
    finally:
        ox.settings.use_cache = prior

    g = nx.Graph()
    for n, d in g_osm.nodes(data=True):
        g.add_node(n, x=float(d["x"]), y=float(d["y"]))
    for u, v, d in g_osm.edges(data=True):
        if g.has_edge(u, v):
            continue
        g.add_edge(u, v, length=float(d.get("length") or 0.0))
    return g


def sample_elevations(graph: nx.Graph, dem_path) -> nx.Graph:
    """Annotate each node with `elev` sampled from the DEM; drop nodes outside coverage."""
    import rasterio
    from pyproj import Transformer

    nodes = list(graph.nodes())
    if not nodes:
        return graph
    with rasterio.open(dem_path) as src:
        tr = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        coords = [tr.transform(graph.nodes[n]["x"], graph.nodes[n]["y"]) for n in nodes]
        nodata = src.nodata
        drop = []
        for n, val in zip(nodes, src.sample(coords)):
            v = float(val[0])
            if v != v or (nodata is not None and v == nodata) or v < -1000.0:
                drop.append(n)
            else:
                graph.nodes[n]["elev"] = v
        graph.remove_nodes_from(drop)
    return graph


def fetch_building_footprints(bbox_wgs84):
    """OSM building footprints inside the bbox (EPSG:4326 polygons), for the service-area
    evidence test (ADR 0017: a block interior with buildings is lots, not wilderness).
    Graceful: any failure returns [] — buildings refine the mask, they never block a build."""
    import osmnx as ox

    try:
        left, bottom, right, top = bbox_wgs84
        with _OSM_LOCK:
            gdf = ox.features_from_bbox(bbox=(left, bottom, right, top), tags={"building": True})
        return [g for g in gdf.geometry if g is not None and g.geom_type in ("Polygon", "MultiPolygon")]
    except Exception:
        return []
