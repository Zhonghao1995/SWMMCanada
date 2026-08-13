"""Victoria's official storm catchments as a yardstick (#129, ADR 0029 Q2).

Not consumed as model units — Phase 0 measured the layer at 64 polygons against 7,864 catch
basins, a per-outfall basin. It is consumed as the reference our own outlet resolution is
scored against, which is the role a macro basin can actually fill.

The live test is guarded so CI never depends on a municipal server being up.
"""
import os

import pytest

from swmmcanada.sources.cities.victoria import (STORM_CATCHMENTS,
                                                fetch_victoria_official_catchments)

LIVE = os.environ.get("SWMMCANADA_LIVE_SOURCES") == "1"
live_only = pytest.mark.skipif(not LIVE, reason="set SWMMCANADA_LIVE_SOURCES=1 for live")


def test_the_layer_id_is_declared():
    assert STORM_CATCHMENTS == 12


def test_the_fetcher_accepts_an_aoi_or_a_bbox(monkeypatch):
    """Same calling convention as the other Victoria fetchers, so the pipeline does not
    special-case it."""
    seen = {}

    def fake(base, layer, bbox, client):
        seen["layer"] = layer
        seen["bbox"] = bbox
        return []

    monkeypatch.setattr("swmmcanada.sources.cities.victoria._fetch_layer_bbox", fake)
    fetch_victoria_official_catchments((-123.37, 48.42, -123.36, 48.43), client=object())
    assert seen["layer"] == STORM_CATCHMENTS


@live_only
def test_live_layer_carries_the_join_key():
    """`OUTLET` holds an outfall AssetID, which is what makes this comparable at all."""
    feats = fetch_victoria_official_catchments((-123.375, 48.415, -123.355, 48.435))
    assert feats, "downtown Victoria should intersect some official catchments"
    props = feats[0]["properties"]
    assert "OUTLET" in props and "OutfallNo" in props


@live_only
def test_live_polygons_are_macro_not_model_units():
    """Guards the Phase 0 finding against a silent municipal change: if this layer ever
    becomes per-inlet, the Level decision must be revisited, not inherited."""
    feats = fetch_victoria_official_catchments((-123.375, 48.415, -123.355, 48.435))
    assert len(feats) < 200, f"{len(feats)} polygons — no longer a macro basin layer"
