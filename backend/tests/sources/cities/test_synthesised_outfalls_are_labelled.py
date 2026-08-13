"""Invented outfalls must say they were invented.

Where a city publishes no outfall for a drainage component, the assembler promotes that
component's lowest node into one so the network has a destination. Victoria's sanitary
fixture gets 19 of them — every single "outfall" in that system is a modelling boundary,
not a published structure, and nothing in the model said so.

That is a larger honesty gap than a missing outfall would have been: a missing destination
fails validation loudly, while an invented one that looks published passes quietly and gets
used.
"""
import json
from pathlib import Path

import pytest

from swmmcanada.sources.cities.victoria import build_victoria_network

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "victoria"


def _load(name):
    d = json.loads((FIXTURES / f"{name}.geojson").read_text())
    return d["features"] if isinstance(d, dict) else d


@pytest.fixture(scope="module")
def sanitary():
    return build_victoria_network(
        mains=_load("sanitary_mains"), manholes=_load("sanitary_manholes"),
        fittings=_load("sanitary_fittings"), outfalls=_load("sanitary_outfalls"))


def test_the_city_published_no_sanitary_outfalls(sanitary):
    assert sanitary.diagnostics["n_direct_outfalls"] == 0


def test_the_assembler_invented_one_per_component(sanitary):
    assert sanitary.diagnostics["n_dedicated_outfalls"] == 19
    assert len(sanitary.network.outfalls) == 19


def test_every_invented_outfall_is_marked_synthetic(sanitary):
    """The marker travels on the element, not only in a diagnostics counter: a counter is
    read once at build time, the element is read by everything downstream."""
    assert all(o.synthesised for o in sanitary.network.outfalls)


def test_the_marker_is_separate_from_the_swmm_boundary_type(sanitary):
    """`kind` is FREE/NORMAL/FIXED/TIDAL — the hydraulic boundary condition SWMM applies.
    Overloading it with provenance would write an invalid .inp."""
    assert all(o.kind == "FREE" for o in sanitary.network.outfalls)


def test_a_published_outfall_is_not_marked(sanitary):
    """Victoria's storm system publishes real outfalls; they must not be tarred with the
    same label or the marker means nothing."""
    storm = build_victoria_network(
        mains=_load("mains"), manholes=_load("manholes"),
        fittings=_load("fittings"), outfalls=_load("outfalls"))
    published = [o for o in storm.network.outfalls if not o.synthesised]
    assert published, "the storm fixture publishes real outfalls"
