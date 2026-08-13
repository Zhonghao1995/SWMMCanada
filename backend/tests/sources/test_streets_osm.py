"""Street fetch resilience: a poisoned (partial-Overpass) cached answer triggers ONE
cache-bypassed recheck; the richer live graph wins and the cache is dropped."""
import sys
import types

import networkx as nx
import pytest

from swmmcanada.sources import streets_osm


@pytest.fixture(autouse=True)
def _offline_status(monkeypatch):
    """No test in this module may ask the real Overpass whether it has a slot.

    The fetcher consults the status page before querying; left live, every test here would
    make three HTTP calls to a volunteer service. Tests that care about the answer set their
    own stub over this one.
    """
    monkeypatch.setattr(streets_osm, "_overpass_status", lambda _u: "2 slots available now.")


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


class TestAnOutageMustNotCostUsTheCachedStreets:
    """The recheck that guards against a poisoned cache must not become a second way to fail.

    A small cached graph triggers one cache-bypassed recheck. When Overpass is unreachable
    that recheck raises, and the exception used to escape — taking with it the perfectly
    usable graph already in hand. The caller then has no streets at all and the plan falls
    back to a materially coarser method, which is a real cost: frontage splitting is the
    unit a municipality would draw, and it silently disappears whenever a third party blinks.
    """

    def test_the_cached_graph_survives_an_unreachable_recheck(self, monkeypatch):
        cached = nx.Graph()
        for i in range(5):                      # under MIN_PLAUSIBLE_NODES, so a recheck runs
            cached.add_node(i, x=-123.0 - i / 1000, y=48.4)
        for i in range(4):
            cached.add_edge(i, i + 1, length=50.0)

        def fake(_ox, _bbox, *, use_cache):
            if use_cache:
                return cached
            raise ConnectionError("overpass-api.de: Max retries exceeded")

        monkeypatch.setattr(streets_osm, "_graph_from_bbox", fake)
        got = streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))
        assert got.number_of_nodes() == 5

    def test_a_genuinely_poisoned_cache_is_still_replaced(self, monkeypatch):
        """The recheck keeps doing its job when Overpass answers."""
        small, full = nx.Graph(), nx.Graph()
        for i in range(5):
            small.add_node(i, x=-123.0, y=48.4)
        for i in range(40):
            full.add_node(i, x=-123.0, y=48.4)

        monkeypatch.setattr(streets_osm, "_graph_from_bbox",
                            lambda _ox, _bbox, *, use_cache: small if use_cache else full)
        got = streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))
        assert got.number_of_nodes() == 40

    def test_no_cache_and_no_network_still_raises(self, monkeypatch):
        """Nothing in hand and nothing reachable is a real failure, and must stay one."""
        def fake(_ox, _bbox, *, use_cache):
            raise ConnectionError("overpass-api.de: Max retries exceeded")

        monkeypatch.setattr(streets_osm, "_graph_from_bbox", fake)
        with pytest.raises(ConnectionError):
            streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))


class TestOneHostIsNotTheWholeOfOSM:
    """The frontage split is the municipal unit; it must not hinge on one server being up.

    Overpass is a public volunteer service with several interchangeable mirrors serving the
    same OSM data. Pointing at exactly one of them meant an outage there removed the primary
    delineation method fleet-wide and silently substituted a coarser fallback — observed
    live: overpass-api.de refused connections and every city dropped to Voronoi cells.
    """

    def test_the_next_mirror_is_tried_when_the_first_is_unreachable(self, monkeypatch):
        seen = []
        good = nx.Graph()
        for i in range(40):
            good.add_node(i, x=-123.0, y=48.4)

        def fake(_ox, _bbox, *, use_cache):
            seen.append(_ox.settings.overpass_url)
            if len(seen) == 1:
                raise ConnectionError("Max retries exceeded")
            return good

        monkeypatch.setattr(streets_osm, "_graph_from_bbox", fake)
        got = streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))
        assert got.number_of_nodes() == 40
        assert len(seen) == 2 and seen[0] != seen[1], f"same endpoint retried: {seen}"

    def test_the_endpoint_is_left_as_we_found_it(self, monkeypatch):
        """osmnx settings are process-global; a build that reaches a mirror must not
        redirect every later build in the worker."""
        import osmnx as ox

        before = ox.settings.overpass_url
        good = nx.Graph()
        for i in range(40):
            good.add_node(i, x=-123.0, y=48.4)
        calls = []

        def fake(_ox, _bbox, *, use_cache):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("Max retries exceeded")
            return good

        monkeypatch.setattr(streets_osm, "_graph_from_bbox", fake)
        streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))
        assert ox.settings.overpass_url == before

    def test_every_mirror_failing_still_raises(self, monkeypatch):
        def fake(_ox, _bbox, *, use_cache):
            raise ConnectionError("Max retries exceeded")

        monkeypatch.setattr(streets_osm, "_graph_from_bbox", fake)
        with pytest.raises(ConnectionError):
            streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))


class TestTheRateLimitHandshakeMustNotHang:
    """osmnx asks Overpass for a slot before querying, and cannot read the current answer.

    Measured: the same downtown query takes 1.7 s with the handshake off and never returns
    with it on — `_get_overpass_pause` recurses on a status page it cannot parse. Politeness
    is not the thing being dropped here; the server itself is asked whether it has a slot,
    and its answer is believed. What is dropped is a client-side loop with no exit.
    """

    def test_a_free_slot_is_read_from_the_status_page(self):
        assert streets_osm._slots_free("Rate limit: 2\n2 slots available now.\n") is True

    def test_no_free_slot_is_read_from_the_status_page(self):
        assert streets_osm._slots_free(
            "Rate limit: 2\n0 slots available now.\nSlot available after: 2026-08-13T00:51:02Z"
        ) is False

    def test_an_unreadable_status_says_so_rather_than_guessing(self):
        assert streets_osm._slots_free("<html>502 Bad Gateway</html>") is None

    def test_a_free_slot_skips_the_handshake(self, monkeypatch):
        import osmnx as ox

        good = nx.Graph()
        for i in range(40):
            good.add_node(i, x=-123.0, y=48.4)
        seen = []
        monkeypatch.setattr(streets_osm, "_overpass_status", lambda _u: "2 slots available now.")
        monkeypatch.setattr(streets_osm, "_graph_from_bbox",
                            lambda _ox, _b, *, use_cache: (
                                seen.append(_ox.settings.overpass_rate_limit) or good))
        streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))
        assert seen == [False], "the handshake ran even though the server offered a slot"

    def test_no_free_slot_leaves_the_handshake_alone(self, monkeypatch):
        good = nx.Graph()
        for i in range(40):
            good.add_node(i, x=-123.0, y=48.4)
        seen = []
        monkeypatch.setattr(streets_osm, "_overpass_status",
                            lambda _u: "0 slots available now.\nSlot available after: x")
        monkeypatch.setattr(streets_osm, "_graph_from_bbox",
                            lambda _ox, _b, *, use_cache: (
                                seen.append(_ox.settings.overpass_rate_limit) or good))
        streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))
        assert seen == [True], "we skipped a wait the server actually asked for"

    def test_the_setting_is_restored(self, monkeypatch):
        import osmnx as ox

        before = ox.settings.overpass_rate_limit
        good = nx.Graph()
        for i in range(40):
            good.add_node(i, x=-123.0, y=48.4)
        monkeypatch.setattr(streets_osm, "_overpass_status", lambda _u: "2 slots available now.")
        monkeypatch.setattr(streets_osm, "_graph_from_bbox", lambda _ox, _b, *, use_cache: good)
        streets_osm.fetch_street_graph((-123.01, 48.39, -123.0, 48.4))
        assert ox.settings.overpass_rate_limit == before
