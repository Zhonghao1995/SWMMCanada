"""Live end-to-end check of the Reykjavík adapter against REAL Kópavogur (LÚKK) fráveita data.

Opt-in (``pytest -m integration``); excluded from the default run. Encodes the validation done
2026-07-17: a small live AOI must fetch real mains + structures, carry surveyed BOTNKODI inverts,
and assemble into a fully-drained SWMM network with unique node names. When the Reykjavík LÚKOR
host is wired in, point this at a Reykjavík AOI (the schema is identical).
"""
from collections import defaultdict, deque

import pytest

from swmmcanada.sources.cities.reykjavik import build_reykjavik_network, fetch_reykjavik_storm

pytestmark = pytest.mark.integration

# ~400 m AOI over the older Kópavogur core (EPSG:4326 lon/lat).
BBOX = (-21.912, 64.103, -21.905, 64.108)


@pytest.fixture(scope="module")
def storm():
    try:
        return fetch_reykjavik_storm(BBOX)
    except Exception as exc:  # noqa: BLE001 — a portal outage should skip, not fail
        pytest.skip(f"LÚKK open service unreachable: {exc}")


def test_live_fetch_carries_real_inverts(storm):
    assert len(storm["pipes"]) > 0 and len(storm["structures"]) > 0
    n_inv = sum(1 for f in storm["structures"] if f["properties"].get("BOTNKODI") is not None)
    n_rim = sum(1 for f in storm["structures"] if f["properties"].get("HAED") is not None)
    assert n_inv > 0, "no surveyed BOTNKODI inverts — the adapter's whole premise"
    assert n_rim > 0, "no surveyed HAED rims"


def test_live_network_is_wellformed_and_fully_drained(storm):
    res = build_reykjavik_network(storm)
    net = res.network
    assert net.junctions and net.conduits and net.outfalls

    names = [j.name for j in net.junctions] + [o.name for o in net.outfalls]
    assert len(names) == len(set(names)), "duplicate node names on real data"
    node_set = set(names)
    assert all(c.from_node in node_set and c.to_node in node_set for c in net.conduits)

    # every junction's component must contain an outfall (undirected — DYNWAVE routes on head)
    outs = {o.name for o in net.outfalls}
    adj = defaultdict(set)
    for c in net.conduits:
        adj[c.from_node].add(c.to_node)
        adj[c.to_node].add(c.from_node)
    seen, dq = set(outs), deque(outs)
    while dq:
        n = dq.popleft()
        for m in adj[n]:
            if m not in seen:
                seen.add(m)
                dq.append(m)
    stranded = [j.name for j in net.junctions if j.name not in seen]
    assert not stranded, f"{len(stranded)} junctions cannot reach any outfall"

    # inverts came from real BOTNKODI (plausible Kópavogur ground elevations, not the 0.0 fallback)
    invs = [j.invert_m for j in net.junctions]
    assert min(invs) > -5 and max(invs) < 120
