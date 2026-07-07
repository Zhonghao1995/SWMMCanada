"""Street fetch resilience: a poisoned (partial-Overpass) cached answer triggers ONE
cache-bypassed recheck; the richer live graph wins and the cache is dropped."""
import sys
import types

import networkx as nx
import pytest


def _osm_graph(n_nodes):
    g = nx.MultiDiGraph()
    for i in range(n_nodes):
        g.add_node(i, x=-123.0 + i * 1e-4, y=48.0)
    for i in range(n_nodes - 1):
        g.add_edge(i, i + 1, length=10.0)
    return g


class _FakeOx(types.ModuleType):
    def __init__(self, responses):
        super().__init__("osmnx")
        self.settings = types.SimpleNamespace(cache_folder="", use_cache=True)
        self._responses = list(responses)
        self.calls = []

    def graph_from_bbox(self, *, bbox, network_type):
        self.calls.append(self.settings.use_cache)
        return self._responses.pop(0)


def _fetch_with(monkeypatch, responses):
    fake = _FakeOx(responses)
    monkeypatch.setitem(sys.modules, "osmnx", fake)
    from swmmcanada.sources.streets_osm import fetch_street_graph

    return fake, fetch_street_graph((-123.01, 47.99, -122.99, 48.01))


def test_poisoned_sparse_cache_is_rechecked_and_replaced(monkeypatch):
    fake, g = _fetch_with(monkeypatch, [_osm_graph(6), _osm_graph(59)])
    assert g.number_of_nodes() == 59
    assert fake.calls == [True, False]        # second attempt bypassed the cache


def test_genuinely_tiny_area_keeps_the_consistent_answer(monkeypatch):
    fake, g = _fetch_with(monkeypatch, [_osm_graph(6), _osm_graph(6)])
    assert g.number_of_nodes() == 6           # rural box: same answer both times, kept
    assert fake.calls == [True, False]


def test_plausible_graph_never_refetches(monkeypatch):
    fake, g = _fetch_with(monkeypatch, [_osm_graph(59)])
    assert g.number_of_nodes() == 59
    assert fake.calls == [True]               # one call only


def test_empty_graph_still_raises(monkeypatch):
    from swmmcanada.network.errors import NetworkError
    with pytest.raises(NetworkError):
        _fetch_with(monkeypatch, [_osm_graph(1), _osm_graph(1)])
