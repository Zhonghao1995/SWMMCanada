"""Live street source: OSM via osmnx → an undirected networkx graph (x, y per node),
then DEM elevation sampling so `network.synthesise_network` can run. The synthesis core
stays osmnx-free and offline-testable; this adapter is the only osmnx user."""
import networkx as nx

from swmmcanada.network.errors import NetworkError


def fetch_street_graph(bbox_wgs84) -> nx.Graph:
    """bbox = (minlon, minlat, maxlon, maxlat). Returns an undirected graph with node x/y
    (lon/lat) and edge length (m)."""
    import osmnx as ox

    left, bottom, right, top = bbox_wgs84
    g_osm = ox.graph_from_bbox(bbox=(left, bottom, right, top), network_type="drive")

    g = nx.Graph()
    for n, d in g_osm.nodes(data=True):
        g.add_node(n, x=float(d["x"]), y=float(d["y"]))
    for u, v, d in g_osm.edges(data=True):
        if g.has_edge(u, v):
            continue
        g.add_edge(u, v, length=float(d.get("length") or 0.0))

    if g.number_of_nodes() < 2:
        raise NetworkError("OSM returned too few street nodes for this AOI.")
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
